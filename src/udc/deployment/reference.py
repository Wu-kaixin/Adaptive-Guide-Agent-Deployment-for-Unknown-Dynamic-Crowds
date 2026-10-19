"""Multi-start numerical reference for the same fixed-count curve problem.

This is a deployment-planning reference. It does not assign guides, route
around the crowd, or apply the safety filter, so a better objective value is
not a certificate that the full task is better or optimal.
"""
from time import perf_counter

import numpy as np
from scipy.optimize import minimize

from .planners import (_interpolate, coverage_statistics, demand_profile, periodic_gaps,
                       project_gaps, quadrature_mass)


def _sites(phase, gaps, length):
    return (float(phase) + np.r_[0.0, np.cumsum(gaps[:-1])]) % length


def _objective_parts(boundary, quadrature, mass, length, phase, gaps):
    sites = _sites(phase, gaps, length)
    stats = coverage_statistics(quadrature, mass, sites, length, np.inf)
    return sites, stats['weighted_cost']


def independent_feasibility(boundary, sites, gap_limit, min_separation, tolerance=1e-6):
    """Check arc-gap bounds and every pairwise Euclidean distance, including non-neighbours."""
    gaps = periodic_gaps(np.sort(np.asarray(sites, dtype=float) % boundary.length), boundary.length)
    points = _interpolate(boundary, np.sort(np.asarray(sites, dtype=float) % boundary.length))
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    gap_violation = float(max(0.0, gaps.max() - gap_limit, min_separation - gaps.min()))
    separation_violation = float(max(0.0, min_separation - distances.min()))
    return {
        'feasible': bool(gap_violation <= tolerance and separation_violation <= tolerance),
        'max_gap': float(gaps.max()),
        'min_gap': float(gaps.min()),
        'min_euclidean': float(distances.min()),
        'gap_violation': gap_violation,
        'separation_violation': separation_violation,
        'sum_gap_error': float(abs(gaps.sum() - boundary.length)),
    }


def _evaluate_start(boundary, quadrature, mass, length, phase, gaps, gap_limit, min_separation):
    gaps = np.asarray(gaps, dtype=float)
    projected = project_gaps(gaps, min_separation, gap_limit, length)
    if projected is None:
        sites = _sites(phase, gaps, length)
        _, cost = _objective_parts(boundary, quadrature, mass, length, phase, gaps)
        return {
            'phase': float(phase % length), 'gaps': gaps.tolist(), 'cost': cost,
            'feasible': False, 'projection': 'infeasible_box', **independent_feasibility(
                boundary, sites, gap_limit, min_separation),
        }
    sites, cost = _objective_parts(boundary, quadrature, mass, length, phase, projected)
    return {
        'phase': float(phase % length), 'gaps': projected.tolist(), 'cost': cost,
        'projection': 'applied', **independent_feasibility(boundary, sites, gap_limit, min_separation),
    }


def optimize_reference(boundary, people, demand, count, bandwidth, gap_limit, min_separation,
                       starts, samples=2048, maxiter=100):
    """Minimise the original demand-weighted arc cost from several feasible or recorded starts.

    ``starts`` are ``(name, phase, gaps)``. The absolute phase stays free. A
    start that is already feasible is retained even when the iteration does not
    improve it. Exhausting ``maxiter`` is not an infeasibility certificate.
    """
    if count < 3:
        raise ValueError('At least three targets are required')
    rho = demand_profile(boundary, np.asarray(people, dtype=float), np.asarray(demand, dtype=float),
                         bandwidth, 'mass_cvt')
    quadrature, mass, length = quadrature_mass(boundary, rho, int(samples))
    pair_index = [(i, j) for i in range(count) for j in range(i + 1, count)]

    def unpack(values):
        phase = values[0]
        gaps = np.asarray(values[1:], dtype=float)
        return phase, gaps

    def objective(values):
        phase, gaps = unpack(values)
        return _objective_parts(boundary, quadrature, mass, length, phase, gaps)[1]

    def euclidean_constraint(values, pair):
        phase, gaps = unpack(values)
        points = _interpolate(boundary, _sites(phase, gaps, length))
        return float(np.linalg.norm(points[pair[0]] - points[pair[1]]) - min_separation)

    constraints = [{'type': 'eq', 'fun': lambda values: float(np.sum(values[1:]) - length)}]
    constraints.extend(
        {'type': 'ineq', 'fun': euclidean_constraint, 'args': (pair,)} for pair in pair_index)
    bounds = [(0.0, length)] + [(min_separation, gap_limit)] * count
    attempts = []
    best = None
    for name, phase, gaps in starts:
        start = _evaluate_start(boundary, quadrature, mass, length, phase, gaps, gap_limit, min_separation)
        initial = np.r_[start['phase'], start['gaps']]
        started = perf_counter()
        result = minimize(
            objective, initial, method='SLSQP', bounds=bounds, constraints=constraints,
            options={'maxiter': int(maxiter), 'ftol': 1e-10, 'disp': False})
        elapsed = perf_counter() - started
        phase_out, gaps_out = unpack(result.x)
        sites, cost = _objective_parts(boundary, quadrature, mass, length, phase_out, gaps_out)
        checked = independent_feasibility(boundary, sites, gap_limit, min_separation)
        record = {
            'start': name,
            'start_cost': start['cost'],
            'start_feasible': start['feasible'],
            'solver_success': bool(result.success),
            'solver_message': str(result.message),
            'solver_iterations': int(result.nit),
            'seconds': elapsed,
            'cost': cost,
            'improved_on_start': bool(start['feasible'] and checked['feasible'] and cost < start['cost'] - 1e-8),
            'kept_start': bool(start['feasible'] and (not checked['feasible'] or cost > start['cost'] + 1e-8)),
            **checked,
        }
        attempts.append(record)
        candidates = []
        if checked['feasible']:
            candidates.append((cost, np.sort(sites % length), 'solver'))
        if start['feasible']:
            candidates.append((start['cost'], np.sort(_sites(start['phase'], np.asarray(start['gaps']), length) % length), 'feasible_start'))
        if not candidates:
            continue
        chosen_cost, chosen_sites, chosen_from = min(candidates, key=lambda item: item[0])
        if best is None or chosen_cost < best['cost'] - 1e-12:
            best = {**record, 'cost': chosen_cost, 'selected': chosen_from,
                    'positions': _interpolate(boundary, chosen_sites),
                    'arc_positions': chosen_sites}
    return {
        'samples': int(samples),
        'maxiter': int(maxiter),
        'objective': 'original mass_cvt gaussian demand, mean squared arc distance (m^2), lower is better',
        'scope': 'deployment targets only; assignment, navigation and safety filtering are not included',
        'global_optimum': False,
        'attempts': attempts,
        'best_cost': None if best is None else best['cost'],
        'best_feasible': best is not None,
        'best_positions': None if best is None else best['positions'],
        'best_arc_positions': None if best is None else best['arc_positions'],
    }
