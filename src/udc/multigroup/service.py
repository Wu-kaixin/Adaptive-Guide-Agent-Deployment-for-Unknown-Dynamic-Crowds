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
