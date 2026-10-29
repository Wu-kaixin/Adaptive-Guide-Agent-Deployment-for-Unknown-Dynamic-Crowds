"""Quantitative loss enclosures for EXACT observations only.

The reference is still the original construction. Nonzero observation error
is explicitly unsupported: this does not solve the unknown-curve mass bound.
Disk tests and dyadic integrals use exact binary-rational inputs; Euclidean
segment lengths have rational square-root brackets. GEOS/Gaussian construction
and the separate float64 evaluator are not thereby formally verified.
"""
from fractions import Fraction as F
import heapq
import math

import numpy as np

from udc.deployment.planners import demand_profile
from .geometry import construct_groups


def rational(value):
    return F.from_float(float(value))


def _sqrt_bounds(value, bits=96):
    denominator = 1 << bits
    scaled = value.numerator * denominator * denominator
    n = math.isqrt(scaled // value.denominator)
    lower = F(n, denominator)
    return lower, lower if n * n * value.denominator == scaled else F(n + 1, denominator)


def _float_bound(value, upward):
    result = float(value)
    if (rational(result) < value if upward else rational(result) > value):
        result = math.nextafter(result, math.inf if upward else -math.inf)
    return result


def _coverage(p, q, robots, radius_squared):
    """Lower/upper indicator bounds, almost everywhere on a nonzero segment."""
    direction = tuple(b - a for a, b in zip(p, q))
    norm2 = sum(v * v for v in direction)
    possible = False
    for center in robots:
        relative = tuple(a - b for a, b in zip(p, center))
        first = sum(v * v for v in relative)
        second = sum((a - b) ** 2 for a, b in zip(q, center))
        if max(first, second) <= radius_squared:
            return 1, 1  # Convex squared distance: endpoints suffice.
        dot = sum(a * b for a, b in zip(relative, direction))
        minimum = first if dot >= 0 else second if -dot >= norm2 else first - dot * dot / norm2
        # Tangency contributes zero arc mass; a nonzero line cannot lie along
        # a circle. Equality may therefore be excluded almost everywhere.
        possible |= minimum < radius_squared
    return 0, int(possible)


def polyline_loss_bound(boundary, density, retained, greedy, radius, delta=.002,
                        tracking_bound=1e-10, max_cells=4096, max_depth=18):
    points, rho = np.asarray(boundary.deployment, float), np.asarray(density, float)
    retained, greedy = (np.asarray(q, float).reshape(-1, 2) for q in (retained, greedy))
    if (points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or rho.shape != (len(points),)
            or not all(np.isfinite(a).all() for a in (points, rho, retained, greedy)) or np.any(rho < 0)
            or not np.isfinite([radius, delta, tracking_bound]).all() or radius <= tracking_bound
            or tracking_bound < 0 or not 0 <= delta <= 1 or type(max_cells) is not int
            or max_cells < len(points) or type(max_depth) is not int or max_depth < 0):
        raise ValueError('Invalid finite exact-polyline certificate model or subdivision budget')
    p = [tuple(rational(v) for v in point) for point in points]
    weights = [rational(v) for v in rho]
    robots = [[tuple(rational(v) for v in point) for point in q] for q in (greedy, retained)]
    radii = [rational(radius) ** 2, (rational(radius) - rational(tracking_bound)) ** 2]
    mass_low, mass_high, upper, lower = F(0), F(0), F(0), F(0)
    queue, serial, leaves, evaluations = [], 0, len(points), 0

    def cell(index, a, b, r0, r1, width, depth, length_bounds):
        nonlocal serial, evaluations
        evaluations += 1
        g0, g1 = _coverage(a, b, robots[0], radii[0])
        r0_cover, r1_cover = _coverage(a, b, robots[1], radii[1])
        lo, hi = g0 - r1_cover, g1 - r0_cover
        coefficient = (r0 + r1) * width / 2
        mass_l, mass_h = coefficient * length_bounds[0], coefficient * length_bounds[1]
        low_value = lo * (mass_l if lo >= 0 else mass_h)
        high_value = hi * (mass_h if hi >= 0 else mass_l)
        if lo != hi and depth < max_depth:
            serial += 1
            heapq.heappush(queue, (-(high_value - low_value), serial,
                (index, a, b, r0, r1, width, depth, length_bounds, low_value, high_value)))
        return low_value, high_value

    for i, first in enumerate(p):
        second = p[(i + 1) % len(p)]
        norm2 = sum((a - b) ** 2 for a, b in zip(first, second))
        if norm2 <= 0:
            raise ValueError('Reference contains a zero-length segment')
        length_bounds = _sqrt_bounds(norm2)
        coefficient = (weights[i] + weights[(i + 1) % len(p)]) / 2
        mass_low += coefficient * length_bounds[0]
        mass_high += coefficient * length_bounds[1]
        lo, hi = cell(i, first, second, weights[i], weights[(i + 1) % len(p)], F(1), 0, length_bounds)
        lower += lo
        upper += hi
    if mass_low <= 0:
        raise ValueError('No positive certified total reference mass')

    def bounds():
        low = lower / (mass_high if lower >= 0 else mass_low)
        high = upper / (mass_low if upper >= 0 else mass_high)
        return max(F(-1), low), min(F(1), high)

    threshold = rational(delta)
    lo, hi = bounds()
    while hi > threshold and lo <= threshold and queue and leaves < max_cells:
        _, _, data = heapq.heappop(queue)
        i, a, b, r0, r1, width, depth, lengths, old_low, old_high = data
        midpoint = tuple((x + y) / 2 for x, y in zip(a, b))
        middle_rho = (r0 + r1) / 2
        lower -= old_low
        upper -= old_high
        for first, second, w0, w1 in ((a, midpoint, r0, middle_rho), (midpoint, b, middle_rho, r1)):
            low_value, high_value = cell(i, first, second, w0, w1, width / 2, depth + 1, lengths)
            lower += low_value
            upper += high_value
        leaves += 1
        lo, hi = bounds()
    positive_upper = max(F(0), hi)
    accept = positive_upper <= threshold
    return {'accept_retained': accept, 'upper_loss': _float_bound(positive_upper, True),
        'signed_loss_interval': [_float_bound(lo, False), _float_bound(hi, True)],
        'exact_positive_upper_fraction': str(positive_upper), 'exact_delta_fraction': str(threshold),
        'leaf_cells': leaves, 'cell_evaluations': evaluations, 'max_cells': max_cells, 'max_depth': max_depth,
        'reason': 'exact_curve_signed_mass_bound' if accept else
            'conservative_shrunken_service_exceeds_budget' if lo > threshold else 'finite_subdivision_budget_exhausted',
        'geometry_scope': 'exact rational disk tests on supplied binary-coordinate polyline, linear nodal demand and rational length brackets',
        'source_geometry_and_gaussian_construction_formally_verified': False}


def exact_observation_certificate(measurement, room, config, retained, greedy, delta=.002,
                                  tracking_bound=1e-10, max_cells=4096, max_depth=18):
    if measurement.position_error_bound_m != 0.:
        return {'status': 'UNCERTIFIED', 'accept_retained': False,
            'reason': 'nonzero_observation_error_original_reference_unknown', 'groups': []}
    groups, _ = construct_groups(measurement, room, config)
    obs = measurement.observation
    rows = []
    for group in groups:
        index = list(group.rows)
        density = demand_profile(group.boundary, obs.positions[index], obs.demand[index], config['bandwidth_m'])
        rows.append(polyline_loss_bound(group.boundary, density, retained, greedy, config['max_gap_m'] / 2,
            delta, tracking_bound, max_cells, max_depth))
    accept = all(row['accept_retained'] for row in rows)
    return {'status': 'EXACT_OBSERVATION_RATIONAL_SERVICE' if accept else 'UNCERTIFIED',
        'accept_retained': accept, 'groups': rows, 'delta': delta, 'tracking_bound_m': tracking_bound,
        'reason': 'quantitative_exact_observation_only' if accept else 'exact_curve_bound_not_within_budget',
        'position_error_bound_m': 0., 'reference': 'original_world_reference_reconstructed_from_exact_observation',
        'scope': 'exact sensing only; retained actual coverage lower bounded by radius-minus-tracking; greedy planned baseline unchanged',
        'general_nonzero_error_certificate': False}
