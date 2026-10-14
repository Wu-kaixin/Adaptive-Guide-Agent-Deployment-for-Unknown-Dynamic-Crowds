"""Run reproducible Step-1 experiments and independent seed batches."""
import argparse
import json
import os
import platform
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import numpy as np
import yaml

from udc.controller import ABCGController
from udc.controller.boundary import Boundary
from udc.controller.safety import Safety, is_safe, path_clearances
from udc.environment import Environment
from udc.evaluation.acceptance import evaluate
from .config import load_config, METHODS, PROTOCOLS
from .provenance import provenance as _provenance


STATUSES = {
    'INITIALIZATION_INVALID', 'BOUNDARY_INVALID', 'OFFSET_INVALID',
    'CAPACITY_SHORTFALL', 'PLAN_INVALID', 'PLAN_SEARCH_EXHAUSTED',
    'TARGET_SEPARATION_INVALID', 'PATH_UNREACHABLE', 'NUMERICAL_FAILURE',
    'DEADLOCK', 'REPLAN_EXHAUSTED', 'TIMEOUT', 'COVERAGE_NOT_MET',
    'SAFETY_VIOLATION', 'SUCCESS',
}
RESULT_FILES = {'metrics.json', 'config.yaml', 'trajectory.npz', 'planning.json', 'state.json'}




def _atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n',
                                     dir=path.parent, delete=False) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _atomic_npz(path, **arrays):
    path = Path(path)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False) as stream:
        np.savez_compressed(stream, **arrays)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _status_from_error(error, stage):
    prefix = str(error).split(':', 1)[0]
    if prefix in STATUSES:
        return prefix
    if stage == 'initialization':
        return 'INITIALIZATION_INVALID'
    raise error


def _empty_criteria():
    return {name: False for name in [
        'geometry_ok', 'resource_ok', 'plan_ok', 'path_ok', 'safety_ok',
        'arrival_ok', 'curve_ok', 'gap_ok', 'coverage_ok',
    ]}


def _resume_result(output, task_hash):
    if not RESULT_FILES.issubset({path.name for path in output.iterdir()}):
        return None
    try:
        state = json.loads((output / 'state.json').read_text(encoding='utf-8'))
        metrics = json.loads((output / 'metrics.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if state.get('status') == 'completed' and metrics.get('task_sha256') == task_hash:
        return metrics
    return None


def _resume_initialization_failure(output, request_hash):
    if not RESULT_FILES.issubset({path.name for path in output.iterdir()}):
        return None
    try:
        state = json.loads((output / 'state.json').read_text(encoding='utf-8'))
        metrics = json.loads((output / 'metrics.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (state.get('status') == 'completed'
            and metrics.get('failure_stage') == 'initialization'
            and metrics.get('task_request_sha256') == request_hash):
        return metrics
    return None


def experiment(config_path, output, seed=None, plots=True,
               protocol='adaptive_resource', fixed_n=None, resume=False,
               method=None):
    """Run one task and always emit a structured result for expected failures."""
    started = perf_counter()
    output = Path(output)
    if protocol not in PROTOCOLS:
        raise ValueError(f'Unsupported protocol: {protocol}')
    if output.exists() and any(output.iterdir()) and not resume:
        raise FileExistsError(f'Use an empty output directory or --resume: {output}')
    output.mkdir(parents=True, exist_ok=True)

    config = env = observation = initial = safety = controller = None
    provenance = {}
    positions = []
    nominal = []
    executed = []
    waypoint_history = []
    safety_log = []
    deadlocks = []
    termination = 'INITIALIZATION_INVALID'
    failure_reason = ''
    failure_stage = 'initialization'
    hold = 0

    try:
        config = load_config(config_path, seed)
        if method is not None:
            if method not in METHODS:
                raise ValueError(f'Invalid method override: {method}')
            config['controller']['method'] = method
        provenance = _provenance(config, None, protocol, fixed_n)
        if resume and any(output.iterdir()):
            result = _resume_initialization_failure(
                output, provenance['task_request_sha256']
            )
            if result is not None:
                return result
        env = Environment(config)
        observation = env.observe()
        initial = env.initialize_guides(config['guides'])
        positions = [initial]
        guide = config['guides']; control = config['controller']; sim = config['simulation']
        safety = Safety(
            2 * guide['radius'] + control['clearance'],
            guide['radius'] + float(observation.radii.max()) + control['clearance'],
            guide['radius'] + control['clearance'], guide['max_speed'],
            control['safety_numeric_tolerance'],
        )
        provenance = _provenance(config, observation, protocol, fixed_n)
        if resume and any(output.iterdir()):
            result = _resume_result(output, provenance['task_sha256'])
            if result is not None:
                return result
            raise FileExistsError(f'Existing result is incomplete or hash-mismatched: {output}')
        _atomic_text(output / 'state.json', json.dumps({
            'status': 'started', 'task_sha256': provenance['task_sha256']
        }, indent=2) + '\n')
        if not is_safe(path_clearances(initial, initial, observation.positions,
                                       env.scene.size), safety):
            raise ValueError('INITIALIZATION_INVALID: unsafe guide start')

        failure_stage = 'planning'
        controller = ABCGController(control, safety, env.scene.size, protocol, fixed_n)
        controller.prepare(observation, initial)
        failure_stage = 'execution'
        termination = 'TIMEOUT'
        progress = [float(controller.remaining_path_lengths(initial).mean())]
        for step in range(sim['max_steps']):
            requested, result = controller.step(env.observe(), positions[-1], sim['dt'])
            next_position = positions[-1] + sim['dt'] * result.velocity
            clearances = path_clearances(
                positions[-1], next_position, observation.positions, env.scene.size
            )
            if not is_safe(clearances, safety):
                raise ValueError('SAFETY_VIOLATION: candidate segment rejected before execution')
            nominal.append(requested)
            executed.append(result.velocity)
            positions.append(next_position)
            waypoint_history.append(np.array([
                controller.waypoint.get(i, -1) for i in range(len(initial))
            ], dtype=int))
            env.advance(sim['dt'])
            safety_log.append({
                key: (value.item() if isinstance(value, np.generic) else value)
                for key, value in asdict(result).items() if key != 'velocity'
            })
            errors = controller.errors(next_position)
            progress.append(float(controller.remaining_path_lengths(next_position).mean()))
            settled = (np.all(errors <= control['position_tolerance'])
                       and np.max(np.linalg.norm(result.velocity, axis=1))
                       <= control['speed_tolerance'])
            hold = hold + 1 if settled else 0
            if hold >= control['hold_steps']:
                termination = 'COVERAGE_NOT_MET'
                break
            window = control['stall_window']
            active_speed = np.linalg.norm(result.velocity[controller.assignment >= 0], axis=1)
            stalled = (len(progress) >= window + 1
                       and progress[-window - 1] - progress[-1] < control['position_tolerance']
                       and errors.max(initial=0.) > control['position_tolerance']
                       and active_speed.mean() <= control['speed_tolerance'])
            if stalled:
                strategy = controller.recover(env.observe(), next_position)
                event = {'step': step, 'remaining_path': progress[-1],
                         'strategy': strategy or 'exhausted'}
                deadlocks.append(event)
                if strategy is None:
                    termination = 'REPLAN_EXHAUSTED'
                    failure_reason = 'Progress stalled and the finite recovery budget was exhausted'
                    break
                progress = [float(controller.remaining_path_lengths(next_position).mean())]
    except ValueError as error:
        failure_reason = str(error)
        termination = _status_from_error(error, failure_stage)

    trajectory = np.asarray(positions) if positions else np.empty((0, 0, 2))
    guide_count = len(initial) if initial is not None else 0
    velocities = (np.asarray(executed).reshape(-1, guide_count, 2)
                  if guide_count else np.empty((0, 0, 2)))
    requested = (np.asarray(nominal).reshape(-1, guide_count, 2)
                 if guide_count else np.empty((0, 0, 2)))
    active = (controller.assignment >= 0 if controller is not None
              and controller.assignment is not None else np.zeros(guide_count, bool))
    assignment = (controller.assignment if controller is not None
                  and controller.assignment is not None else np.full(guide_count, -1))
    targets = (controller.plan.positions if controller is not None
               and controller.plan is not None else np.empty((0, 2)))
    criteria = _empty_criteria()
    diagnostics = {}
    if observation is not None and safety is not None and len(trajectory):
        evaluation_config = dict(
            config['controller'], _people=observation.positions,
            _radii=observation.radii, _room=env.scene.size,
        )
        criteria, diagnostics = evaluate(
            trajectory, velocities, active, targets, assignment,
            None if controller is None else controller.boundary,
            safety, evaluation_config,
        )
    success = all(criteria.values())
    if success:
        termination = 'SUCCESS'; failure_reason = ''; failure_stage = None
    elif termination == 'COVERAGE_NOT_MET':
        failure_reason = ('Terminal motion condition reached, but independent '
                          'acceptance criteria were not all satisfied')
    motion_status = ('CONVERGED' if hold >= (config or {}).get('controller', {}).get('hold_steps', 1)
                     else 'TRACKING' if termination == 'TIMEOUT' else 'STOPPED')
    scene_name = env.scene.name if env is not None else None
    method_name = config['controller']['method'] if config is not None else method
    seed_value = config['simulation']['seed'] if config is not None else seed
    attempts = controller.attempts if controller is not None else []
    boundary = controller.boundary if controller is not None else None
    plan = controller.plan if controller is not None else None
    provenance_fields = {
        'commit': None, 'dirty': None, 'patch_sha256': None,
        'source_sha256': None, 'input_snapshot_sha256': None,
        'config_sha256': None, 'task_request_sha256': None,
        'task_sha256': None, 'python': platform.python_version(),
        'platform': platform.platform(), 'dependencies': None,
    }
    metrics = {
        'schema_version': '1.1.0', **(provenance_fields | provenance),
        'scene': scene_name, 'method': method_name, 'protocol': protocol,
        'fixed_n': fixed_n, 'seed': seed_value,
        'termination_status': termination, 'motion_status': motion_status,
        'failure_reason': failure_reason, 'failure_stage': failure_stage,
        'success': success, 'criteria': criteria,
        'active_guides': int(active.sum()),
        'reserve_guides': int(guide_count - active.sum()),
        'steps': len(executed),
        'simulation_time': (len(executed) * config['simulation']['dt']
                            if config is not None else 0.0),
        'wall_time': perf_counter() - started,
        'curve_length': None if boundary is None else boundary.length,
        'planned_max_gap': None if plan is None else plan.max_gap,
        'max_gap_limit': (None if config is None
                          else config['controller']['max_gap_limit']),
        'target_min_euclidean_distance': (
            None if plan is None else float(np.min(np.linalg.norm(
                plan.positions[:, None] - plan.positions[None, :]
                + np.eye(len(plan.positions))[:, :, None] * 1e9, axis=2
            )))
        ),
        **diagnostics,
        'planning_attempts': attempts,
        'total_demand': (None if observation is None
                         else float(observation.demand.sum())),
        'safety_limits': None if safety is None else asdict(safety),
        'safety_diagnostics': safety_log,
        'fallback_count': sum(item['fallback_used'] for item in safety_log),
        'replan_count': 0 if controller is None else controller.replans,
        'deadlock_events': deadlocks,
        'crowd_static': (False if env is None or observation is None else bool(
            np.array_equal(observation.positions, env.observe().positions)
        )),
    }
    _atomic_text(output / 'metrics.json', json.dumps(metrics, indent=2,
                                                     allow_nan=False) + '\n')
    _atomic_text(output / 'config.yaml', yaml.safe_dump(config, sort_keys=False)
                 if config is not None else 'null\n')
    _atomic_text(output / 'planning.json', json.dumps(attempts, indent=2,
                                                      allow_nan=False) + '\n')
    _atomic_npz(
        output / 'trajectory.npz',
        time=(np.arange(len(trajectory)) * config['simulation']['dt']
              if config is not None else np.empty(0)),
        positions=trajectory, nominal_velocity=requested, safe_velocity=velocities,
        active_mask=active, targets=targets, assignment=assignment,
        waypoint_index=(np.asarray(waypoint_history).reshape(-1, guide_count)
                        if guide_count else np.empty((0, 0), int)),
        crowd_positions=(observation.positions if observation is not None
                         else np.empty((0, 2))),
        crowd_radii=(observation.radii if observation is not None else np.empty(0)),
        crowd_demand=(observation.demand if observation is not None else np.empty(0)),
        boundary_crowd=(boundary.crowd if boundary is not None else np.empty((0, 2))),
        boundary_deployment=(boundary.deployment if boundary is not None else np.empty((0, 2))),
        boundary_arc=(boundary.arc if boundary is not None else np.empty(0)),
        boundary_length=np.array(np.nan if boundary is None else boundary.length),
        room=(env.scene.size if env is not None else np.empty(0)),
    )
    _atomic_text(output / 'state.json', json.dumps({
        'status': 'completed', 'success': success,
        'task_sha256': provenance.get('task_sha256'),
    }, indent=2) + '\n')
    if plots and env is not None and controller is not None and len(trajectory):
        from udc.visualization.plots import plot_result
        plot_result(env, trajectory, controller, metrics, output / 'scene.png')
    return metrics


def reevaluate_result(result_dir):
    """Recompute criteria only from persisted config and trajectory evidence."""
    result_dir = Path(result_dir)
    data = np.load(result_dir / 'trajectory.npz', allow_pickle=False)
    metrics = json.loads((result_dir / 'metrics.json').read_text(encoding='utf-8'))
    if not len(data['boundary_deployment']):
        criteria, diagnostics = _empty_criteria(), {}
    else:
        config = load_config(result_dir / 'config.yaml')
        boundary = Boundary(data['boundary_crowd'], data['boundary_deployment'],
                            data['boundary_arc'], float(data['boundary_length']))
        limits = Safety(**metrics['safety_limits'])
        criteria, diagnostics = evaluate(
            data['positions'], data['safe_velocity'], data['active_mask'],
            data['targets'], data['assignment'], boundary, limits,
            dict(config['controller'], _people=data['crowd_positions'],
                 _radii=data['crowd_radii'], _room=data['room']),
        )
    result = {
        'schema_version': metrics['schema_version'],
        'source_task_sha256': metrics.get('task_sha256'),
        'criteria': criteria, 'diagnostics': diagnostics,
        'success': all(criteria.values()),
    }
    _atomic_text(result_dir / 'reevaluated_metrics.json', json.dumps(
        result, indent=2, allow_nan=False
    ) + '\n')
    return result


def _worker(arguments):
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        return experiment(*arguments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    parser.add_argument('--output')
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--seed', type=int)
    choice.add_argument('--seeds', type=int, nargs='+')
    parser.add_argument('--workers', default='auto')
    parser.add_argument('--no-plots', action='store_true')
    parser.add_argument('--protocol', choices=sorted(PROTOCOLS),
                        default='adaptive_resource')
    parser.add_argument('--fixed-n', type=int)
    parser.add_argument('--method', choices=sorted(METHODS))
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--reevaluate')
    parser.add_argument('--verbose', action='store_true', help='Print full metrics; all metrics are always saved')
    args = parser.parse_args()
    if args.reevaluate:
        print(json.dumps(reevaluate_result(args.reevaluate), indent=2)); return
    if not args.config or not args.output:
        parser.error('--config and --output are required')
    if args.protocol == 'fixed_n' and args.fixed_n is None:
        parser.error('--fixed-n is required for fixed_n protocol')
    if args.seeds is None:
        result = experiment(args.config, args.output, args.seed, not args.no_plots,
                            args.protocol, args.fixed_n, args.resume, args.method)
        shown = result if args.verbose else {
            key: result.get(key) for key in ['termination_status', 'success', 'method',
                'protocol', 'active_guides', 'simulation_time', 'failure_reason', 'criteria']
        }
        print(json.dumps(shown, indent=2)); return
    if min(args.seeds) < 0 or len(args.seeds) != len(set(args.seeds)):
        parser.error('Seeds must be distinct nonnegative integers')
    available = (len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity')
                 else os.cpu_count() or 1)
    workers = min(available if args.workers == 'auto' else int(args.workers),
                  len(args.seeds))
    if workers < 1:
        parser.error('workers must be positive')
    output = Path(args.output); output.mkdir(parents=True, exist_ok=True)
    tasks = [
        (args.config, output / f'seed_{seed}', seed, not args.no_plots,
         args.protocol, args.fixed_n, args.resume, args.method)
        for seed in args.seeds
    ]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_worker, tasks))
    summary = {
        'runs': len(results), 'successful': sum(item['success'] for item in results),
        'workers': workers,
        'failure_reasons': {
            status: sum(item['termination_status'] == status for item in results)
            for status in sorted(STATUSES)
        },
    }
    _atomic_text(output / 'summary.json', json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
