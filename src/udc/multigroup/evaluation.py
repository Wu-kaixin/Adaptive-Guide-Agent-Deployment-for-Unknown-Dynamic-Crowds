"""Independent truth-based motion and common-reference service evaluation."""
import numpy as np

from udc.deployment.planners import demand_profile, quadrature_mass, _interpolate
from udc.interfaces import Observation
from .geometry import construct_groups
from .observation import BoundedMeasurement


def partition_matches(groups, true_labels):
    inferred = np.empty(len(true_labels), dtype=int)
    for group in groups:
        inferred[list(group.rows)] = group.group_id
    return bool(np.array_equal(inferred[:, None] == inferred[None, :],
                               true_labels[:, None] == true_labels[None, :]))


def motion_metrics(execution, truth, room, config):
    """Analytic closest approach on every full linear interval, batched.

    Variable physical radii are used directly. Neither estimated occupancy
    nor the observed radii inflated by epsilon are passed to this evaluator.
    """
    positions, velocities = execution.positions, execution.velocities
    radius, clearance = config['guide_radius_m'], config['clearance_m']
    initial = positions[0]
    human_threshold = radius + truth.radii + clearance
    minima = {
        'human': float((np.linalg.norm(initial[:, None] - truth.positions[None, :], axis=2) - human_threshold).min()),
        'guide': None,
        'wall': float(np.minimum(initial, np.asarray(room) - initial).min() - config['wall_distance_m']),
    }
    first, second = np.triu_indices(len(initial), 1)
    if len(first):
        minima['guide'] = float((np.linalg.norm(initial[first] - initial[second], axis=1) - config['guide_distance_m']).min())
    initial_safe = all(value is None or value >= -config['numeric_tolerance_m'] for value in minima.values())
    if len(positions) < 2:
        return {'initial_safety_ok': initial_safe, 'executed_intervals': 0,
                'interval_safety_ok': None, 'unsafe_interval_count': None,
                'minimum_net_clearances_m': minima, 'terminal_arrival_ok': False,
                'simulation_time_s': None, 'travel_m': None, 'max_speed_m_s': None,
                'speed_limit_ok': None, 'kinematics_ok': None}

    def closest(relative, movement):
        denominator = np.sum(movement * movement, axis=-1)
        fraction = np.clip(-np.sum(relative * movement, axis=-1) / np.maximum(denominator, 1e-30), 0, 1)
        return np.linalg.norm(relative + fraction[..., None] * movement, axis=-1)

    violations = 0
    for start_index in range(0, len(positions) - 1, 32):
        start = positions[start_index:min(start_index + 32, len(positions) - 1)]
        end = positions[start_index + 1:min(start_index + 33, len(positions))]
        movement = end - start
        human = (closest(start[:, :, None] - truth.positions[None, None], movement[:, :, None])
                 - human_threshold[None, None]).min(axis=(1, 2))
        wall = np.minimum(np.minimum(start, end), np.asarray(room) - np.maximum(start, end)).min(axis=(1, 2)) - config['wall_distance_m']
        guide = (closest(start[:, first] - start[:, second], movement[:, first] - movement[:, second])
                 - config['guide_distance_m']).min(axis=1) if len(first) else np.full(len(start), np.inf)
        violations += int(np.count_nonzero(np.minimum(np.minimum(human, wall), guide) < -config['numeric_tolerance_m']))
        for name, values in [('human', human), ('wall', wall), ('guide', guide)]:
            if name != 'guide' or len(first):
                minima[name] = min(minima[name], float(values.min()))
    active = execution.assignment >= 0
    hold = config['hold_steps']
    enough = len(velocities) >= hold
    arrived = bool(enough and np.all(np.linalg.norm(velocities[-hold:], axis=2) <= config['speed_tolerance_m_s']))
    max_speed = float(np.linalg.norm(velocities, axis=2).max())
    kinematics = bool(np.allclose(velocities, np.diff(positions, axis=0) / config['dt_s'], rtol=0, atol=1e-12))
    # Target error is checked by caller, which has the actual selected targets.
    return {'initial_safety_ok': initial_safe, 'executed_intervals': len(velocities),
            'interval_safety_ok': violations == 0, 'unsafe_interval_count': violations,
            'minimum_net_clearances_m': minima, 'terminal_arrival_ok': arrived,
            'simulation_time_s': float(len(velocities) * config['dt_s']),
            'travel_m': float(np.linalg.norm(np.diff(positions, axis=0), axis=2).sum()),
            'max_speed_m_s': max_speed,
            'speed_limit_ok': max_speed <= config['max_speed_m_s'] + 1e-12,
            'kinematics_ok': kinematics,
            'active_guides': int(active.sum())}


def reference_groups(truth, room, config):
    """A frozen geometric reference from exact people; never sent to the planner."""
    exact = BoundedMeasurement(Observation(truth.positions, truth.radii, truth.demand), 0., 'evaluation_reference')
    return construct_groups(exact, room, config)[0]


def common_service(groups, truth, robots, config):
    """Euclidean geometric service on the SAME exact reference for all methods.

    This is deliberately distinct from observed-curve arc service used in
    planning. It does not assume a noise-induced arc correspondence exists.
    All active robots can provide service; assignments do not hide a bad group.
    """
    robots = np.asarray(robots, dtype=float).reshape(-1, 2)
    rows = []
    for group in groups:
        index = np.asarray(group.rows, dtype=int)
        density = demand_profile(group.boundary, truth.positions[index], truth.demand[index], config['bandwidth_m'])
        arcs, mass, length = quadrature_mass(group.boundary, density, config['evaluation_samples'])
        points = _interpolate(group.boundary, arcs)
        if len(robots):
            nearest = np.linalg.norm(points[:, None] - robots[None, :], axis=2).min(axis=1)
            cost = float(np.sum(mass * nearest ** 2) / mass.sum())
            uncovered = float(mass[nearest > config['max_gap_m'] / 2].sum() / mass.sum())
        else:
            cost, uncovered = None, 1.
        rows.append({'group_id': group.group_id, 'uncovered': uncovered,
                     'mean_squared_euclidean_distance_m2': cost, 'demand_total': group.demand_total,
                     'reference_length_m': length})
    weights = np.array([row['demand_total'] for row in rows])
    weights /= weights.sum()
    return {'groups': rows, 'worst_group_uncovered': max(row['uncovered'] for row in rows),
            'weighted_uncovered': float(sum(w * row['uncovered'] for w, row in zip(weights, rows))),
            'reference': 'exact_people_external_offset_v1', 'distance': 'euclidean',
            'samples_per_group': config['evaluation_samples']}


def evaluate(execution, targets, target_groups, groups, measurement, truth, room, config):
    motion = motion_metrics(execution, truth, room, config)
    active = execution.assignment >= 0
    reached, observed_gap_ok, observed_curve_ok = False, False, False
    final = execution.positions[-1]
    if len(targets) and len(execution.velocities) >= config['hold_steps']:
        history = execution.positions[-config['hold_steps']:, active]
        target_error = np.linalg.norm(history - targets[execution.assignment[active]][None], axis=2)
        reached = bool(target_error.max() <= config['position_tolerance_m'])
    elif not len(targets):
        reached = True  # No assignment; full coverage below still fails.
    motion['terminal_arrival_ok'] = bool(reached and motion['terminal_arrival_ok'])
    gaps, curve_errors = [], []
    from shapely.geometry import Point, Polygon
    from udc.deployment.planners import periodic_gaps
    for group in groups:
        target_indices = set(np.flatnonzero(target_groups == group.group_id).tolist())
        selected = [i for i, target in enumerate(execution.assignment) if target in target_indices]
        line = Polygon(group.boundary.deployment).exterior
        if not selected:
            gaps.append(group.boundary.length)
            curve_errors.append(None)
            continue
        points = final[selected]
        sites = np.sort([line.project(Point(point)) for point in points])
        gaps.append(float(periodic_gaps(sites, group.boundary.length).max()))
        curve_errors.append(max(float(line.distance(Point(point))) for point in points))
    observed_gap_ok = bool(all(gap <= config['max_gap_m'] for gap in gaps)
                           and all(error is not None for error in curve_errors))
    observed_curve_ok = bool(all(error is not None and error <= config['curve_tolerance_m'] for error in curve_errors))
    references = reference_groups(truth, room, config)
    planned_service = common_service(references, truth, targets, config)
    actual_service = common_service(references, truth, final[active], config) if len(execution.velocities) else None
    safe = motion['initial_safety_ok'] and motion['interval_safety_ok'] is True
    deployment_completed = bool(execution.status == 'ARRIVED' and safe and motion['terminal_arrival_ok']
                                and motion['speed_limit_ok'] and motion['kinematics_ok'])
    observed_full = bool(deployment_completed and observed_gap_ok and observed_curve_ok)
    reference_full = bool(deployment_completed and actual_service['worst_group_uncovered'] == 0.)
    noise = np.linalg.norm(measurement.observation.positions - truth.positions, axis=1)
    matching = partition_matches(groups, truth.group_ids)
    respected = bool(np.all(noise <= measurement.position_error_bound_m + 1e-12))
    return {
        'group_partition_matches_truth': matching,
        'measurement_max_error_m': float(noise.max()),
        'declared_error_bound_m': measurement.position_error_bound_m,
        'error_bound_respected': respected,
        'motion': motion, 'deployment_completed': deployment_completed,
        'observed_full_coverage': observed_full, 'reference_full_coverage': reference_full,
        'full_task_success': bool(observed_full and reference_full and matching and respected),
        'observed_final_max_gaps_m': gaps, 'observed_final_curve_errors_m': curve_errors,
        'planned_common_service': planned_service, 'actual_common_service': actual_service,
        'claim': 'static geometric deployment, not crowd response or behavioral risk',
    }
