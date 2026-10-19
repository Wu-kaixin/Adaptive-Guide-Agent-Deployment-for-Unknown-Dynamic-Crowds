"""Diagnose the priority-yield stall on the saved wide-U executions.

The counterfactual keeps every motion decision of the current controller and,
once the third recovery is already active, does not stop at the mean-path
stall. It does not add a recovery, replan targets, or reset the clock. Its
success is not an official result.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import perf_counter

import numpy as np
from threadpoolctl import threadpool_limits

from udc.experiments.execution_progress import replay


NINE_SOURCE = Path('runs/step1_wide_u_replan_repair')
SAVED = Path('runs/step1_joint_progress_diagnostics/full/saved_replay/nine')
WIDE = (
    ('wide_u', 'equal_arc'),
    ('wide_u', 'projected_mass'),
    ('wide_u', 'pairwise_euclidean'),
)
TOLERANCE_M = 0.03
WINDOW = 20


def _remaining(position, path, index):
    index = int(index)
    value = float(np.linalg.norm(path[index] - position))
    if index < len(path) - 1:
        value += float(np.linalg.norm(np.diff(path[index:], axis=0), axis=1).sum())
    return value


def _priority_event(metrics):
    return next(
        item for item in metrics['recovery_attempts']
        if item.get('strategy') == 'priority_yield' and item.get('accepted')
    )


def _load_case(result_dir):
    result_dir = Path(result_dir)
    metrics = json.loads((result_dir / 'metrics.json').read_text(encoding='utf-8'))
    data = np.load(result_dir / 'trajectory.npz')
    event = _priority_event(metrics)
    return metrics, data, event


def classify_saved(result_dir):
    """Separate a mean-path stall from a stall of the priority guide itself."""
    metrics, data, event = _load_case(result_dir)
    paths = {int(key): np.asarray(value, float) for key, value in event['paths'].items()}
    order = [int(guide) for guide in event['priority_order']]
    frame = int(event['frame'])
    positions = data['positions']
    nominal = data['nominal_velocity']
    safe = data['safe_velocity']
    waypoints = data['waypoint_index']
    targets = data['targets']
    assignment = np.asarray(data['assignment'])
    index = 0
    handoffs = []
    omissions = []
    seen = set()
    priority_drop = 0.0
    mean_drop = 0.0
    samples = []
    for step in range(frame, len(nominal)):
        while index < len(order):
            guide = order[index]
            distance = float(np.linalg.norm(
                targets[int(assignment[guide])] - positions[step, guide]))
            if distance <= TOLERANCE_M:
                handoffs.append({'step': step, 'guide': guide, 'distance_m': distance})
                index += 1
            else:
                break
        if index >= len(order):
            break
        guide = order[index]
        before = _remaining(positions[step, guide], paths[guide], waypoints[step - 1, guide])
        retargeted = _remaining(positions[step, guide], paths[guide], waypoints[step, guide])
        after = _remaining(positions[step + 1, guide], paths[guide], waypoints[step, guide])
        motion = retargeted - after
        priority_drop += motion
        active = np.flatnonzero(assignment >= 0)
        mean_before = float(np.mean([
            _remaining(positions[step, int(item)], paths[int(item)], waypoints[step - 1, int(item)])
            for item in active
        ]))
        mean_after = float(np.mean([
            _remaining(positions[step + 1, int(item)], paths[int(item)], waypoints[step, int(item)])
            for item in active
        ]))
        mean_drop += mean_before - mean_after
        samples.append({
            'step': step,
            'time_s': float((step + 1) * 0.1),
            'guide': guide,
            'motion_m': motion,
            'jump_m': before - retargeted,
            'remaining_m': after,
            'nominal_m_s': float(np.linalg.norm(nominal[step, guide])),
            'filtered_m_s': float(np.linalg.norm(safe[step, guide])),
            'displacement_m': float(np.linalg.norm(positions[step + 1, guide] - positions[step, guide])),
            'mean_decrease_m': mean_before - mean_after,
        })
        for passed in order[:index]:
            distance = float(np.linalg.norm(
                targets[int(assignment[passed])] - positions[step + 1, passed]))
            if distance > TOLERANCE_M and passed not in seen:
                seen.add(passed)
                omissions.append({'step': step, 'guide': passed, 'distance_m': distance})
    window = samples[-WINDOW:]
    priority_window = float(sum(item['motion_m'] for item in window))
    mean_window = float(sum(item['mean_decrease_m'] for item in window))
    moving_without_progress = [
        item for item in samples
        if item['displacement_m'] > 0.02 and item['motion_m'] < 0.005
    ]
    if omissions:
        kind = 'D'
    elif priority_window >= TOLERANCE_M and mean_window < TOLERANCE_M:
        kind = 'A'
    elif priority_window < TOLERANCE_M and moving_without_progress:
        kind = 'B'
    elif priority_window < TOLERANCE_M:
        kind = 'C'
    else:
        kind = 'unclassified'
    final = positions[-1]
    far = []
    for guide in np.flatnonzero(assignment >= 0):
        distance = float(np.linalg.norm(targets[int(assignment[guide])] - final[guide]))
        if distance > TOLERANCE_M:
            far.append({'guide': int(guide), 'distance_m': distance})
    return {
        'termination_status': metrics['termination_status'],
        'time_s': metrics['simulation_time'],
        'replans': metrics['replan_count'],
        'priority_start_s': float(event['time_s']),
        'priority_order_head': order[:4],
        'priority_steps': len(samples),
        'classification': kind,
        'priority_motion_m': priority_drop,
        'mean_remaining_decrease_m': mean_drop,
        'priority_window_motion_m': priority_window,
        'mean_window_decrease_m': mean_window,
        'window_steps': WINDOW,
        'position_tolerance_m': TOLERANCE_M,
        'last_step': samples[-1] if samples else None,
        'steps_with_motion_but_no_path_progress': len(moving_without_progress),
        'handoffs_before_completion': handoffs,
        'queue_omissions': omissions,
        'guides_beyond_tolerance': far,
        'coordination': {
            key: (metrics.get('coordination') or {}).get(key)
            for key in (
                'steps', 'enforce_attempts', 'enforce_installed', 'enforce_rejected',
                'exit_reason', 'fallback_steps', 'illegal_aim_steps',
            )
        },
    }


def _decision_counts(decisions):
    return dict(Counter(item['decision'] for item in decisions))


def analyze_counterfactual(destination, baseline_dir):
    """What happens after the saved termination if that stall does not stop the run."""
    metrics, data, event = _load_case(destination)
    baseline_metrics, baseline, _baseline_event = _load_case(baseline_dir)
    prefix = len(baseline['positions'])
    position_delta = float(np.max(np.abs(
        data['positions'][:prefix] - baseline['positions'])))
    velocity_count = min(len(data['safe_velocity']), len(baseline['safe_velocity']))
    velocity_delta = float(np.max(np.abs(
        data['safe_velocity'][:velocity_count] - baseline['safe_velocity'])))
    nominal_delta = float(np.max(np.abs(
        data['nominal_velocity'][:velocity_count] - baseline['nominal_velocity'])))
    paths = {int(key): np.asarray(value, float) for key, value in event['paths'].items()}
    order = [int(guide) for guide in event['priority_order']]
    frame = int(event['frame'])
    positions = data['positions']
    nominal = data['nominal_velocity']
    safe = data['safe_velocity']
    waypoints = data['waypoint_index']
    targets = data['targets']
    assignment = np.asarray(data['assignment'])
    index = 0
    mode = True
    active_guides = []
    already_inside = []
    omissions = []
    seen = set()
    stints = []
    current = None
    for step in range(frame, len(nominal)):
        while mode and index < len(order):
            guide = order[index]
            distance = float(np.linalg.norm(
                targets[int(assignment[guide])] - positions[step, guide]))
            if distance <= TOLERANCE_M:
                if active_guides and guide == active_guides[-1]['guide']:
                    active_guides[-1]['released_step'] = step
                    active_guides[-1]['release'] = 'arrived'
                else:
                    already_inside.append({'step': step, 'guide': guide, 'distance_m': distance})
                index += 1
            else:
                break
        if index >= len(order):
            mode = False
            guide = None
        else:
            guide = order[index]
            if not active_guides or active_guides[-1]['guide'] != guide:
                active_guides.append({
                    'guide': guide, 'start_step': step, 'start_s': float(step * 0.1),
                    'released_step': None, 'release': None,
                })
        for passed in order[:index]:
            distance = float(np.linalg.norm(
                targets[int(assignment[passed])] - positions[step + 1, passed]))
            if distance > TOLERANCE_M and passed not in seen:
                seen.add(passed)
                omissions.append({
                    'step': step, 'time_s': float((step + 1) * 0.1),
                    'guide': int(passed), 'distance_m': distance,
                })
        if guide is None:
            continue
        retargeted = _remaining(positions[step, guide], paths[guide], waypoints[step, guide])
        after = _remaining(positions[step + 1, guide], paths[guide], waypoints[step, guide])
        sample = {
            'time_s': float((step + 1) * 0.1),
            'motion_m': retargeted - after,
            'remaining_m': after,
            'target_distance_m': float(np.linalg.norm(
                targets[int(assignment[guide])] - positions[step + 1, guide])),
            'filtered_m_s': float(np.linalg.norm(safe[step, guide])),
            'nominal_m_s': float(np.linalg.norm(nominal[step, guide])),
        }
        if current is None or current['guide'] != guide:
            current = {'guide': guide, 'samples': [sample]}
            stints.append(current)
        else:
            current['samples'].append(sample)
    stalls = []
    for stint in stints:
        samples = stint['samples']
        for end in range(WINDOW, len(samples)):
            decrease = float(sum(item['motion_m'] for item in samples[end - WINDOW:end]))
            if decrease < TOLERANCE_M and samples[end - 1]['target_distance_m'] > TOLERANCE_M:
                stalls.append({
                    'time_s': samples[end - 1]['time_s'],
                    'guide': stint['guide'],
                    'window_motion_m': decrease,
                    'remaining_m': samples[end - 1]['remaining_m'],
                    'filtered_m_s': samples[end - 1]['filtered_m_s'],
                })
    original_time = float(baseline_metrics['simulation_time'])
    after = []
    for stint in stints:
        selected = [item for item in stint['samples'] if item['time_s'] > original_time + 1e-9]
        if not selected:
            continue
        after.append({
            'guide': stint['guide'],
            'steps': len(selected),
            'motion_m': float(sum(item['motion_m'] for item in selected)),
            'remaining_start_m': selected[0]['remaining_m'] + selected[0]['motion_m'],
            'remaining_end_m': selected[-1]['remaining_m'],
            'final_target_distance_m': selected[-1]['target_distance_m'],
        })
    far = []
    final = positions[-1]
    end_distance = {}
    for guide in np.flatnonzero(assignment >= 0):
        distance = float(np.linalg.norm(targets[int(assignment[guide])] - final[guide]))
        end_distance[int(guide)] = distance
        if distance > TOLERANCE_M:
            far.append({'guide': int(guide), 'distance_m': distance})
    yield_peak = {}
    for step in range(frame, len(nominal)):
        for guide in np.flatnonzero(assignment >= 0):
            guide = int(guide)
            if float(np.linalg.norm(nominal[step, guide])) > 1e-8:
                continue
            displacement = float(np.linalg.norm(positions[step + 1, guide] - positions[step, guide]))
            if displacement <= 1e-4:
                continue
            distance = float(np.linalg.norm(
                targets[int(assignment[guide])] - positions[step + 1, guide]))
            peak = yield_peak.setdefault(guide, {
                'max_distance_m': 0.0, 'yield_steps': 0, 'yield_displacement_m': 0.0,
            })
            peak['max_distance_m'] = max(peak['max_distance_m'], distance)
            peak['yield_steps'] += 1
            peak['yield_displacement_m'] += displacement
    for guide, peak in yield_peak.items():
        peak['end_distance_m'] = end_distance.get(guide)
        peak['returned_toward_target'] = bool(
            peak['end_distance_m'] + 1e-6 < peak['max_distance_m'])
    decisions = (metrics.get('coordination') or {}).get('progress_decisions') or []
    cut = len(baseline['safe_velocity'])
    offline = json.loads((Path(destination) / 'reevaluated_metrics.json').read_text(encoding='utf-8'))
    suppressed = metrics.get('priority_stall_suppressed_events') or []
    return {
        'status': metrics['termination_status'],
        'success': bool(metrics['success']),
        'counts_as_official_success': False,
        'time_s': metrics['simulation_time'],
        'baseline_status': baseline_metrics['termination_status'],
        'baseline_time_s': original_time,
        'replans': metrics['replan_count'],
        'baseline_replans': baseline_metrics['replan_count'],
        'offline_consistent': bool(offline['criteria_consistent']),
        'offline_success': bool(offline['success']),
        'position_prefix_delta_m': position_delta,
        'safe_velocity_prefix_delta_m_s': velocity_delta,
        'nominal_prefix_delta_m_s': nominal_delta,
        'target_delta_m': float(np.max(np.abs(data['targets'] - baseline['targets']))),
        'assignment_changed': bool(not np.array_equal(data['assignment'], baseline['assignment'])),
        'priority_subjects': active_guides,
        'already_inside_when_reached': already_inside,
        'queue_omissions': omissions,
        'progress_after_original_termination': after,
        'priority_stall_windows_after_start': {
            'count': len(stalls),
            'first': stalls[0] if stalls else None,
            'last': stalls[-1] if stalls else None,
        },
        'guides_beyond_tolerance': far,
        'yield_peaks': {str(key): value for key, value in sorted(
            yield_peak.items(), key=lambda item: -item[1]['max_distance_m'])[:6]},
        'suppressed_stall_count': len(suppressed),
        'first_suppressed_s': None if not suppressed else suppressed[0]['time_s'],
        'last_suppressed_s': None if not suppressed else suppressed[-1]['time_s'],
        'decisions_through_saved_horizon': _decision_counts(
            [item for item in decisions if item['step'] - 1 < cut]),
        'decisions_after_saved_horizon': _decision_counts(
            [item for item in decisions if item['step'] - 1 >= cut]),
        'coordination': {
            key: (metrics.get('coordination') or {}).get(key)
            for key in (
                'steps', 'enforce_attempts', 'enforce_installed', 'enforce_rejected',
                'exit_step', 'exit_reason', 'fallback_steps', 'illegal_aim_steps',
                'filter_seconds', 'enforce_seconds',
            )
        },
        'minimum_net_clearances': metrics.get('minimum_net_clearances'),
        'criteria': metrics.get('criteria'),
        'diagnostic_flag': bool(metrics.get('diagnostic_priority_stall_counterfactual')),
    }


def _counterfactual_task(task):
    os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'] = '1'
    destination = Path(task['destination'])
    with threadpool_limits(limits=1):
        started = perf_counter()
        replay(task['config'], Path(task['source']), destination)
        analysis = analyze_counterfactual(destination, task['baseline'])
        analysis['wall_s'] = perf_counter() - started
    analysis['label'] = task['label']
    print(json.dumps({
        'label': task['label'], 'status': analysis['status'], 'success': analysis['success'],
        'time_s': analysis['time_s'], 'prefix_m': analysis['position_prefix_delta_m'],
        'replans': analysis['replans'],
    }, default=str), flush=True)
    return analysis


def run_counterfactual(output, workers=2):
    output = Path(output)
    saved = []
    for scene, method in WIDE:
        saved.append({
            'label': f'{scene}/{method}',
            'saved': classify_saved(SAVED / scene / method),
        })
        print(json.dumps({'saved': saved[-1]['label'], 'class': saved[-1]['saved']['classification'],
                          'priority_window_m': saved[-1]['saved']['priority_window_motion_m'],
                          'mean_window_m': saved[-1]['saved']['mean_window_decrease_m']}), flush=True)
    tasks = []
    for scene, method in WIDE:
        source = NINE_SOURCE / scene / method / 'run'
        tasks.append({
            'label': f'{scene}/{method}',
            'config': str(source / 'config.yaml'),
            'source': str(source),
            'baseline': str(SAVED / scene / method),
            'destination': str(output / 'counterfactual' / scene / method),
        })
    if workers == 1:
        results = [_counterfactual_task(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_counterfactual_task, task) for task in tasks]
            results = [future.result() for future in futures]
    payload = {
        'diagnostic_only': True,
        'counts_as_official_success': False,
        'saved_classification': saved,
        'counterfactual': results,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / 'counterfactual_summary.json').write_text(
        json.dumps(payload, indent=2, default=str) + '\n', encoding='utf-8')
    return payload


def _candidate_task(task):
    """Official priority-path window. The diagnostic stall bypass stays off."""
    os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'] = '0'
    destination = Path(task['destination'])
    with threadpool_limits(limits=1):
        started = perf_counter()
        replay(task['config'], Path(task['source']), destination)
        elapsed = perf_counter() - started
    metrics = json.loads((destination / 'metrics.json').read_text(encoding='utf-8'))
    offline = json.loads((destination / 'reevaluated_metrics.json').read_text(encoding='utf-8'))
    current = np.load(destination / 'trajectory.npz')
    counterfactual = np.load(Path(task['counterfactual']) / 'trajectory.npz')
    saved = np.load(Path(task['baseline']) / 'trajectory.npz')
    same_shape = current['positions'].shape == counterfactual['positions'].shape
    prefix = len(saved['positions'])
    coordination = metrics.get('coordination') or {}
    row = {
        'label': task['label'],
        'status': metrics['termination_status'],
        'success': bool(metrics['success']),
        'time_s': metrics['simulation_time'],
        'replans': metrics['replan_count'],
        'offline_consistent': bool(offline['criteria_consistent']),
        'offline_success': bool(offline['success']),
        'counterfactual_position_delta_m': (
            None if not same_shape else float(np.max(np.abs(
                current['positions'] - counterfactual['positions'])))
        ),
        'counterfactual_same_length': bool(same_shape),
        'saved_prefix_delta_m': float(np.max(np.abs(current['positions'][:prefix] - saved['positions']))),
        'target_delta_m': float(np.max(np.abs(current['targets'] - saved['targets']))),
        'assignment_changed': bool(not np.array_equal(current['assignment'], saved['assignment'])),
        'exit_reason': coordination.get('exit_reason'),
        'exit_step': coordination.get('exit_step'),
        'exit_outside_tolerance': coordination.get('exit_outside_tolerance'),
        'reference_changes': len(coordination.get('progress_reference_changes') or []),
        'reference_kinds': dict(Counter(
            item['kind'] for item in coordination.get('progress_reference_changes') or [])),
        'enforce_attempts': coordination.get('enforce_attempts'),
        'enforce_installed': coordination.get('enforce_installed'),
        'enforce_rejected': coordination.get('enforce_rejected'),
        'fallback_steps': coordination.get('fallback_steps'),
        'minimum_net_clearances': metrics.get('minimum_net_clearances'),
        'criteria_failed': [key for key, value in metrics.get('criteria', {}).items() if not value],
        'diagnostic_flag': bool(metrics.get('diagnostic_priority_stall_counterfactual')),
        'wall_s': elapsed,
    }
    print(json.dumps({
        'label': row['label'], 'status': row['status'], 'time_s': row['time_s'],
        'counterfactual_m': row['counterfactual_position_delta_m'],
        'prefix_m': row['saved_prefix_delta_m'], 'replans': row['replans'],
    }), flush=True)
    return row


def run_candidate(output, workers=2):
    output = Path(output)
    tasks = []
    for scene, method in WIDE:
        source = NINE_SOURCE / scene / method / 'run'
        tasks.append({
            'label': f'{scene}/{method}',
            'config': str(source / 'config.yaml'),
            'source': str(source),
            'baseline': str(SAVED / scene / method),
            'counterfactual': str(output / 'counterfactual' / scene / method),
            'destination': str(output / 'candidate' / scene / method),
        })
    if workers == 1:
        results = [_candidate_task(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = [future.result() for future in [pool.submit(_candidate_task, task) for task in tasks]]
    payload = {'candidate': results, 'official': True}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'candidate_summary.json').write_text(
        json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runs/step1_priority_progress_monitor'))
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--counterfactual', action='store_true')
    parser.add_argument('--candidate', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.workers <= 2:
        raise ValueError('Use 1 or 2 episode workers')
    if args.counterfactual:
        payload = run_counterfactual(args.output, args.workers)
        brief = [{
            'label': item['label'], 'status': item['status'], 'time_s': item['time_s'],
            'prefix_m': item['position_prefix_delta_m'], 'far': item['guides_beyond_tolerance'],
            'stalls': item['priority_stall_windows_after_start']['count'],
            'omissions': item['queue_omissions'],
        } for item in payload['counterfactual']]
        print(json.dumps(brief, indent=2, default=str))
    if args.candidate:
        payload = run_candidate(args.output, args.workers)
        print(json.dumps(payload['candidate'], indent=2))


if __name__ == '__main__':
    main()
