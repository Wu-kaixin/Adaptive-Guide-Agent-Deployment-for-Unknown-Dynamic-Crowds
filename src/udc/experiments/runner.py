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

from udc.boundary import Boundary
from udc.controller import ABCGController
from udc.controller.progress import stalled_this_step
from udc.safety import Safety, is_safe, path_clearances
from udc.environment import Environment
from udc.navigation.paths import target_domain_check
from udc.evaluation.acceptance import diagnose_stages, evaluate
from .accounting import unabsorbed_program_error
from .config import demand_kind, load_config, METHODS, PROTOCOLS
from .provenance import provenance as _provenance


STATUSES = {
    'INITIALIZATION_INVALID', 'BOUNDARY_INVALID', 'OFFSET_INVALID',
    'CAPACITY_SHORTFALL', 'PLAN_INVALID', 'PLAN_SEARCH_EXHAUSTED',
    'TARGET_SEPARATION_INVALID', 'TARGET_DOMAIN_INVALID', 'PATH_UNREACHABLE', 'NUMERICAL_FAILURE',
    'DEADLOCK', 'REPLAN_EXHAUSTED', 'TIMEOUT', 'COVERAGE_NOT_MET',
    'SAFETY_VIOLATION', 'SUCCESS', 'NOT_EXECUTED',
}
RESULT_FILES = {'metrics.json', 'config.yaml', 'trajectory.npz', 'planning.json', 'state.json'}


def _priority_stall_counterfactual():
    """Continue an already active priority yield after its stall would stop the run.

    This is a causal diagnostic. It does not add a recovery, reset the budget,
    or change any velocity computed before that stall. Official runs leave it unset.
    """
    value = os.environ.get('UDC_PRIORITY_STALL_COUNTERFACTUAL')
    if value is None or value == '0':
        return False
    if value == '1':
        return True
    raise ValueError('UDC_PRIORITY_STALL_COUNTERFACTUAL must be unset, 0, or 1')




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
        if unabsorbed_program_error(output):
            return None
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
        if unabsorbed_program_error(output):
            return None
        return metrics
    return None


def _saved_state(output):
    try:
        return json.loads((output / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def experiment(config_path, output, seed=None, plots=True,
               protocol='adaptive_resource', fixed_n=None, resume=False,
               method=None, execute=True, prescribed_boundary=None,
               prescribed_plan=None, method_label=None,
               boundary_source='prescribed_diagnostic', initial_guides=None,
               restart_interrupted=False):
    """Run one task and always emit a structured result for expected failures.

    ``execute=False`` stops after a feasible plan is saved (``NOT_EXECUTED``).
    Resuming with ``execute=True`` replans deterministically and then tracks.
    A completed planning failure is not run again.

    The default estimates the convex offset from the observation.
    ``prescribed_boundary`` is used only when the caller supplies a curve.
    ``method_label`` records which supplied plan was installed. It does not
    select a different navigation or safety stack.
    ``initial_guides`` replaces the inset-perimeter start when a caller has
    already drawn one shared guide state. The default keeps that placement.
    ``restart_interrupted`` resumes a started or interrupted run when the
    source, config, and input snapshot hashes still match.
    """
    started = perf_counter()
    output = Path(output)
    if protocol not in PROTOCOLS:
        raise ValueError(f'Unsupported protocol: {protocol}')
    if output.exists() and any(output.iterdir()) and not resume:
        raise FileExistsError(f'Use an empty output directory or --resume: {output}')
    output.mkdir(parents=True, exist_ok=True)

    config = env = observation = perception = initial = safety = controller = None
    provenance = {}
    positions = []
    nominal = []
    executed = []
    waypoint_history = []
    safety_log = []
    deadlocks = []
    priority_stall_suppressed = []
    priority_stall_counterfactual = _priority_stall_counterfactual()
    termination = 'INITIALIZATION_INVALID'
    failure_reason = ''
    failure_stage = 'initialization'
    failure_diagnosis = None
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
        if initial_guides is None:
            initial = env.initialize_guides(config['guides'])
        else:
            initial = np.asarray(initial_guides, dtype=float).copy()
            expected = (int(config['guides']['count']), 2)
            if initial.shape != expected or not np.isfinite(initial).all():
                raise ValueError('INITIALIZATION_INVALID: supplied guide state has the wrong shape')
        perception = env.perceive(0.0, initial)
        observation = perception.observation()
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
            state = _saved_state(output)
            same_plan = (state.get('status') == 'planned'
                         and state.get('task_sha256') == provenance['task_sha256'])
            same_interruption=(restart_interrupted and state.get('status') in ('started','interrupted')
                               and state.get('source_sha256')==provenance['source_sha256']
                               and state.get('config_sha256')==provenance['config_sha256']
                               and state.get('input_snapshot_sha256')==provenance['input_snapshot_sha256']
                               and not (output/'metrics.json').exists())
            if same_plan and not execute:
                return json.loads((output / 'metrics.json').read_text(encoding='utf-8'))
            if not (execute and (same_plan or same_interruption)):
                raise FileExistsError(f'Existing result is incomplete or hash-mismatched: {output}')
            if same_interruption:
                _atomic_text(output/'resumed_interruption.json',json.dumps(state,indent=2)+'\n')
        _atomic_text(output / 'state.json', json.dumps({
            'status': 'started', 'task_sha256': provenance['task_sha256'],
            **{key:provenance[key] for key in ('source_sha256','config_sha256','input_snapshot_sha256')}
        }, indent=2) + '\n')
        if not is_safe(path_clearances(initial, initial, observation.positions,
                                       env.scene.size), safety):
            raise ValueError('INITIALIZATION_INVALID: unsafe guide start')

        failure_stage = 'planning'
        controller = ABCGController(control, safety, env.scene.size, protocol, fixed_n)
        if prescribed_boundary is None:
            controller.prepare(observation, initial)
        else:
            controller.prepare_prescribed(
                observation, initial, prescribed_boundary, prescribed_plan,
                boundary_source=boundary_source,
            )
        if not execute:
            termination = 'NOT_EXECUTED'
            failure_stage = None
            failure_reason = 'Feasible plan saved; execution was not requested'
        else:
            failure_stage = 'execution'
            termination = 'TIMEOUT'
        progress = [float(controller.remaining_path_lengths(initial).mean())]
        for step in range(0 if execute else sim['max_steps'], sim['max_steps']):
            perceived = env.perceive((step + 1) * sim['dt'], positions[-1])
            requested, result = controller.step(perceived.observation(), positions[-1], sim['dt'])
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
            max_error = float(errors.max(initial=0.0))
            # Priority yield watches that guide's polyline. The mean path and
            # the mean speed stay in force only after the mode has exited.
            priority_stalled = controller.observe_priority_progress(
                next_position, max_error, window, float(control['position_tolerance']))
            mean_speed = float(active_speed.mean()) if len(active_speed) else float('nan')
            stalled = stalled_this_step(
                controller.priority_mode, priority_stalled, progress, window,
                float(control['position_tolerance']), max_error, mean_speed,
                float(control['speed_tolerance']))
            # The diagnostic keeps the third strategy past this branch. It still
            # records the original mean-path and mean-speed inputs, and it does
            # not open a fourth recovery or clear the stall samples.
            continue_priority = (
                stalled and priority_stall_counterfactual and controller.priority_mode
                and controller.replans >= control['max_replans']
            )
            if continue_priority:
                guide = (int(controller.priority_order[controller.priority_index])
                         if controller.priority_index < len(controller.priority_order) else None)
                priority_stall_suppressed.append({
                    'step': int(step),
                    'time_s': float((step + 1) * sim['dt']),
                    'mean_remaining_m': float(progress[-1]),
                    'window_decrease_m': float(progress[-window - 1] - progress[-1]),
                    'window_steps': int(window),
                    'mean_active_speed_m_s': float(active_speed.mean()),
                    'max_target_error_m': float(errors.max(initial=0.0)),
                    'position_tolerance_m': float(control['position_tolerance']),
                    'speed_tolerance_m_s': float(control['speed_tolerance']),
                    'priority_guide': guide,
                    'priority_index': int(controller.priority_index),
                    'priority_remaining_m': (None if guide is None else float(
                        controller.guide_remaining_path(guide, next_position))),
                    'priority_filtered_speed_m_s': (None if guide is None else float(
                        np.linalg.norm(result.velocity[guide]))),
                    'replans': int(controller.replans),
                    'action': 'continue_priority_yield_without_fourth_recovery',
                })
            elif stalled:
                attempts_before = len(controller.recovery_attempts)
                try:
                    strategy = controller.recover(
                        env.perceive((step + 1) * sim['dt'], next_position).observation(),
                        next_position,
                        trigger_reason='stall_window',
                    )
                finally:
                    for attempt in controller.recovery_attempts[attempts_before:]:
                        attempt.update(frame=step + 1, time_s=(step + 1) * sim['dt'])
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
        failure_diagnosis = getattr(error,'diagnosis',None)
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
            _demand=observation.demand, _hotspot_mask=env.hotspot_mask,
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
    method_name = method_label or (config['controller']['method'] if config is not None else method)
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
    feasibility = controller.feasibility if controller is not None else None
    plan_found = bool(controller is not None and controller.plan_found)
    assignment_found = bool(controller is not None and controller.assignment_found)
    metrics = {
        'schema_version': '1.2.0', **(provenance_fields | provenance),
        'scene': scene_name, 'method': method_name, 'protocol': protocol,
        'fixed_n': fixed_n, 'seed': seed_value,
        'termination_status': termination, 'motion_status': motion_status,
        'failure_reason': failure_reason, 'failure_stage': failure_stage,
        'failure_diagnosis':failure_diagnosis,
        'success': success, 'criteria': criteria,
        # Stage diagnostics (schema 1.2.0). Statuses and thresholds are unchanged.
        'stage_diagnosis': diagnose_stages(termination, failure_stage, feasibility,
                                           plan_found, assignment_found),
        'feasibility': feasibility,
        'plan_found': plan_found,
        'assignment_found': assignment_found,
        'target_domain': (None if plan is None else target_domain_check(
                controller._domain(), plan.positions, plan.arc_positions)),
        'demand_kind': None if config is None else demand_kind(config['crowd']),
        'boundary_source': None if controller is None else controller.boundary_source,
        'prescribed_plan_installed': (False if controller is None
                                      else bool(controller.prescribed_plan_installed)),
        'demand_region': None if config is None else config['crowd'].get('demand_region'),
        'demand_clipped_count': None if env is None else env.demand_clipped,
        'hotspot_people': None if env is None else int(env.hotspot_mask.sum()),
        'planned_concentration': (None if plan is None or plan.method not in {'ra_abdc', 'projected_mass'}
                                  else plan.concentration),
        'equal_arc_fallback': (None if plan is None or plan.method not in {'ra_abdc', 'projected_mass'}
                               else bool(plan.equal_arc_fallback)),
        'adapt_concentration': (None if plan is None or plan.method not in {'ra_abdc', 'projected_mass'}
                                else bool(plan.adapt_concentration)),
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
        'path_length': (None if controller is None or getattr(controller, 'navigation', None) is None
                        else float(np.sum(controller.navigation.lengths))),
        'resource_utilization': (None if guide_count == 0
                                 else float(active.sum()) / guide_count),
        'safety_limits': None if safety is None else asdict(safety),
        'safety_diagnostics': safety_log,
        'fallback_count': sum(item['fallback_used'] for item in safety_log),
        'replan_count': 0 if controller is None else controller.replans,
        'recovery_attempts': [] if controller is None else controller.recovery_attempts,
        'coordination': None if controller is None else getattr(controller, 'coordination', None),
        'segment_disagreement_count': 0 if controller is None else int(getattr(controller, 'segment_disagreement_count', 0)),
        'segment_disagreements': [] if controller is None else list(getattr(controller, 'segment_audits', [])),
        'vertex_arrival_events': [] if controller is None else list(getattr(controller, 'vertex_arrivals', [])),
        'initial_navigation_wall_seconds':(None if controller is None else getattr(controller,'initial_navigation_wall_seconds',None)),
        'planning_margin_connections': ([] if controller is None
                                        else list(getattr(controller, 'margin_connections', []))),
        'deadlock_events': deadlocks,
        'diagnostic_priority_stall_counterfactual': priority_stall_counterfactual,
        'priority_stall_suppressed_events': priority_stall_suppressed,
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
        crowd_ids=(perception.pedestrians.ids if perception is not None
                   else np.empty(0, dtype=int)),
        crowd_positions=(observation.positions if observation is not None
                         else np.empty((0, 2))),
        crowd_radii=(observation.radii if observation is not None else np.empty(0)),
        crowd_demand=(observation.demand if observation is not None else np.empty(0)),
        crowd_hotspot_mask=(env.hotspot_mask if env is not None else np.empty(0, dtype=bool)),
        crowd_velocities=(perception.pedestrians.velocities if perception is not None
                          else np.empty((0, 2))),
        boundary_crowd=(boundary.crowd if boundary is not None else np.empty((0, 2))),
        boundary_deployment=(boundary.deployment if boundary is not None else np.empty((0, 2))),
        boundary_arc=(boundary.arc if boundary is not None else np.empty(0)),
        boundary_length=np.array(np.nan if boundary is None else boundary.length),
        room=(env.scene.size if env is not None else np.empty(0)),
    )
    _atomic_text(output / 'state.json', json.dumps({
        'status': 'planned' if termination == 'NOT_EXECUTED' else 'completed',
        'success': success,
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
        # Older archives (schema < 1.2.0) have no hotspot mask; diagnostics then skip it.
        mask = data['crowd_hotspot_mask'] if 'crowd_hotspot_mask' in data.files else None
        criteria, diagnostics = evaluate(
            data['positions'], data['safe_velocity'], data['active_mask'],
            data['targets'], data['assignment'], boundary, limits,
            dict(config['controller'], _people=data['crowd_positions'],
                 _radii=data['crowd_radii'], _room=data['room'],
                 _demand=data['crowd_demand'],
                 _hotspot_mask=None if mask is None or not len(mask) else mask),
        )
    compared = {}
    for key in ['actual_max_gap', 'mean_curve_distance', 'tracking_rmse',
                'actual_demand_weighted_cost', 'actual_hotspot_concentration',
                'planned_demand_weighted_cost']:
        online, offline = metrics.get(key), diagnostics.get(key)
        compared[key] = (online == offline if online is None or offline is None
                         else bool(abs(float(online)-float(offline)) <= 1e-9))
    result = {
        'schema_version': metrics['schema_version'],
        'source_task_sha256': metrics.get('task_sha256'),
        'criteria': criteria, 'diagnostics': diagnostics,
        'success': all(criteria.values()),
        'criteria_consistent': criteria == metrics.get('criteria'),
        'diagnostics_consistent': compared,
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
