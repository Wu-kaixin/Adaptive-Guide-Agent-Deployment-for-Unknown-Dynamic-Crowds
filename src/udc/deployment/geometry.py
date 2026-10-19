"""Geometry checks that sit beside a plan and do not change it.

Adjacent means consecutive in curve order, including the pair that crosses the
parameter origin. It does not mean the spatially nearest sample.
"""
from __future__ import annotations

import math

import numpy as np

from udc.boundary.prescribed import hausdorff, resample_arclength
from udc.deployment.planners import (
    _interpolate, coverage_statistics, feasibility_bounds, infeasibility_certificate,
    periodic_gaps, quadrature_mass,
)


def count_rule(length, gap_limit, separation):
    """Two frozen tiers. The integers are not changed when a method fails.

    ``near`` is the arc-gap necessary lower bound ``ceil(L / H_p)``.
    ``margin`` is ``ceil(L / g*)`` with ``g* = d_min * pi / 2``, the arc gap
    whose semicircular chord equals the center-separation threshold. The
    separation upper bound ``floor(L / d_min)`` is reported and is not used to
    replace either tier.
    """
    if min(length, gap_limit, separation) <= 0 or not np.isfinite([length, gap_limit, separation]).all():
        raise ValueError('Count rule needs positive finite length, gap limit and separation')
    near = max(3, int(math.ceil(length / gap_limit - 1e-12)))
    chord_gap = float(separation * math.pi / 2)
    margin = max(near, int(math.ceil(length / chord_gap - 1e-12)))
    upper = int(math.floor(length / separation + 1e-12))
    bounds = feasibility_bounds(length, gap_limit, separation)
    return {
        'near': near,
        'margin': margin,
        'separation_upper_bound': upper,
        'chord_gap_m': chord_gap,
        'margin_exceeds_separation_upper': bool(margin > upper),
        'near_exceeds_separation_upper': bool(near > upper),
        'bounds': bounds,
        'assumptions': (
            'ceil(L/H_p) and floor(L/d_min) assume every target lies on this one '
            'closed curve and that a chord is no longer than its arc. They do not '
            'imply that every pair is at least d_min apart in the plane.'
        ),
    }


def tier_count(rule, tier):
    if tier not in {'near', 'margin'}:
        raise ValueError(f'Unknown guide tier: {tier}')
    count = int(rule[tier])
    certificate = infeasibility_certificate(rule['bounds'], count)
    return count, certificate


def _round(value, digits=6):
    if value is None:
        return None
    return float(np.round(value, digits))


def pair_geometry(boundary, sites, gap_limit, separation):
    """Arc gaps and every pairwise Euclidean distance for sites in curve order."""
    length = float(boundary.length)
    sites = np.sort(np.asarray(sites, dtype=float) % length)
    gaps = periodic_gaps(sites, length)
    points = _interpolate(boundary, sites)
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    count = len(sites)
    pairs = []
    adjacent_best = math.inf
    adjacent_pair = None
    other_best = math.inf
    other_pair = None
    for i in range(count):
        j = (i + 1) % count
        adjacent = float(distances[i, j])
        pairs.append({
            'i': i, 'j': j, 'adjacent': True,
            'euclidean_m': _round(adjacent),
            'arc_gap_m': _round(float(gaps[i])),
        })
        if adjacent < adjacent_best:
            adjacent_best = adjacent
            adjacent_pair = (i, j)
        for j in range(i + 1, count):
            if min(j - i, count - (j - i)) == 1:
                continue
            distance = float(distances[i, j])
            pairs.append({
                'i': i, 'j': j, 'adjacent': False,
                'euclidean_m': _round(distance),
                'arc_gap_m': None,
            })
            if distance < other_best:
                other_best = distance
                other_pair = (i, j)
    global_index = np.unravel_index(int(np.argmin(distances)), distances.shape)
    global_distance = float(distances[global_index])
    curve_error = np.array([
        float(np.min(np.linalg.norm(boundary.deployment - point, axis=1))) for point in points
    ])
    arc_limit_ok = bool(float(gaps.max()) <= gap_limit + 1e-9)
    arc_box_ok = bool(arc_limit_ok and float(gaps.min()) + 1e-12 >= separation)
    euclidean_ok = bool(global_distance >= separation)
    worst = _pair_record(points, sites, distances, global_index, count)
    return {
        'arc_positions': [_round(value) for value in sites],
        'positions': np.round(points, 6).tolist(),
        'min_arc_gap_m': _round(float(gaps.min())),
        'max_arc_gap_m': _round(float(gaps.max())),
        'seam_arc_gap_m': _round(float(gaps[-1])),
        'sum_arc_gap_error_m': _round(float(abs(gaps.sum() - length))),
        'min_adjacent_euclidean_m': None if adjacent_pair is None else _round(adjacent_best),
        'min_nonadjacent_euclidean_m': None if other_pair is None else _round(other_best),
        'min_euclidean_m': _round(global_distance),
        'adjacent_euclidean_pair': None if adjacent_pair is None else list(adjacent_pair),
        'nonadjacent_euclidean_pair': None if other_pair is None else list(other_pair),
        'worst_pair': worst,
        'max_curve_deviation_m': _round(float(curve_error.max())),
        'arc_limit_ok': arc_limit_ok,
        'arc_box_ok': arc_box_ok,
        'euclidean_ok': euclidean_ok,
        'accepted_by_controller_rule': bool(arc_limit_ok and euclidean_ok),
        'pairwise': pairs,
        'local_curve': local_curve(boundary, points[global_index[0]], points[global_index[1]]),
    }


def _pair_record(points, sites, distances, index, count):
    i, j = (int(index[0]), int(index[1]))
    order = min(abs(i - j), count - abs(i - j))
    return {
        'i': i,
        'j': j,
        'adjacent': order == 1,
        'euclidean_m': _round(float(distances[i, j])),
        'arc_i_m': _round(float(sites[i])),
        'arc_j_m': _round(float(sites[j])),
        'position_i': np.round(points[i], 6).tolist(),
        'position_j': np.round(points[j], 6).tolist(),
    }


def local_curve(boundary, first, second, window=0.75):
    """Curve samples within ``window`` metres of arc from either conflict point."""
    length = float(boundary.length)
    samples = boundary.deployment
    arcs = np.r_[boundary.arc, length]
    ring = np.vstack([samples, samples[0]])
    keep = np.zeros(len(ring), dtype=bool)
    for point in (np.asarray(first, dtype=float), np.asarray(second, dtype=float)):
        nearest = int(np.argmin(np.linalg.norm(ring - point, axis=1)))
        origin = float(arcs[nearest])
        delta = (arcs - origin + length / 2) % length - length / 2
        keep |= np.abs(delta) <= window
    return np.round(ring[keep], 6).tolist()


def uniform_cost(boundary, sites, samples=2048):
    density = np.ones(len(boundary.deployment))
    quadrature, mass, length = quadrature_mass(boundary, density, samples)
    stats = coverage_statistics(quadrature, mass, np.asarray(sites, dtype=float), length, np.inf)
    return {
        'uniform_weighted_cost_m2': None if stats['weighted_cost'] is None else _round(stats['weighted_cost'], 8),
        'quadrature_samples': int(samples),
        'density': 'constant curve density, one per arc-length sample, before quadrature',
    }


def classify_geometry(curve_valid, certificate, attempts):
    """Separate invalid curves, count bounds, arc search and Euclidean conflict.

    A feasible target set is not an executed deployment. Path and safety stay
    unevaluated until a later stage says otherwise.
    """
    if not curve_valid:
        return 'curve_invalid'
    if certificate is not None:
        return 'count_necessary_violated'
    if any(item.get('planner_exception') for item in attempts):
        return 'planner_exception'
    if any(item.get('accepted_by_controller_rule') for item in attempts):
        return 'target_geometry_feasible'
    if any(item.get('arc_limit_ok') for item in attempts):
        return 'arc_pass_euclidean_fail'
    return 'no_arc_feasible_plan'


def termination_for(geometry_class):
    """Map a diagnostic class onto an existing termination word.

    Pure planning never uses SUCCESS. ``NOT_EXECUTED`` means a target set was
    accepted and then not tracked.
    """
    if geometry_class == 'curve_invalid':
        return 'BOUNDARY_INVALID', 'geometry_invalid'
    if geometry_class == 'count_necessary_violated':
        return 'PLAN_SEARCH_EXHAUSTED', 'certified_infeasible'
    if geometry_class == 'planner_exception':
        return 'PLAN_INVALID', 'experiment_error'
    if geometry_class == 'target_geometry_feasible':
        return 'NOT_EXECUTED', 'not_executed'
    if geometry_class in {'arc_pass_euclidean_fail', 'no_arc_feasible_plan'}:
        return 'PLAN_SEARCH_EXHAUSTED', 'no_plan_found'
    raise ValueError(f'Unknown geometry class: {geometry_class}')


def high_resolution_check(boundary, sites, gap_limit, separation, spacing=0.01, margin=0.02):
    """Recompute the same arc sites on a finer ring when a value is near a threshold.

    The official pass/fail is not replaced. A difference above ``margin`` is
    reported as numerical sensitivity.
    """
    official = pair_geometry(boundary, sites, gap_limit, separation)
    near = (
        abs(official['max_arc_gap_m'] - gap_limit) <= margin
        or abs(official['min_arc_gap_m'] - separation) <= margin
        or abs(official['min_euclidean_m'] - separation) <= margin
        or abs(official['seam_arc_gap_m'] - gap_limit) <= margin
    )
    if not near:
        return {'checked': False, 'reason': 'not_near_threshold'}
    fine_points = resample_arclength(boundary.deployment, spacing)
    from udc.boundary.prescribed import boundary_from_ring
    fine = boundary_from_ring(fine_points)
    # Keep the official sites as fractions of the original length, then scale
    # onto the fine ring's own length. Both rings describe the same shape.
    fraction = np.asarray(sites, dtype=float) / boundary.length
    checked = pair_geometry(fine, fraction * fine.length, gap_limit, separation)
    keys = ('max_arc_gap_m', 'min_arc_gap_m', 'seam_arc_gap_m', 'min_euclidean_m',
            'min_adjacent_euclidean_m', 'min_nonadjacent_euclidean_m')
    delta = {key: _round(abs(official[key] - checked[key]), 8) for key in keys}
    curve_shift = hausdorff(boundary.deployment, fine_points)
    return {
        'checked': True,
        'spacing_m': float(spacing),
        'official_pass': official['accepted_by_controller_rule'],
        'fine_pass': checked['accepted_by_controller_rule'],
        'pass_changed': official['accepted_by_controller_rule'] != checked['accepted_by_controller_rule'],
        'absolute_delta_m': delta,
        'curve_hausdorff_m': _round(curve_shift, 8),
        'official_result_retained': True,
    }


def projection_trace(boundary, plan):
    """Copy projection bookkeeping that the constrained planner already stored."""
    adjustments = list(getattr(plan, 'adjustments', ()) or ())
    if not adjustments:
        return {'projection': 'not_applicable', 'attempts': []}
    traces = []
    for item in adjustments:
        before = item.get('before_projection')
        after = item.get('after_projection')
        traces.append({
            'concentration': item.get('concentration'),
            'exit_reason': item.get('exit_reason'),
            'iterations': item.get('iterations'),
            'accepted': item.get('accepted'),
            'gap_projection': item.get('gap_projection'),
            'before': None if before is None else _trace_sites(boundary, before),
            'after': None if after is None else _trace_sites(boundary, after),
            'projection_shift_m': item.get('projection_shift'),
        })
    return {'projection': traces[-1]['gap_projection'], 'attempts': traces}


def _trace_sites(boundary, report):
    sites = np.asarray(report['arc_positions'], dtype=float)
    points = _interpolate(boundary, sites)
    return {
        'arc_positions': [_round(value) for value in sites],
        'positions': np.round(points, 6).tolist(),
        'max_gap_m': _round(report['max_gap']),
        'min_gap_m': _round(report['min_gap']),
        'min_separation_m': _round(report['min_separation']),
        'gap_violation_m': _round(report['gap_violation']),
        'separation_violation_m': _round(report['separation_violation']),
    }


def unevaluated_execution():
    """Explicit missing values. Zero would look like a measured clearance."""
    return {
        'people_evaluated': False,
        'walls_evaluated': False,
        'path_evaluated': False,
        'execution_evaluated': False,
        'path_length_m': None,
        'minimum_net_clearances_m': None,
        'safety_violation_count': None,
        'deployment_success': False,
    }
