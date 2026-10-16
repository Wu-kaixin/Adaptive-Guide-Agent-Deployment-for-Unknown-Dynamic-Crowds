"""Diagnose the saved wide-U replan failure and replay frozen target configurations.

The diagnosis reads the original trajectory. It does not move guides and it does
not overwrite that run. Replay calls the package controller with the saved
float64 targets and boundary.
"""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import LineString, Point, Polygon

from udc.boundary import Boundary
from udc.deployment import Plan
from udc.experiments.runner import experiment, reevaluate_result
from udc.navigation import navigate_and_assign, navigation_domain, route_assigned
from udc.safety import Safety, is_safe, path_clearances


def _sha256(path):
    digest = hashlib.sha256()
    digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def _jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    return value


def _remaining(position, path, index):
    value = float(np.linalg.norm(path[index] - position))
    if index < len(path) - 1:
        value += float(np.linalg.norm(np.diff(path[index:], axis=0), axis=1).sum())
    return value


def _endpoint(domain, point, people):
    return {
        'position': np.asarray(point, dtype=float).tolist(),
        'conservative_signed_m': domain.signed_free_distance(point),
        'physical_free_signed_m': _signed(domain.physical_free, point),
        'occupied_boundary_distance_m': float(Point(point).distance(domain.physical_obstacle.boundary)),
        'covered_by_conservative_free': bool(domain.free.covers(Point(point))),
        'covered_by_physical_free': bool(domain.physical_free.covers(Point(point))),
        'component': domain.component_index(point),
        'min_person_center_m': float(np.linalg.norm(np.asarray(point) - people, axis=1).min()),
    }


def _signed(geometry, point):
    sample = Point(point)
    distance = float(geometry.boundary.distance(sample))
    return distance if geometry.covers(sample) else -distance


def diagnose_saved_run(run_dir):
    """Reproduce the strict free-space rejection on a saved execution."""
    run_dir = Path(run_dir)
    data = np.load(run_dir / 'trajectory.npz')
    metrics = json.loads((run_dir / 'metrics.json').read_text(encoding='utf-8'))
    positions = data['positions']
    targets = data['targets']
    assignment = data['assignment']
    room = np.asarray(data['room'], dtype=float)
    people = data['crowd_positions']
    wall = float(metrics['safety_limits']['wall_distance'])
    crowd = Polygon(data['boundary_crowd'])
    domain = navigation_domain(room, wall, crowd, wall)
    limits = Safety(**metrics['safety_limits'])
    initial = navigate_and_assign(positions[0], targets, room, wall, crowd, wall)
    path_of = {
        int(guide): path for guide, path in zip(np.flatnonzero(initial.assignment >= 0), initial.paths)
    }
    first_exit = None
    outside_counts = []
    for frame in range(len(positions)):
        outside = []
        for guide, point in enumerate(positions[frame]):
            if assignment[guide] < 0 or domain.free.covers(Point(point)):
                continue
            outside.append(int(guide))
            if first_exit is None:
                first_exit = _event(domain, data, metrics, limits, people, targets, assignment,
                                    path_of, frame, int(guide), 'first_left_conservative_free_space')
        outside_counts.append(outside)
    failure_frame = len(positions) - 1
    failing = []
    for guide, point in enumerate(positions[failure_frame]):
        if assignment[guide] < 0:
            continue
        target = targets[int(assignment[guide])]
        if not domain.free.covers(Point(point)) or not domain.free.covers(Point(target)):
            failing.append(int(guide))
    failure_guide = min(failing, key=lambda guide: domain.signed_free_distance(positions[failure_frame, guide]))
    failure = _event(domain, data, metrics, limits, people, targets, assignment, path_of,
                     failure_frame, failure_guide, 'first_replan_rejection')
    try:
        route_assigned(positions[failure_frame], targets, assignment, room, wall, crowd, wall)
        strict_error = None
    except ValueError as error:
        strict_error = {'type': type(error).__name__, 'message': str(error)}
    stall_step = _first_stall(positions, data['safe_velocity'], data['waypoint_index'],
                              assignment, targets, path_of, metrics)
    windows = {
        'first_exit': _window(data, first_exit['frame'], first_exit['guide']),
        'replan_failure': _window(data, failure['frame'], failure['guide']),
    }
    return {
        'source_run': str(run_dir),
        'source_trajectory_sha256': _sha256(run_dir / 'trajectory.npz'),
        'source_metrics_sha256': _sha256(run_dir / 'metrics.json'),
        'saved_termination': metrics['termination_status'],
        'saved_failure_reason': metrics['failure_reason'],
        'saved_failure_stage': metrics['failure_stage'],
        'saved_steps': metrics['steps'],
        'saved_replan_count': metrics['replan_count'],
        'saved_deadlock_events': metrics['deadlock_events'],
        'geometry': {
            'wall_margin_m': domain.wall_margin,
            'crowd_margin_m': domain.crowd_margin,
            'simplify_tolerance_m': domain.simplify_tolerance_m,
            'planning_margin_m': domain.planning_margin_m,
            'planning_margin_is_safety_distance': False,
            'geometry_sha256': domain.geometry_sha256,
            'free_type': domain.free.geom_type,
            'component_count': 1 if domain.free.geom_type == 'Polygon' else len(domain.free.geoms),
            'guides_included_in_obstacle': False,
            'initial_and_replan_builder': 'navigation_domain',
        },
        'initial_positions_outside': int(sum(not domain.free.covers(Point(point)) for point in positions[0])),
        'targets_outside': int(sum(not domain.free.covers(Point(point)) for point in targets)),
        'target_min_conservative_signed_m': min(domain.signed_free_distance(point) for point in targets),
        'first_exit': first_exit,
        'replan_failure': failure,
        'failing_guides_at_rejection': failing,
        'strict_route_assigned_error': strict_error,
        'first_stall_step': stall_step,
        'stall_matches_rejection_frame': stall_step == failure['frame'] - 1,
        'deadlock_not_recorded_because': (
            'route_assigned raised inside recover before the deadlock event was appended '
            'and before replans was incremented'
        ),
        'windows': windows,
        'domain': domain,
        'data': data,
    }


def _event(domain, data, metrics, limits, people, targets, assignment, path_of, frame, guide, kind):
    point = data['positions'][frame, guide]
    target_index = int(assignment[guide])
    target = targets[target_index]
    waypoint = None if frame == 0 else int(data['waypoint_index'][frame - 1, guide])
    path = path_of[guide]
    step = None if frame == 0 else (data['positions'][frame, guide] - data['positions'][frame - 1, guide]).tolist()
    clearances = path_clearances(data['positions'][frame], data['positions'][frame], people, data['room'])
    return {
        'kind': kind,
        'frame': int(frame),
        'time_s': float(frame * 0.1),
        'guide': int(guide),
        'target_index': target_index,
        'start': _endpoint(domain, point, people),
        'target': _endpoint(domain, target, people),
        'waypoint_index': waypoint,
        'waypoint_position': None if waypoint is None else path[waypoint].tolist(),
        'waypoint_conservative_signed_m': None if waypoint is None else domain.signed_free_distance(path[waypoint]),
        'executed_step_m': step,
        'nominal_velocity': None if frame == 0 else data['nominal_velocity'][frame - 1, guide].tolist(),
        'safe_velocity': None if frame == 0 else data['safe_velocity'][frame - 1, guide].tolist(),
        'physical_clearances_m': {key: _jsonable(value) for key, value in clearances.items()},
        'physical_safe': bool(is_safe(clearances, limits)),
        'old_path': path.tolist(),
        'remaining_old_segment_in_free': bool(
            waypoint is not None and domain.free.covers(LineString([point, path[waypoint]]))
        ),
    }


def _window(data, frame, guide):
    start = max(0, frame - 2)
    stop = min(len(data['positions']), frame + 3)
    return {
        'frame_start': start,
        'frame_stop_exclusive': stop,
        'guide': int(guide),
        'time_s': (np.arange(start, stop) * 0.1).tolist(),
        'positions': data['positions'][start:stop, guide].tolist(),
        'nominal_velocity': data['nominal_velocity'][max(0, start - 1):max(0, stop - 1), guide].tolist(),
        'safe_velocity': data['safe_velocity'][max(0, start - 1):max(0, stop - 1), guide].tolist(),
    }


def _first_stall(positions, velocity, waypoints, assignment, targets, path_of, metrics):
    tolerance = 0.03
    window = 20
    active = np.flatnonzero(assignment >= 0)
    progress = [float(np.mean([
        _remaining(positions[0, guide], path_of[int(guide)], 1) for guide in active
    ]))]
    for step in range(len(velocity)):
        frame = step + 1
        progress.append(float(np.mean([
            _remaining(positions[frame, guide], path_of[int(guide)], int(waypoints[step, guide]))
            for guide in active
        ])))
        errors = np.linalg.norm(positions[frame, active] - targets[assignment[active]], axis=1)
        speed = np.linalg.norm(velocity[step, active], axis=1)
        stalled = (len(progress) >= window + 1
                   and progress[-window - 1] - progress[-1] < tolerance
                   and float(errors.max()) > tolerance
                   and float(speed.mean()) <= tolerance)
        if stalled:
            return step
    return None


def _without_live_objects(report):
    return {key: value for key, value in report.items() if key not in {'domain', 'data'}}


def write_diagnosis(run_dir, json_path, npz_path, figure_path):
    report = diagnose_saved_run(run_dir)
    domain, data = report['domain'], report['data']
    payload = _without_live_objects(report)
    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(json_path).write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    failure = report['replan_failure']
    first = report['first_exit']
    np.savez(
        npz_path,
        failure_position=np.asarray(failure['start']['position'], dtype=np.float64),
        failure_target=np.asarray(failure['target']['position'], dtype=np.float64),
        failure_path=np.asarray(failure['old_path'], dtype=np.float64),
        first_position=np.asarray(first['start']['position'], dtype=np.float64),
        first_target=np.asarray(first['target']['position'], dtype=np.float64),
        first_path=np.asarray(first['old_path'], dtype=np.float64),
        positions=data['positions'],
        targets=data['targets'],
        assignment=data['assignment'],
        nominal_velocity=data['nominal_velocity'],
        safe_velocity=data['safe_velocity'],
        waypoint_index=data['waypoint_index'],
        crowd_positions=data['crowd_positions'],
        crowd_radii=data['crowd_radii'],
        boundary_crowd=data['boundary_crowd'],
        conservative_obstacle=np.asarray(domain.conservative_obstacle.exterior.coords, dtype=np.float64),
        physical_obstacle=np.asarray(domain.physical_obstacle.exterior.coords, dtype=np.float64),
        free_exterior=np.asarray(domain.free.exterior.coords, dtype=np.float64),
    )
    _plot_failure(report, figure_path)
    return payload


def _plot_failure(report, path):
    data = report['data']
    domain = report['domain']
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), constrained_layout=True)
    for axis, event, title in (
        (axes[0], report['first_exit'], 'First exit from conservative free space'),
        (axes[1], report['replan_failure'], 'First replan rejection'),
    ):
        _draw_local(axis, data, domain, event)
        axis.set_title(title)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _draw_local(axis, data, domain, event):
    frame = event['frame']
    guide = event['guide']
    point = np.asarray(data['positions'][frame, guide], dtype=float)
    center = point
    half = 1.4
    for geometry, color, label in (
        (domain.physical_obstacle, '#c47b4a', 'Physical crowd obstacle'),
        (domain.conservative_obstacle, '#8c3f3f', 'Conservative obstacle'),
    ):
        axis.plot(*np.asarray(geometry.exterior.coords).T, color=color, linewidth=1.0, label=label)
    axis.plot(*np.asarray(domain.free.exterior.coords).T, color='#1f4e79', linewidth=0.8, label='Free-space boundary')
    path = np.asarray(event['old_path'])
    axis.plot(*path.T, color='#3778bf', linewidth=1.2, label='Old path')
    axis.plot(*data['positions'][:, guide].T, color='#9aa0a6', alpha=0.8, linewidth=0.8, label='Executed track')
    axis.scatter(*point, color='#b00020', s=36, zorder=5, label='Guide position')
    axis.scatter(*event['target']['position'], marker='+', color='#087f5b', s=80, label='Target')
    if event['waypoint_position'] is not None:
        axis.scatter(*event['waypoint_position'], marker='x', color='#5f3dc4', s=40, label='Waypoint')
    for person, radius in zip(data['crowd_positions'], data['crowd_radii']):
        if np.linalg.norm(person - center) < half + 1:
            axis.add_patch(plt.Circle(person, radius, color='#dc8e55', alpha=0.35))
    axis.set_xlim(center[0] - half, center[0] + half)
    axis.set_ylim(center[1] - half, center[1] + half)
    axis.set_aspect('equal')
    axis.grid(alpha=0.2)
    axis.legend(fontsize=7, loc='upper right')
    axis.set_xlabel('x [m]')
    axis.set_ylabel('y [m]')


def plot_before_after(before_npz, after_npz, path):
    before = np.load(before_npz)
    after = np.load(after_npz)
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), constrained_layout=True)
    for axis, record, title in (
        (axes[0], before, 'Before repair'),
        (axes[1], after, 'After repair'),
    ):
        positions = record['positions']
        for guide in range(positions.shape[1]):
            axis.plot(*positions[:, guide].T, color='#3778bf', alpha=0.45, linewidth=0.8)
        axis.plot(*np.vstack([record['boundary_crowd'], record['boundary_crowd'][:1]]).T,
                  color='#8c3f3f', linewidth=1.0, label='Occupied boundary')
        axis.scatter(*positions[-1].T, s=12, color='#b00020', label='Final guides')
        axis.scatter(*record['targets'].T, marker='+', color='#087f5b', label='Targets')
        finite = positions[np.isfinite(positions).all(axis=2)]
        center = finite.mean(axis=0)
        axis.set_xlim(center[0] - 8, center[0] + 8)
        axis.set_ylim(center[1] - 8, center[1] + 8)
        axis.set_aspect('equal')
        axis.set_title(title)
        axis.grid(alpha=0.2)
        axis.legend(fontsize=7)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def replay_saved_plan(source_run, output_run):
    """Execute the saved float64 targets with the current controller."""
    source_run = Path(source_run)
    output_run = Path(output_run)
    data = np.load(source_run / 'trajectory.npz')
    metrics = json.loads((source_run / 'metrics.json').read_text(encoding='utf-8'))
    config_path = source_run.parent / 'input.yaml'
    boundary = Boundary(
        data['boundary_crowd'], data['boundary_deployment'], data['boundary_arc'],
        float(data['boundary_length']),
    )
    line = Polygon(boundary.deployment).exterior
    arcs = np.array([line.project(Point(point)) for point in data['targets']])
    plan = Plan(
        positions=np.asarray(data['targets'], dtype=float),
        arc_positions=arcs,
        cost=np.zeros(1),
        max_gap=float(metrics['planned_max_gap']),
        method=str(metrics['method']),
        iterations=0,
        exit_reason='reused_saved_targets',
        density_total=float(metrics.get('total_demand') or 0.0),
    )
    result = experiment(
        config_path, output_run, plots=False, protocol='fixed_n',
        fixed_n=int(len(data['targets'])), method_label=str(metrics['method']),
        prescribed_boundary=boundary, prescribed_plan=plan,
        boundary_source='estimated_disk_union',
    )
    offline = reevaluate_result(output_run)
    replay = np.load(output_run / 'trajectory.npz')
    initial_delta = float(np.max(np.abs(replay['positions'][0] - data['positions'][0])))
    target_delta = float(np.max(np.abs(replay['targets'] - data['targets'])))
    return {
        'scene': result['scene'],
        'method': result['method'],
        'termination_status': result['termination_status'],
        'failure_stage': result['failure_stage'],
        'failure_reason': result['failure_reason'],
        'success': result['success'],
        'criteria': result['criteria'],
        'steps': result['steps'],
        'simulation_time': result['simulation_time'],
        'wall_time': result['wall_time'],
        'replan_count': result['replan_count'],
        'planning_margin_connections': result.get('planning_margin_connections'),
        'deadlock_events': result['deadlock_events'],
        'path_length': result.get('path_length'),
        'max_tracking_error': result.get('max_tracking_error'),
        'minimum_net_clearances': result.get('minimum_net_clearances'),
        'actual_max_gap': result.get('actual_max_gap'),
        'reevaluation_criteria_consistent': offline['criteria_consistent'],
        'reevaluation_success': offline['success'],
        'offline_criteria': offline['criteria'],
        'initial_position_max_abs_delta_m': initial_delta,
        'target_max_abs_delta_m': target_delta,
        'source_trajectory_sha256': _sha256(source_run / 'trajectory.npz'),
        'output': str(output_run),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnose', type=Path)
    parser.add_argument('--diagnosis-json', type=Path)
    parser.add_argument('--diagnosis-npz', type=Path)
    parser.add_argument('--diagnosis-figure', type=Path)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--output-root', type=Path)
    parser.add_argument('--scenes', nargs='*', default=[])
    parser.add_argument('--methods', nargs='*', default=['equal_arc', 'projected_mass', 'pairwise_euclidean'])
    parser.add_argument('--summary', type=Path)
    parser.add_argument('--comparison-figure', type=Path)
    args = parser.parse_args()
    if args.diagnose:
        payload = write_diagnosis(args.diagnose, args.diagnosis_json, args.diagnosis_npz, args.diagnosis_figure)
        print(json.dumps({
            'first_exit_frame': payload['first_exit']['frame'],
            'failure_frame': payload['replan_failure']['frame'],
            'failure_guide': payload['replan_failure']['guide'],
            'strict_error': payload['strict_route_assigned_error'],
            'stall_step': payload['first_stall_step'],
        }, indent=2))
        return
    rows = []
    for scene in args.scenes:
        for method in args.methods:
            source = args.source_root / scene / 'disk_union_primary' / method / 'run'
            output = args.output_root / scene / method / 'run'
            rows.append(replay_saved_plan(source, output))
            print(json.dumps({key: rows[-1][key] for key in ('scene', 'method', 'termination_status', 'success', 'replan_count')}))
    if args.comparison_figure and 'wide_u' in args.scenes:
        plot_before_after(
            args.source_root / 'wide_u' / 'disk_union_primary' / 'equal_arc' / 'run' / 'trajectory.npz',
            args.output_root / 'wide_u' / 'equal_arc' / 'run' / 'trajectory.npz',
            args.comparison_figure,
        )
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
