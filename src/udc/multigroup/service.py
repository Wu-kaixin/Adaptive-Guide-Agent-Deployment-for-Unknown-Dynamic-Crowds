"""Continuous service of a declared polyline and piecewise-linear density.

Analytic interval construction in ordinary float64, not interval arithmetic.
These utilities never assert a correspondence between noisy and true curves.
"""
import numpy as np


def _union(intervals):
    merged = []
    for low, high in sorted(intervals):
        if high <= low:
            continue
        if merged and low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([float(low), float(high)])
    return merged


def _model(boundary, density):
    points = np.asarray(boundary.deployment, dtype=float)
    rho = np.asarray(density, dtype=float)
    lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    if (len(points) < 3 or rho.shape != (len(points),) or not np.isfinite(points).all()
            or not np.isfinite(rho).all() or np.any(rho < 0) or np.any(lengths <= 0)):
        raise ValueError('Need a finite closed polyline and nonnegative nodal density')
    total = float(np.sum(lengths * (rho + np.roll(rho, -1)) / 2))
    if total <= 0:
        raise ValueError('Density must have positive integral')
    return points, rho, lengths, total


def continuous_euclidean_service(boundary, density, robots, radius):
    """Integrate service disk unions on each complete straight curve segment."""
    points, rho, lengths, total = _model(boundary, density)
    robots = np.asarray(robots, dtype=float).reshape(-1, 2)
    if not np.isfinite(robots).all() or not np.isfinite(radius) or radius < 0:
        raise ValueError('Finite robots and nonnegative radius required')
    covered, covered_length, full, interval_count = 0., 0., True, 0
    for i, (point, length) in enumerate(zip(points, lengths)):
        direction = points[(i + 1) % len(points)] - point
        relative = point - robots
        a = length * length
        b = 2 * (relative @ direction)
        c = np.sum(relative * relative, axis=1) - radius * radius
        discriminant = b * b - 4 * a * c
        valid = discriminant >= 0
        root = np.sqrt(np.maximum(discriminant[valid], 0))
        low = np.maximum(0, (-b[valid] - root) / (2 * a))
        high = np.minimum(1, (-b[valid] + root) / (2 * a))
        intervals = _union(zip(low, high))
        full = full and len(intervals) == 1 and intervals[0][0] == 0 and intervals[0][1] == 1
        interval_count += len(intervals)
        for left, right in intervals:
            covered += length * (rho[i] * (right - left)
                                 + (rho[(i + 1) % len(rho)] - rho[i]) * (right ** 2 - left ** 2) / 2)
            covered_length += length * (right - left)
    return {'uncovered': float(np.clip(1 - covered / total, 0, 1)),
            'uncovered_length_m': float(max(0, lengths.sum() - covered_length)),
            'full_curve_covered': bool(full), 'covered_intervals': interval_count,
            'model': 'closed_polyline_piecewise_linear_density_float64'}


def continuous_arc_service(boundary, density, sites, radius):
    """Integrate periodic arc-radius intervals without quadrature sampling."""
    _, rho, lengths, total = _model(boundary, density)
    length = float(lengths.sum())
    sites = np.asarray(sites, dtype=float)
    if not np.isfinite(sites).all() or not np.isfinite(radius) or radius < 0:
        raise ValueError('Finite sites and nonnegative radius required')
    if len(sites) and 2 * radius >= length:
        return {'uncovered': 0., 'uncovered_length_m': 0., 'full_curve_covered': True}
    intervals = []
    for site in sites % length:
        for shift in (-length, 0., length):
            intervals.append((max(0, site - radius + shift), min(length, site + radius + shift)))
    intervals = _union(intervals)
    knots = np.r_[0., np.cumsum(lengths)]
    rho_next = np.roll(rho, -1)
    integrals = lengths * (rho + rho_next) / 2
    prefix = np.r_[0., np.cumsum(integrals)]

    def primitive(s):
        i = min(int(np.searchsorted(knots, s, side='right') - 1), len(rho) - 1)
        fraction = (s - knots[i]) / lengths[i]
        return prefix[i] + lengths[i] * (rho[i] * fraction + (rho_next[i] - rho[i]) * fraction ** 2 / 2)

    covered = sum(primitive(right) - primitive(left) for left, right in intervals)
    return {'uncovered': float(np.clip(1 - covered / total, 0, 1)),
            'uncovered_length_m': float(max(0, length - sum(right - left for left, right in intervals))),
            'full_curve_covered': bool(len(intervals) == 1 and intervals[0] == [0., length])}


def continuous_service_exchange(boundary, density, greedy, retained, radius):
    """Integrate lost AND gained service on a declared fixed original polyline.

    Evaluator diagnostic only: this needs the reference curve and its nodal
    density. It is not an observation-only uncertainty certificate.
    """
    points, rho, lengths, total = _model(boundary, density)
    first, second = (np.asarray(q, float).reshape(-1, 2) for q in (greedy, retained))
    if not np.isfinite(radius) or radius < 0 or not np.isfinite(first).all() or not np.isfinite(second).all():
        raise ValueError('Finite service layouts and nonnegative radius required')

    def covered(point, direction, length, robots):
        relative = point - robots
        a = length * length
        b = 2 * (relative @ direction)
        c = np.sum(relative * relative, axis=1) - radius * radius
        discriminant = b * b - 4 * a * c
        valid = discriminant >= 0
        roots = np.sqrt(np.maximum(discriminant[valid], 0))
        low = np.maximum(0., (-b[valid] - roots) / (2 * a))
        high = np.minimum(1., (-b[valid] + roots) / (2 * a))
        return _union(zip(low, high))

    def subtract(left, right):
        result = []
        for low, high in left:
            cursor = low
            for a, b in right:
                if b <= cursor:
                    continue
                if a >= high:
                    break
                if a > cursor:
                    result.append((cursor, min(a, high)))
                cursor = max(cursor, b)
                if cursor >= high:
                    break
            if cursor < high:
                result.append((cursor, high))
        return result

    lost, gained = 0., 0.
    for index, (point, length) in enumerate(zip(points, lengths)):
        direction = points[(index + 1) % len(points)] - point
        a = covered(point, direction, length, first)
        b = covered(point, direction, length, second)
        def mass(intervals):
            return sum(length * (rho[index] * (high - low)
                + (rho[(index + 1) % len(rho)] - rho[index]) * (high ** 2 - low ** 2) / 2)
                for low, high in intervals)
        lost += mass(subtract(a, b))
        gained += mass(subtract(b, a))
    return {'lost_mass_fraction': float(np.clip(lost / total, 0., 1.)),
            'gained_mass_fraction': float(np.clip(gained / total, 0., 1.)),
            'net_uncovered_difference': float((lost - gained) / total),
            'scope': 'fixed_declared_polyline_and_piecewise_linear_density_evaluator_only',
            'uncertainty_certificate': False}
