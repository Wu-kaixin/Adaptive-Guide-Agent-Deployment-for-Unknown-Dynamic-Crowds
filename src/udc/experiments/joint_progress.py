"""Offline joint-velocity references for saved wide-U stalls.

The solver budget is fixed before the run. A failed search is not an
infeasibility proof. This module does not change the controller.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.optimize import minimize
from shapely.geometry import Polygon
from threadpoolctl import threadpool_limits

from udc.navigation import navigation_domain
from udc.safety import Safety, is_safe, path_clearances
from udc.safety.filter import _obstacle_support, _planning_progress_ok, safe_velocity

SOURCE = Path('runs/step1_navigation_control_consistency/full/regression')
CASES = (
    ('wide_u', 30, 'equal_arc'),
    ('wide_u', 26, 'equal_arc'),
    ('wide_u', 30, 'projected_mass'),
    ('wide_u', 30, 'pairwise_euclidean'),
)
MAXITER = 200
ROLLOUT_STEPS = 40
WINDOW = 20
SPEED_TOLERANCE = 0.03


def _halfplanes(positions, people, room, dt, limits, obstacle):
    n = len(positions)
    rows, bounds, names = [], [], []

    def add(kind, guide, normal, bound, other=None):
        row = np.zeros(2 * n)
        row[2 * guide:2 * guide + 2] = normal
        if other is not None:
            row[2 * other:2 * other + 2] = -normal
        rows.append(row)
        bounds.append(float(bound))
        names.append({
            'kind': kind, 'guide': int(guide),
            'other_guide': None if other is None else int(other),
            'bound_m_s': float(bound),
            'normal': np.asarray(normal, float).tolist(),
        })

    for i in range(n):
        for j in range(i):
            delta = positions[i] - positions[j]
            distance = float(np.linalg.norm(delta))
            if distance < 1e-12:
                continue
            bound = (limits.guide_distance + 1e-8 - distance) / dt
            if bound > -2 * limits.max_speed:
                add('guide_guide', i, delta / distance, bound, j)
        deltas = positions[i] - people
        distances = np.linalg.norm(deltas, axis=1)
        for delta, distance in zip(deltas, distances):
            distance = float(distance)
            if distance < 1e-12:
                continue
            bound = (limits.crowd_distance + 1e-8 - distance) / dt
            if bound > -limits.max_speed:
                add('guide_human', i, delta / distance, bound)
        for axis in range(2):
            normal = np.eye(2)[axis]
            for direction, gap in ((normal, positions[i, axis]), (-normal, room[axis] - positions[i, axis])):
                bound = (limits.wall_distance + 1e-8 - float(gap)) / dt
                if bound > -limits.max_speed:
                    add('guide_wall', i, direction, bound)
        if obstacle is not None and not obstacle.is_empty:
            normal, signed = _obstacle_support(positions[i], obstacle)
            bound = -float(signed) / dt
            if bound > -limits.max_speed:
                add('planning_boundary', i, normal, bound)
    if rows:
        return np.vstack(rows), np.asarray(bounds, float), names
    return np.zeros((0, 2 * n)), np.zeros(0), names


def _installed_paths(metrics, frame):
    """Path geometry in force when the command at ``frame`` was computed."""
    chosen = None
    for attempt in metrics['recovery_attempts']:
        if attempt.get('accepted') and attempt.get('paths') and int(attempt['frame']) <= frame:
            chosen = attempt
    if chosen is None:
        for attempt in metrics['recovery_attempts']:
            if attempt.get('paths'):
                chosen = attempt
                break
    paths = {int(key): np.asarray(value, float) for key, value in chosen['paths'].items()}
    return paths, {
        'frame': chosen.get('frame'), 'strategy': chosen.get('strategy'),
        'classification': chosen.get('classification'),
        'budget_before': chosen.get('budget_before'),
        'budget_remaining': chosen.get('budget_remaining'),
    }


def _heading(position, path, index):
    index = int(np.clip(index, 0, len(path) - 1))
    delta = path[index] - position
    distance = float(np.linalg.norm(delta))
    if distance > 1e-8:
        return delta / distance, distance, 'to_waypoint', index
    if index < len(path) - 1:
        edge = path[index + 1] - path[index]
        length = float(np.linalg.norm(edge))
        if length > 1e-8:
            return edge / length, 0.0, 'along_next_edge', index
    return np.zeros(2), distance, 'at_aim', index


def _remaining(position, path, index):
    index = int(np.clip(index, 0, len(path) - 1))
    value = float(np.linalg.norm(path[index] - position))
    if index < len(path) - 1:
        value += float(np.linalg.norm(np.diff(path[index:], axis=0), axis=1).sum())
    return value


def _onset(mean_speed, trigger):
    """Earliest step of the low-speed run that ends at the stall trigger.

    ``mean_speed[k]`` is the mean active filtered speed at control step k.
    The runner fires at ``trigger`` after ``WINDOW`` steps. Onset walks backward
    from that trigger, and only while the mean stays at or below 0.03 m/s.
    """
    start = max(0, int(trigger) - WINDOW)
    chosen = int(trigger)
    for step in range(int(trigger), start - 1, -1):
        if mean_speed[step] <= SPEED_TOLERANCE:
            chosen = step
        else:
            break
    return chosen


def _solve(objective, gradient, nominal, matrix, bounds, limits, extra=()):
    n = len(nominal)
    x0 = nominal.ravel()
    constraints = []
    for row, bound in zip(matrix, bounds):
        constraints.append({
            'type': 'ineq',
            'fun': lambda vector, row=row, bound=bound: float(row @ vector - bound),
            'jac': lambda vector, row=row: row,
        })
    for guide in range(n):
        constraints.append({
            'type': 'ineq',
            'fun': lambda vector, guide=guide: float(
                limits.max_speed ** 2 - vector[2 * guide:2 * guide + 2] @ vector[2 * guide:2 * guide + 2]),
            'jac': lambda vector, guide=guide: _speed_jac(vector, guide),
        })
    constraints.extend(extra)
    started = perf_counter()
    solved = minimize(objective, x0, jac=gradient, constraints=constraints, method='SLSQP',
                      options={'maxiter': MAXITER, 'ftol': 1e-12, 'disp': False})
    elapsed = perf_counter() - started
    velocity = solved.x.reshape(n, 2)
    residual = float(np.max(bounds - matrix @ solved.x, initial=0.0)) if len(bounds) else 0.0
    return solved, velocity, residual, elapsed


def _speed_jac(vector, guide):
    jac = np.zeros_like(vector)
    jac[2 * guide:2 * guide + 2] = -2.0 * vector[2 * guide:2 * guide + 2]
    return jac


def _validate(positions, velocity, people, room, dt, limits, obstacle):
    speeds = np.linalg.norm(velocity, axis=1)
    end = positions + dt * velocity
    checked = path_clearances(positions, end, people, room)
    planning = all(
        _planning_progress_ok(positions[i], end[i], obstacle) for i in range(len(positions))
    )
    return {
        'max_speed_m_s': float(speeds.max(initial=0.0)),
        'speed_limit_ok': bool(speeds.max(initial=0.0) <= limits.max_speed + 1e-8),
        'interval_safe': bool(is_safe(checked, limits)),
        'planning_ok': bool(planning),
        'clearances_m': {
            key: None if value is None else float(value) for key, value in checked.items()
        },
    }


def _objective_gap(velocity, nominal):
    return 0.5 * float(np.sum((velocity - nominal) ** 2))


def _frame_record(data, metrics, limits, domain, frame, role):
    dt = 0.1
    positions = np.asarray(data['positions'][frame], float)
    nominal = np.asarray(data['nominal_velocity'][frame], float)
    filtered = np.asarray(data['safe_velocity'][frame], float)
    waypoints = np.asarray(data['waypoint_index'][frame])
    people = np.asarray(data['crowd_positions'], float)
    room = np.asarray(data['room'], float)
    paths, path_source = _installed_paths(metrics, frame)
    matrix, bounds, names = _halfplanes(
        positions, people, room, dt, limits, domain.conservative_obstacle)
    started = perf_counter()
    rerun = safe_velocity(
        positions, nominal, people, room, dt, limits, domain.conservative_obstacle)
    filter_seconds = perf_counter() - started
    headings, remainders = {}, {}
    guides = []
    for guide, path in sorted(paths.items()):
        heading, distance, kind, index = _heading(positions[guide], path, waypoints[guide])
        headings[guide] = heading
        remainders[guide] = _remaining(positions[guide], path, int(waypoints[guide]))
        if remainders[guide] > 0.03 or np.linalg.norm(nominal[guide]) > 0.01:
            guides.append({
                'guide': int(guide),
                'remaining_m': remainders[guide],
                'waypoint_index': int(waypoints[guide]),
                'aim': kind,
                'aim_distance_m': distance,
                'nominal_speed_m_s': float(np.linalg.norm(nominal[guide])),
                'filtered_speed_m_s': float(np.linalg.norm(filtered[guide])),
                'heading': heading.tolist(),
                'signed_free_distance_m': float(domain.signed_free_distance(positions[guide])),
            })
    order = sorted(remainders, key=lambda guide: (-remainders[guide], guide))
    priority = int(order[0])
    tangent = headings[priority]
    flat_nominal = nominal.ravel()
    flat_filtered = filtered.ravel()
    constraints = []
    for name, row, bound in zip(names, matrix, bounds):
        constraints.append({
            **name,
            'nominal_slack_m_s': float(row @ flat_nominal - bound),
            'filtered_slack_m_s': float(row @ flat_filtered - bound),
            'priority_tangent_coefficient': float(row[2 * priority:2 * priority + 2] @ tangent),
        })

    def progress(vector, guide=priority, tangent=tangent):
        return float(tangent @ vector.reshape(-1)[2 * guide:2 * guide + 2])

    reference_a, velocity_a, residual_a, seconds_a = _solve(
        lambda vector: _objective_gap(vector.reshape(len(nominal), 2), nominal),
        lambda vector: (vector - flat_nominal),
        nominal, matrix, bounds, limits,
    )
    witness_a = _validate(positions, velocity_a, people, room, dt, limits, domain.conservative_obstacle)
    reference_b, velocity_b, residual_b, seconds_b = _solve(
        lambda vector, guide=priority, tangent=tangent: -progress(vector, guide, tangent),
        lambda vector, guide=priority, tangent=tangent: _progress_gradient(vector, guide, tangent),
        nominal, matrix, bounds, limits,
    )
    witness_b = _validate(positions, velocity_b, people, room, dt, limits, domain.conservative_obstacle)
    classification = _classify(filtered, velocity_a, velocity_b, witness_b, progress)
    return {
        'role': role,
        'frame': int(frame),
        'time_s': float(frame * dt),
        'path_source': path_source,
        'priority_guide': priority,
        'priority_order': [int(guide) for guide in order if remainders[guide] > 0.03],
        'priority_heading': tangent.tolist(),
        'active_guides': guides,
        'constraints': constraints,
        'filter': {
            'status': rerun.status,
            'iterations': int(rerun.iterations),
            'residual_m_s': float(rerun.residual),
            'fallback_used': bool(rerun.fallback_used),
            'objective': _objective_gap(rerun.velocity, nominal),
            'priority_progress_m_s': progress(rerun.velocity),
            'matches_saved_velocity': bool(np.max(np.abs(rerun.velocity - filtered)) < 1e-8),
            'seconds': filter_seconds,
        },
        'reference_a': {
            'objective_name': 'euclidean_projection_of_saved_nominal',
            'solver': 'SLSQP',
            'maxiter': MAXITER,
            'success': bool(reference_a.success),
            'message': str(reference_a.message),
            'iterations': int(reference_a.nit),
            'seconds': seconds_a,
            'linear_residual_m_s': residual_a,
            'objective': _objective_gap(velocity_a, nominal),
            'priority_progress_m_s': progress(velocity_a),
            'max_abs_delta_vs_filter_m_s': float(np.max(np.abs(velocity_a - rerun.velocity))),
            'witness': witness_a,
        },
        'reference_b': {
            'objective_name': 'maximize_priority_progress_along_installed_segment',
            'same_linear_set_as_filter': True,
            'solver': 'SLSQP',
            'maxiter': MAXITER,
            'success': bool(reference_b.success),
            'message': str(reference_b.message),
            'iterations': int(reference_b.nit),
            'seconds': seconds_b,
            'linear_residual_m_s': residual_b,
            'priority_progress_m_s': progress(velocity_b),
            'speeds_m_s': np.linalg.norm(velocity_b, axis=1).round(6).tolist(),
            'witness': witness_b,
            'priority_retreat': bool(progress(velocity_b) < -1e-4),
        },
        'classification': classification,
    }


def _progress_gradient(vector, guide, tangent):
    gradient = np.zeros_like(vector)
    gradient[2 * guide:2 * guide + 2] = -tangent
    return gradient


def _classify(filtered, velocity_a, velocity_b, witness_b, progress):
    filter_progress = abs(progress(filtered))
    reference_gap = float(np.max(np.abs(velocity_a - filtered)))
    progress_b = progress(velocity_b)
    safe = witness_b['interval_safe'] and witness_b['planning_ok'] and witness_b['speed_limit_ok']
    if reference_gap > 1e-3 and progress(velocity_a) > filter_progress + 0.05:
        return 'filter_projection_disagrees'
    if safe and progress_b > 0.05:
        return 'safe_progress_velocity_found'
    if safe and abs(progress_b) <= 0.05:
        return 'safe_velocity_without_clear_progress'
    if not witness_b['interval_safe'] or not witness_b['planning_ok'] or not witness_b['speed_limit_ok']:
        return 'candidate_failed_interval_safety'
    return 'numerical_search_did_not_find_a_candidate'


def _rollout(data, metrics, limits, domain, frame):
    """Forty steps of the along-path nominal through the existing joint filter.

    Waypoints advance only when the current aim is reached. This is a mechanism
    check on the saved scene, not a second controller and not a recorded tape.
    """
    dt = 0.1
    positions = np.asarray(data['positions'][frame], float).copy()
    waypoints = np.asarray(data['waypoint_index'][frame], int).copy()
    people = np.asarray(data['crowd_positions'], float)
    room = np.asarray(data['room'], float)
    paths, _source = _installed_paths(metrics, frame)
    order = sorted(
        paths, key=lambda guide: (-_remaining(positions[guide], paths[guide], waypoints[guide]), guide))
    priority_index = 0
    initial_guide = int(order[0])
    initial_remaining = _remaining(positions[initial_guide], paths[initial_guide], waypoints[initial_guide])
    trace = []
    filter_seconds = 0.0
    for step in range(ROLLOUT_STEPS):
        while priority_index < len(order):
            guide = order[priority_index]
            if _remaining(positions[guide], paths[guide], waypoints[guide]) > 0.03:
                break
            priority_index += 1
        if priority_index >= len(order):
            break
        guide = int(order[priority_index])
        heading, distance, _kind, index = _heading(positions[guide], paths[guide], waypoints[guide])
        nominal = np.zeros_like(positions)
        request = 0.0 if distance <= 1e-8 else min(limits.max_speed, distance / dt)
        nominal[guide] = request * heading
        started = perf_counter()
        result = safe_velocity(
            positions, nominal, people, room, dt, limits, domain.conservative_obstacle)
        filter_seconds += perf_counter() - started
        witness = _validate(positions, result.velocity, people, room, dt, limits, domain.conservative_obstacle)
        progress = float(heading @ result.velocity[guide])
        new_positions = positions + dt * result.velocity
        waypoints = _advance(positions, new_positions, paths, waypoints)
        trace.append({
            'step': step,
            'priority_guide': guide,
            'requested_speed_m_s': float(request),
            'progress_m_s': progress,
            'priority_remaining_m': _remaining(new_positions[guide], paths[guide], waypoints[guide]),
            'fallback_used': bool(result.fallback_used),
            'status': result.status,
            'witness': witness,
            'retreat': bool(progress < -1e-4),
        })
        positions = new_positions
        if not (witness['interval_safe'] and witness['planning_ok'] and witness['speed_limit_ok']):
            break
    safe_steps = [item for item in trace if item['witness']['interval_safe'] and item['witness']['planning_ok']]
    return {
        'start_frame': int(frame),
        'horizon_steps': ROLLOUT_STEPS,
        'steps_run': len(trace),
        'filter_seconds': filter_seconds,
        'initial_priority_guide': initial_guide,
        'initial_remaining_m': initial_remaining,
        'final_remaining_m': _remaining(positions[initial_guide], paths[initial_guide], waypoints[initial_guide]),
        'priority_remaining_drop_m': initial_remaining - _remaining(
            positions[initial_guide], paths[initial_guide], waypoints[initial_guide]),
        'any_priority_retreat': any(item['retreat'] for item in trace),
        'all_run_steps_interval_safe': all(
            item['witness']['interval_safe'] and item['witness']['planning_ok'] and item['witness']['speed_limit_ok']
            for item in trace
        ),
        'min_guide_clearance_m': min(
            (item['witness']['clearances_m']['guides'] for item in safe_steps
             if item['witness']['clearances_m']['guides'] is not None),
            default=None,
        ),
        'trace_head': trace[:3],
        'trace_tail': trace[-2:],
    }


def _advance(previous, current, paths, waypoints):
    updated = waypoints.copy()
    for guide, path in paths.items():
        index = int(updated[guide])
        if index >= len(path) - 1:
            continue
        before = path[index] - previous[guide]
        after = path[index] - current[guide]
        if float(np.linalg.norm(after)) <= 1e-6 or float(np.dot(before, after)) <= 0.0:
            updated[guide] = index + 1
    return updated


def _case(scene, seed, method):
    directory = SOURCE / scene / f'seed_{seed}' / method
    metrics = json.loads((directory / 'metrics.json').read_text(encoding='utf-8'))
    data = np.load(directory / 'trajectory.npz')
    limits = Safety(**metrics['safety_limits'])
    domain = navigation_domain(data['room'], 0.55, Polygon(data['boundary_crowd']), 0.55)
    active = data['assignment'] >= 0
    mean_speed = np.linalg.norm(data['safe_velocity'][:, active], axis=2).mean(axis=1)
    stalls = []
    for event in metrics['deadlock_events']:
        trigger = int(event['step'])
        chosen = _onset(mean_speed, trigger)
        stalls.append({
            'trigger_step': trigger,
            'trigger_time_s': trigger * 0.1,
            'onset_step': chosen,
            'onset_time_s': chosen * 0.1,
            'strategy': event['strategy'],
            'onset_mean_filtered_speed_m_s': float(mean_speed[chosen]),
            'trigger_mean_filtered_speed_m_s': float(mean_speed[trigger]),
        })
    primary = stalls[0]['onset_step']
    priority_event = next(item for item in stalls if item['strategy'] == 'priority_yield')
    priority_frame = min(priority_event['trigger_step'] + 1, len(data['safe_velocity']) - 1)
    frames = [
        _frame_record(data, metrics, limits, domain, primary, 'first_stall_onset'),
        _frame_record(data, metrics, limits, domain, priority_frame, 'first_priority_command'),
    ]
    rollout = _rollout(data, metrics, limits, domain, priority_frame)
    return {
        'scene': scene, 'seed': seed, 'method': method,
        'saved_commit': metrics.get('commit'),
        'saved_source_sha256': metrics.get('source_sha256'),
        'termination_status': metrics['termination_status'],
        'simulation_time_s': metrics['simulation_time'],
        'replans': metrics['replan_count'],
        'selection_rule': (
            'onset is the earliest control step in [trigger-20, trigger] whose mean '
            'active filtered speed stays <= 0.03 m/s through the stall trigger'
        ),
        'stalls': stalls,
        'frames': frames,
        'along_path_rollout': rollout,
    }


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    with threadpool_limits(limits=1):
        cases = [_case(*item) for item in CASES]
    pairwise = next(item for item in cases if item['method'] == 'pairwise_euclidean')
    projected = next(item for item in cases if item['method'] == 'projected_mass')
    same_input = pairwise['saved_source_sha256'] == projected['saved_source_sha256']
    payload = {
        'solver': {'name': 'SLSQP', 'maxiter': MAXITER, 'ftol': 1e-12},
        'rollout_steps': ROLLOUT_STEPS,
        'wall_seconds': perf_counter() - started,
        'same_seed30_source_hash': bool(same_input),
        'cases': cases,
    }
    (output / 'diagnosis.json').write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runs/step1_joint_progress_diagnostics'))
    args = parser.parse_args()
    payload = run(args.output)
    brief = []
    for case in payload['cases']:
        frame = case['frames'][0]
        brief.append({
            'id': f"{case['scene']}:{case['seed']}:{case['method']}",
            'onset_s': case['stalls'][0]['onset_time_s'],
            'class': frame['classification'],
            'filter_progress': frame['filter']['priority_progress_m_s'],
            'reference_b_progress': frame['reference_b']['priority_progress_m_s'],
            'reference_b_safe': frame['reference_b']['witness']['interval_safe'],
            'rollout_safe': case['along_path_rollout']['all_run_steps_interval_safe'],
            'rollout_retreat': case['along_path_rollout']['any_priority_retreat'],
        })
    print(json.dumps({'wall_s': payload['wall_seconds'], 'cases': brief}, indent=2))


if __name__ == '__main__':
    main()
