"""Fixed-input ablation for complete segment checks and vertex arrival.

The four development cases share two scene-seed inputs. Baseline trajectories
already stored under the execution_progress tag are not rerun. Later regression
reuses frozen people, guides and full-precision targets.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter

import numpy as np
from shapely.geometry import Polygon
from threadpoolctl import threadpool_limits

from udc.boundary import Boundary
from udc.deployment import Plan
from udc.experiments.execution_progress import replay
from udc.experiments.nonconvex_rebuild import load_boundary, load_plan
from udc.experiments.runner import experiment, reevaluate_result
from udc.navigation import navigation_domain
from udc.safety import Safety
from udc.safety.filter import binding_constraints


V2 = Path('runs/step1_nonconvex_confirmation_v2')
BASELINE = V2 / 'execution_progress'
CASES = (
    ('elongated', 30, 'pairwise_euclidean'),
    ('wide_u', 30, 'equal_arc'),
    ('wide_u', 30, 'projected_mass'),
    ('wide_u', 30, 'pairwise_euclidean'),
)
MODES = {
    'baseline': (False, False),
    'segment': (True, False),
    'vertex': (False, True),
    'both': (True, True),
}


def _length(positions):
    if len(positions) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(positions, axis=0), axis=-1).sum())


def _first_difference(previous, current):
    count = min(len(previous), len(current))
    if count == 0:
        return None
    delta = np.max(np.abs(previous[:count] - current[:count]), axis=(1, 2))
    hits = np.flatnonzero(delta > 1e-8)
    if len(hits) == 0:
        if len(previous) == len(current):
            return {'frame': None, 'time_s': None, 'position_delta_m': 0.0,
                    'guide': None, 'same_length': True}
        return {'frame': count, 'time_s': None, 'position_delta_m': 0.0,
                'guide': None, 'same_length': False,
                'previous_frames': int(len(previous)), 'current_frames': int(len(current))}
    frame = int(hits[0])
    guide_delta = np.max(np.abs(previous[frame] - current[frame]), axis=1)
    return {
        'frame': frame,
        'position_delta_m': float(delta[frame]),
        'guide': int(np.argmax(guide_delta)),
        'same_length': len(previous) == len(current),
        'previous_frames': int(len(previous)),
        'current_frames': int(len(current)),
    }


def _apply_mode(mode):
    complete, vertex = MODES[mode]
    os.environ['UDC_COMPLETE_SEGMENT'] = '1' if complete else '0'
    os.environ['UDC_VERTEX_ARRIVAL'] = '1' if vertex else '0'


def _clear_mode():
    os.environ.pop('UDC_COMPLETE_SEGMENT', None)
    os.environ.pop('UDC_VERTEX_ARRIVAL', None)


def run_case(task):
    """Execute one saved input. ``mode`` selects the ablation switches."""
    mode = task.get('mode', 'both')
    if mode == 'baseline':
        raise ValueError('baseline trajectories are reused, not rerun')
    _apply_mode(mode)
    source = Path(task['v2'])
    destination = Path(task['destination'])
    scene, seed, method = task['scene'], int(task['seed']), task['method']
    cell = source / 'inputs' / scene / f'seed_{seed}'
    plans = source / 'targets' / scene / f'seed_{seed}' / method
    with threadpool_limits(limits=1):
        started = perf_counter()
        boundary = load_boundary(cell)
        plan = load_plan(plans)
        result = experiment(
            cell / 'input.yaml', destination, plots=False, protocol='fixed_n',
            fixed_n=int(task['count']), method_label=method,
            prescribed_boundary=boundary, prescribed_plan=plan,
            boundary_source='estimated_disk_union',
        )
        offline = reevaluate_result(destination)
        elapsed = perf_counter() - started
    replayed = np.load(destination / 'trajectory.npz')
    frozen = np.load(cell / 'input.npz')
    paired = (np.array_equal(replayed['positions'][0], frozen['guides'])
              and np.array_equal(replayed['crowd_positions'], frozen['people'])
              and np.array_equal(replayed['targets'], plan.positions))
    return {
        'task_id': task['task_id'], 'mode': mode, 'status': result['termination_status'],
        'success': bool(result['success']), 'time_s': result['simulation_time'],
        'replans': result['replan_count'], 'wall_s': elapsed,
        'offline_consistent': bool(offline['criteria_consistent']),
        'exact_input_and_targets': bool(paired),
        'segment_disagreement_count': result.get('segment_disagreement_count', 0),
        'minimum_net_clearances': result.get('minimum_net_clearances'),
        'travel_m': _length(replayed['positions']),
    }


def _baseline_dir(scene, seed, method):
    return BASELINE / scene / f'seed_{seed}' / method


def _summarize_case(scene, seed, method, destination, mode):
    metrics = json.loads((destination / 'metrics.json').read_text(encoding='utf-8'))
    current = np.load(destination / 'trajectory.npz')
    baseline_dir = _baseline_dir(scene, seed, method)
    baseline_metrics = json.loads((baseline_dir / 'metrics.json').read_text(encoding='utf-8'))
    baseline = np.load(baseline_dir / 'trajectory.npz')
    difference = _first_difference(baseline['positions'], current['positions'])
    if difference and difference.get('frame') is not None and 'time_s' not in difference:
        frame = difference['frame']
        if frame < len(current['time']):
            difference['time_s'] = float(current['time'][frame])
    assignment_changed = not np.array_equal(baseline['assignment'], current['assignment'])
    target_delta = float(np.max(np.abs(baseline['targets'] - current['targets'])))
    detail = _frame_detail(destination, baseline_dir, difference, current, baseline)
    return {
        'scene': scene, 'seed': seed, 'method': method, 'mode': mode,
        'status': metrics['termination_status'],
        'baseline_status': baseline_metrics['termination_status'],
        'success': bool(metrics['success']),
        'time_s': metrics['simulation_time'],
        'baseline_time_s': baseline_metrics['simulation_time'],
        'replans': metrics['replan_count'],
        'baseline_replans': baseline_metrics['replan_count'],
        'travel_m': _length(current['positions']),
        'baseline_travel_m': _length(baseline['positions']),
        'minimum_net_clearances': metrics.get('minimum_net_clearances'),
        'baseline_minimum_net_clearances': baseline_metrics.get('minimum_net_clearances'),
        'target_delta_m': target_delta,
        'assignment_changed': bool(assignment_changed),
        'offline_consistent': bool(json.loads((destination / 'reevaluated_metrics.json').read_text(encoding='utf-8'))['criteria_consistent']),
        'segment_disagreement_count': metrics.get('segment_disagreement_count', 0),
        'segment_disagreements': metrics.get('segment_disagreements', []),
        'first_difference': difference,
        'detail': detail,
        'recovery': [
            {'classification': item.get('classification'), 'strategy': item.get('strategy'),
             'frame': item.get('frame'), 'time_s': item.get('time_s'),
             'budget_consumed': item.get('budget_consumed'),
             'budget_remaining': item.get('budget_remaining')}
            for item in metrics.get('recovery_attempts', [])
        ],
    }


def _frame_detail(destination, baseline_dir, difference, current, baseline):
    if not difference or difference.get('frame') is None:
        events = json.loads((destination / 'metrics.json').read_text(encoding='utf-8')).get('vertex_arrival_events', [])
        return {'vertex_events': events[:4], 'note': 'no coordinate divergence from the saved baseline'}
    frame = int(difference['frame'])
    guide = int(difference['guide'])
    metrics = json.loads((destination / 'metrics.json').read_text(encoding='utf-8'))
    domain = navigation_domain(current['room'], 0.55, Polygon(current['boundary_crowd']), 0.55)
    limits = Safety(**metrics['safety_limits'])
    # positions[frame] is the first state that differs. The command was computed
    # at the previous state and stored at velocity index frame-1.
    step = max(frame - 1, 0)
    step = min(step, len(current['safe_velocity']) - 1)
    people = current['crowd_positions']
    active = binding_constraints(
        current['positions'][step], current['safe_velocity'][step], people,
        current['room'], 0.1, limits, domain.conservative_obstacle)
    nominal = current['nominal_velocity'][step, guide]
    filtered = current['safe_velocity'][step, guide]
    base_nominal = baseline['nominal_velocity'][min(step, len(baseline['nominal_velocity']) - 1), guide]
    base_filtered = baseline['safe_velocity'][min(step, len(baseline['safe_velocity']) - 1), guide]
    point = current['positions'][step, guide]
    return {
        'guide': guide,
        'frame': frame,
        'position': point.tolist(),
        'waypoint_index': int(current['waypoint_index'][min(step, len(current['waypoint_index']) - 1), guide]),
        'signed_free_distance_m': float(domain.signed_free_distance(point)),
        'nominal_velocity': nominal.tolist(),
        'nominal_speed_m_s': float(np.linalg.norm(nominal)),
        'filtered_velocity': filtered.tolist(),
        'filtered_speed_m_s': float(np.linalg.norm(filtered)),
        'baseline_nominal_speed_m_s': float(np.linalg.norm(base_nominal)),
        'baseline_filtered_speed_m_s': float(np.linalg.norm(base_filtered)),
        'active_constraints': [item for item in active if item['guide'] == guide or item.get('other_guide') == guide][:8],
        'vertex_events': [
            item for item in metrics.get('vertex_arrival_events', []) if item.get('guide') == guide
        ][:3],
    }


def run_ablation(output, workers):
    output = Path(output)
    tasks = []
    for mode in ('segment', 'vertex', 'both'):
        for scene, seed, method in CASES:
            destination = output / 'ablation' / mode / scene / f'seed_{seed}' / method
            if (destination / 'metrics.json').is_file():
                continue
            cell = V2 / 'inputs' / scene / f'seed_{seed}'
            record = json.loads((cell / 'input_record.json').read_text(encoding='utf-8'))
            tasks.append({
                'mode': mode, 'scene': scene, 'seed': seed, 'method': method,
                'count': record['count'], 'v2': str(V2), 'destination': str(destination),
                'task_id': f'{mode}:{scene}:seed_{seed}:{method}',
            })
    if tasks:
        if workers == 1:
            for task in tasks:
                print(json.dumps(run_case(task)), flush=True)
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(run_case, task): task for task in tasks}
                for future in as_completed(futures):
                    print(json.dumps(future.result()), flush=True)
    rows = []
    for mode in ('segment', 'vertex', 'both'):
        for scene, seed, method in CASES:
            destination = output / 'ablation' / mode / scene / f'seed_{seed}' / method
            rows.append(_summarize_case(scene, seed, method, destination, mode))
    # Baseline rows are references, not new executions.
    for scene, seed, method in CASES:
        metrics = json.loads((_baseline_dir(scene, seed, method) / 'metrics.json').read_text(encoding='utf-8'))
        rows.append({
            'scene': scene, 'seed': seed, 'method': method, 'mode': 'baseline',
            'status': metrics['termination_status'], 'success': bool(metrics['success']),
            'time_s': metrics['simulation_time'], 'replans': metrics['replan_count'],
            'travel_m': _length(np.load(_baseline_dir(scene, seed, method) / 'trajectory.npz')['positions']),
            'reused_trajectory': str(_baseline_dir(scene, seed, method)),
            'executed_this_round': False,
        })
    payload = {'rows': rows, 'cases': [list(item) for item in CASES],
               'baseline': str(BASELINE), 'new_executions': 12}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'ablation_summary.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return payload


def _regression_task(row, output):
    _clear_mode()
    destination = Path(output) / 'regression' / row['scene'] / f"seed_{row['seed']}" / row['method']
    if (destination / 'metrics.json').is_file() and (destination / 'reevaluated_metrics.json').is_file():
        metrics = json.loads((destination / 'metrics.json').read_text(encoding='utf-8'))
        return {'task_id': row['task_id'], 'status': metrics['termination_status'], 'reused': True}
    task = {
        'mode': 'both', 'scene': row['scene'], 'seed': row['seed'], 'method': row['method'],
        'count': row['count'], 'v2': str(V2), 'destination': str(destination),
        'task_id': row['task_id'],
    }
    try:
        result = run_case(task)
        result['reused'] = False
        return result
    except Exception as error:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'runner_error.txt').write_text(repr(error), encoding='utf-8')
        return {'task_id': row['task_id'], 'status': 'EXPERIMENT_ERROR', 'error': repr(error)}


def run_regression(output, workers):
    output = Path(output)
    manifest = json.loads((V2 / 'manifest.json').read_text(encoding='utf-8'))
    accepted = [row for row in manifest['tasks'] if row['input_status'] == 'ACCEPTED']
    invalid = [row for row in manifest['tasks'] if row['input_status'] != 'ACCEPTED']
    if workers == 1:
        results = [_regression_task(row, output) for row in accepted]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_regression_task, row, str(output)): row for row in accepted}
            results = []
            for future in as_completed(futures):
                item = future.result()
                print(json.dumps({key: item.get(key) for key in ('task_id', 'status', 'reused')}), flush=True)
                results.append(item)
    saved = output / 'saved_replay'
    nine = []
    for scene in ('elongated', 'tight_u', 'wide_u'):
        for method in ('equal_arc', 'projected_mass', 'pairwise_euclidean'):
            source = Path('runs/step1_wide_u_replan_repair') / scene / method / 'run'
            destination = saved / 'nine' / scene / method
            if not (destination / 'metrics.json').is_file():
                with threadpool_limits(limits=1):
                    row = replay(source / 'config.yaml', source, destination)
            else:
                metrics = json.loads((destination / 'metrics.json').read_text(encoding='utf-8'))
                offline = reevaluate_result(destination)
                row = {'status': metrics['termination_status'], 'success': metrics['success'],
                       'time_s': metrics['simulation_time'], 'replans': metrics['replan_count'],
                       'offline_consistent': offline['criteria_consistent']}
            row['label'] = f'{scene}/{method}'
            nine.append(row)
            print(json.dumps({'saved': row['label'], 'status': row.get('status', row.get('success'))}), flush=True)
    seed22_source = Path('runs/step1_nonconvex_record_rebuild_v1/repair_regression/wide_u/seed_22/equal_arc')
    seed22_config = Path('runs/step1_nonconvex_record_rebuild_v1/inputs/wide_u/seed_22/input.yaml')
    seed22_dest = saved / 'wide_u_seed22_equal_arc'
    if not (seed22_dest / 'metrics.json').is_file():
        with threadpool_limits(limits=1):
            seed22 = replay(seed22_config, seed22_source, seed22_dest)
    else:
        metrics = json.loads((seed22_dest / 'metrics.json').read_text(encoding='utf-8'))
        offline = reevaluate_result(seed22_dest)
        seed22 = {'status': metrics['termination_status'], 'success': metrics['success'],
                  'time_s': metrics['simulation_time'], 'replans': metrics['replan_count'],
                  'offline_consistent': offline['criteria_consistent']}
    comparisons = _compare_regression(output, manifest)
    payload = {
        'accepted_executed': len(accepted),
        'invalid_retained': len(invalid),
        'invalid_status': dict(Counter(row['input_status'] for row in invalid)),
        'regression_outcomes': dict(Counter(item['status'] for item in results)),
        'nine': nine,
        'seed22': seed22,
        'comparisons': comparisons,
    }
    (output / 'regression_summary.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return payload


def _compare_regression(output, manifest):
    rows = []
    for row in manifest['tasks']:
        current_dir = Path(output) / 'regression' / row['scene'] / f"seed_{row['seed']}" / row['method']
        baseline_dir = _baseline_dir(row['scene'], row['seed'], row['method'])
        baseline = json.loads((baseline_dir / 'metrics.json').read_text(encoding='utf-8'))
        if row['input_status'] != 'ACCEPTED':
            rows.append({
                'task_id': row['task_id'], 'executed': False,
                'status': baseline['termination_status'],
                'retained_from': 'execution_progress',
            })
            continue
        metrics = json.loads((current_dir / 'metrics.json').read_text(encoding='utf-8'))
        offline = json.loads((current_dir / 'reevaluated_metrics.json').read_text(encoding='utf-8'))
        current = np.load(current_dir / 'trajectory.npz')
        previous = np.load(baseline_dir / 'trajectory.npz')
        difference = _first_difference(previous['positions'], current['positions'])
        rows.append({
            'task_id': row['task_id'], 'executed': True,
            'status': metrics['termination_status'],
            'baseline_status': baseline['termination_status'],
            'success': bool(metrics['success']),
            'baseline_success': bool(baseline['success']),
            'time_s': metrics['simulation_time'],
            'baseline_time_s': baseline['simulation_time'],
            'replans': metrics['replan_count'],
            'baseline_replans': baseline['replan_count'],
            'travel_m': _length(current['positions']),
            'baseline_travel_m': _length(previous['positions']),
            'clearance': metrics.get('minimum_net_clearances'),
            'baseline_clearance': baseline.get('minimum_net_clearances'),
            'target_delta_m': float(np.max(np.abs(current['targets'] - previous['targets']))) if len(current['targets']) else None,
            'assignment_changed': bool(not np.array_equal(current['assignment'], previous['assignment'])),
            'offline_consistent': bool(offline['criteria_consistent']),
            'segment_disagreement_count': metrics.get('segment_disagreement_count', 0),
            'first_difference': difference,
        })
    return rows


def write_figure(output, report_dir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    output = Path(output)
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(2, 2, figsize=(10.5, 8.5), constrained_layout=True)
    colors = {'baseline': '#495057', 'segment': '#1c7ed6', 'vertex': '#f08c00', 'both': '#2b8a3e'}
    for axis, (scene, seed, method) in zip(axes.ravel(), CASES):
        baseline = np.load(_baseline_dir(scene, seed, method) / 'trajectory.npz')
        crowd = baseline['boundary_crowd']
        axis.plot(np.r_[crowd[:, 0], crowd[0, 0]], np.r_[crowd[:, 1], crowd[0, 1]],
                  color='#ced4da', lw=1.0)
        guides = _guides_to_draw(output, scene, seed, method, baseline)
        for mode, path in (
            ('baseline', _baseline_dir(scene, seed, method)),
            ('segment', output / 'ablation' / 'segment' / scene / f'seed_{seed}' / method),
            ('vertex', output / 'ablation' / 'vertex' / scene / f'seed_{seed}' / method),
            ('both', output / 'ablation' / 'both' / scene / f'seed_{seed}' / method),
        ):
            data = np.load(path / 'trajectory.npz')
            for guide in guides:
                axis.plot(data['positions'][:, guide, 0], data['positions'][:, guide, 1],
                          color=colors[mode], lw=1.3 if mode == 'both' else 1.0,
                          alpha=0.9 if mode in ('baseline', 'both') else 0.55,
                          label=mode if guide == guides[0] else None)
                axis.scatter(data['positions'][-1, guide, 0], data['positions'][-1, guide, 1],
                             s=18, color=colors[mode], zorder=3)
        finite = np.concatenate([
            np.load((output / 'ablation' / 'both' / scene / f'seed_{seed}' / method) / 'trajectory.npz')['positions'][:, guides],
            baseline['positions'][:, guides],
        ])
        low, high = finite.min(axis=(0, 1)), finite.max(axis=(0, 1))
        pad = 0.8
        axis.set_xlim(low[0] - pad, high[0] + pad)
        axis.set_ylim(low[1] - pad, high[1] + pad)
        axis.set_aspect('equal')
        axis.set_title(f'{scene} seed {seed} {method}')
        axis.legend(fontsize=7, loc='best')
    figure.savefig(report_dir / 'corner_ablation.png', dpi=140)
    plt.close(figure)


def _guides_to_draw(output, scene, seed, method, baseline):
    both = np.load(output / 'ablation' / 'both' / scene / f'seed_{seed}' / method / 'trajectory.npz')
    count = min(len(baseline['positions']), len(both['positions']))
    if count == 0:
        return [0]
    delta = np.max(np.abs(baseline['positions'][:count] - both['positions'][:count]), axis=(0, 2))
    order = np.argsort(-delta)
    chosen = [int(guide) for guide in order[:2] if delta[guide] > 1e-6]
    return chosen or [int(np.argmax(delta))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runs/step1_corner_handoff_repair'))
    parser.add_argument('--report', type=Path, default=Path('reports/step1_corner_handoff_repair'))
    parser.add_argument('--ablation', action='store_true')
    parser.add_argument('--regress', action='store_true')
    parser.add_argument('--figure', action='store_true')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 2:
        raise ValueError('Use 1 or 2 episode workers')
    started = perf_counter()
    if args.ablation:
        payload = run_ablation(args.output, args.workers)
        print(json.dumps({'ablation_rows': len(payload['rows'])}), flush=True)
    if args.regress:
        payload = run_regression(args.output, args.workers)
        print(json.dumps({key: payload[key] for key in ('accepted_executed', 'invalid_retained', 'regression_outcomes')}), flush=True)
    if args.figure or args.ablation:
        write_figure(args.output, args.report)
    print(json.dumps({'wall_s': perf_counter() - started}), flush=True)


if __name__ == '__main__':
    main()
