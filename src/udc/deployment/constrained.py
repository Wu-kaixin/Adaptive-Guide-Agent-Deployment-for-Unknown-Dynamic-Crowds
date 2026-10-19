"""Phase alignment and pairwise Euclidean placement on one closed curve.

The objective is the same demand-weighted mean squared arc distance used by
Mass CVT and ``projected_mass``. Arc gaps stay inside ``[d_min, H_p]``.
Every unordered pair, including pairs that are not neighbours on the curve,
must be at least ``d_min`` apart in the plane. A solver flag is not the
acceptance decision, and using up the iteration budget is not an infeasibility
certificate.
"""
from __future__ import annotations

from time import perf_counter

import numpy as np
from scipy.optimize import minimize

from .geometry import pair_geometry
from .planners import Plan, _interpolate, coverage_statistics, periodic_gaps, project_gaps, quadrature_mass


def planning_outcome(independent_feasible, count_certificate):
    """Separate a feasible witness from a missed search and from a count certificate.

    ``solver_success`` is deliberately not an input. A local solver can report
    success on a point that fails the independent pair check, and it can stop
    on the iteration limit at a point that still passes that check.
    """
    if count_certificate is not None:
        return 'certified_infeasible'
    if independent_feasible:
        return 'feasible_witness'
    return 'no_plan_found'


def rotate_sites(sites, phase, length):
    """Move every site by the same arc length. Order is recovered by sorting.

    Sorting identifies positions along the curve. It is not an assignment of
    those positions to guides.
    """
    return np.sort((np.asarray(sites, dtype=float) + float(phase)) % float(length))


def _cost(quadrature, mass, sites, length):
    stats = coverage_statistics(quadrature, mass, np.asarray(sites, dtype=float), length, np.inf)
    return None if stats['weighted_cost'] is None else float(stats['weighted_cost'])


def _sites_from_gaps(phase, gaps, length):
    gaps = np.asarray(gaps, dtype=float)
    return np.sort((float(phase) + np.r_[0.0, np.cumsum(gaps[:-1])]) % float(length))


def _independent(boundary, sites, gap_limit, separation):
    geometry = pair_geometry(boundary, sites, gap_limit, separation)
    accepted = bool(geometry['arc_box_ok'] and geometry['euclidean_ok'])
    return geometry, accepted


def _attempt_record(name, solver_status, solver_success, iterations, seconds, message,
                    cost_before, cost_after, accepted, geometry, separation):
    return {
        'start': name,
        'solver_status': solver_status,
        'solver_success': bool(solver_success),
        'solver_iterations': int(iterations),
        'solver_seconds': float(seconds),
        'solver_message': message,
        'cost_before': cost_before,
        'cost_after': cost_after,
        'accepted': bool(accepted),
        'outcome': planning_outcome(accepted, None),
        'global_optimum': False,
        'min_arc_gap_m': geometry['min_arc_gap_m'],
        'max_arc_gap_m': geometry['max_arc_gap_m'],
        'seam_arc_gap_m': geometry['seam_arc_gap_m'],
        'sum_arc_gap_error_m': geometry['sum_arc_gap_error_m'],
        'min_euclidean_m': geometry['min_euclidean_m'],
        'min_adjacent_euclidean_m': geometry['min_adjacent_euclidean_m'],
        'min_nonadjacent_euclidean_m': geometry['min_nonadjacent_euclidean_m'],
        'worst_pair': geometry['worst_pair'],
        'arc_box_ok': geometry['arc_box_ok'],
        'euclidean_ok': geometry['euclidean_ok'],
        'pair_count': len(geometry['pairwise']),
        'all_pairs_checked': True,
        'gap_residuals_m': [
            None if item['arc_gap_m'] is None else float(item['arc_gap_m'])
            for item in geometry['pairwise'] if item['adjacent']
        ],
        'pair_residuals_m': [
            {
                'i': item['i'], 'j': item['j'], 'adjacent': item['adjacent'],
                'euclidean_m': item['euclidean_m'],
                'violation_m': None if item['euclidean_m'] is None else float(
                    max(0.0, separation - item['euclidean_m'])
                ),
            }
            for item in geometry['pairwise']
        ],
    }


def _with_violations(record):
    record['max_euclidean_violation_m'] = float(max(
        (item['violation_m'] or 0.0) for item in record['pair_residuals_m']
    )) if record['pair_residuals_m'] else 0.0
    return record


class _Budget:
    def __init__(self, seconds):
        self.seconds = float(seconds)
        self.started = perf_counter()
        self.iterations = 0
        self.last = None

    def callback(self, values):
        self.iterations += 1
        self.last = np.asarray(values, dtype=float).copy()
        if perf_counter() - self.started > self.seconds:
            raise TimeoutError('solver wall-clock budget exhausted')


def _minimize(objective, initial, bounds, constraints, maxiter, timeout):
    budget = _Budget(timeout)
    try:
        result = minimize(
            objective, np.asarray(initial, dtype=float), method='SLSQP',
            bounds=bounds, constraints=constraints, callback=budget.callback,
            options={'maxiter': int(maxiter), 'ftol': 1e-10, 'disp': False},
        )
    except TimeoutError:
        return {
            'status': 'timeout', 'success': False, 'iterations': budget.iterations,
            'message': 'solver wall-clock budget exhausted',
            'x': initial if budget.last is None else budget.last,
        }
    except (ValueError, FloatingPointError, np.linalg.LinAlgError) as error:
        return {
            'status': 'solver_exception', 'success': False, 'iterations': budget.iterations,
            'message': str(error), 'x': initial if budget.last is None else budget.last,
        }
    status = 'iteration_limit' if int(result.nit) >= int(maxiter) and not result.success else (
        'success' if result.success else 'solver_stopped')
    return {
        'status': status, 'success': bool(result.success), 'iterations': int(result.nit),
        'message': str(result.message), 'x': np.asarray(result.x, dtype=float),
    }


def _select(candidates):
    """Lowest demand cost among independently feasible candidates. None if there are none."""
    feasible = [item for item in candidates if item['accepted']]
    if not feasible:
        return None
    return min(feasible, key=lambda item: (item['cost_after'], item['start']))


def optimize_phase(boundary, base_sites, quadrature, mass, start_phases,
                   gap_limit, separation, maxiter, timeout):
    """Keep the projected gap pattern and move only the common arc phase.

    The demand objective is unchanged. On a constant density it does not depend
    on phase, so a feasible start and a feasible local solution have the same cost.
    """
    length = float(boundary.length)
    base = np.sort(np.asarray(base_sites, dtype=float) % length)
    base_gaps = periodic_gaps(base, length)
    pair_index = [(i, j) for i in range(len(base)) for j in range(i + 1, len(base))]

    def sites_of(phase):
        return rotate_sites(base, phase, length)

    def objective(values):
        return _cost(quadrature, mass, sites_of(values[0]), length)

    def separation_constraint(values, pair):
        points = _interpolate(boundary, sites_of(values[0]))
        return float(np.linalg.norm(points[pair[0]] - points[pair[1]]) - separation)

    constraints = [
        {'type': 'ineq', 'fun': separation_constraint, 'args': (pair,)}
        for pair in pair_index
    ]
    attempts = []
    candidates = []
    for phase in start_phases:
        initial_sites = sites_of(phase)
        before = _cost(quadrature, mass, initial_sites, length)
        started = perf_counter()
        solved = _minimize(
            objective, [float(phase) % length], [(0.0, length)], constraints, maxiter, timeout,
        )
        elapsed = perf_counter() - started
        final_sites = sites_of(solved['x'][0])
        after = _cost(quadrature, mass, final_sites, length)
        geometry, accepted = _independent(boundary, final_sites, gap_limit, separation)
        record = _with_violations(_attempt_record(
            f'phase_{float(phase) / length:.2f}L', solved['status'], solved['success'],
            solved['iterations'], elapsed, solved['message'], before, after, accepted, geometry,
            separation,
        ))
        record['frozen_gap_pattern'] = True
        record['phase_m'] = float(solved['x'][0] % length)
        record['arc_positions'] = final_sites.tolist()
        record['gap_sum_error_of_frozen_pattern_m'] = float(abs(base_gaps.sum() - length))
        attempts.append(record)
        start_geometry, start_ok = _independent(boundary, initial_sites, gap_limit, separation)
        if start_ok:
            candidates.append({
                'accepted': True, 'cost_after': before, 'start': record['start'] + '_start',
                'sites': initial_sites, 'geometry': start_geometry,
                'iterations': 0, 'status': 'feasible_start',
            })
        if accepted:
            candidates.append({
                'accepted': True, 'cost_after': after, 'start': record['start'],
                'sites': final_sites, 'geometry': geometry,
                'iterations': solved['iterations'], 'status': solved['status'],
            })
    chosen = _select(candidates)
    return _bundle(
        boundary, 'projected_mass_phase', attempts, chosen, quadrature, mass,
        'projected_mass gaps held fixed; only the common phase is free',
    )


def optimize_pairwise(boundary, starts, quadrature, mass, gap_limit, separation,
                      maxiter, timeout):
    """Free the phase and the arc gaps, with every pair constrained in the plane.

    ``starts`` are ``(name, sites)``. Gap bounds stay ``[d_min, H_p]``. The
    demand weights are not changed. No concentration schedule and no Equal Arc
    fallback run inside this search.
    """
    length = float(boundary.length)
    count = len(np.asarray(starts[0][1]))
    pair_index = [(i, j) for i in range(count) for j in range(i + 1, count)]

    def unpack(values):
        return float(values[0]), np.asarray(values[1:], dtype=float)

    def objective(values):
        phase, gaps = unpack(values)
        return _cost(quadrature, mass, _sites_from_gaps(phase, gaps, length), length)

    def separation_constraint(values, pair):
        phase, gaps = unpack(values)
        points = _interpolate(boundary, _sites_from_gaps(phase, gaps, length))
        return float(np.linalg.norm(points[pair[0]] - points[pair[1]]) - separation)

    constraints = [{'type': 'eq', 'fun': lambda values: float(np.sum(values[1:]) - length)}]
    constraints.extend(
        {'type': 'ineq', 'fun': separation_constraint, 'args': (pair,)} for pair in pair_index
    )
    bounds = [(0.0, length)] + [(float(separation), float(gap_limit))] * count
    attempts = []
    candidates = []
    for name, sites in starts:
        sites = np.sort(np.asarray(sites, dtype=float) % length)
        raw_gaps = periodic_gaps(sites, length)
        before = _cost(quadrature, mass, sites, length)
        projected = project_gaps(raw_gaps, separation, gap_limit, length)
        if projected is None:
            geometry, accepted = _independent(boundary, sites, gap_limit, separation)
            record = _with_violations(_attempt_record(
                name, 'infeasible_gap_box', False, 0, 0.0,
                'gap box is empty for this count', before, before, False, geometry, separation,
            ))
            record['arc_positions'] = sites.tolist()
            attempts.append(record)
            continue
        projected_sites = _sites_from_gaps(sites[0], projected, length)
        projected_cost = _cost(quadrature, mass, projected_sites, length)
        initial = np.r_[float(projected_sites[0]), projected]
        started = perf_counter()
        solved = _minimize(objective, initial, bounds, constraints, maxiter, timeout)
        elapsed = perf_counter() - started
        phase, gaps = unpack(solved['x'])
        final_sites = _sites_from_gaps(phase, gaps, length)
        after = _cost(quadrature, mass, final_sites, length)
        geometry, accepted = _independent(boundary, final_sites, gap_limit, separation)
        record = _with_violations(_attempt_record(
            name, solved['status'], solved['success'], solved['iterations'], elapsed,
            solved['message'], before, after, accepted, geometry, separation,
        ))
        record['cost_after_gap_projection'] = projected_cost
        record['arc_positions'] = final_sites.tolist()
        record['frozen_gap_pattern'] = False
        attempts.append(record)
        start_geometry, start_ok = _independent(boundary, sites, gap_limit, separation)
        if start_ok:
            candidates.append({
                'accepted': True, 'cost_after': before, 'start': name + '_start',
                'sites': sites, 'geometry': start_geometry, 'iterations': 0,
                'status': 'feasible_start',
            })
        if accepted:
            candidates.append({
                'accepted': True, 'cost_after': after, 'start': name,
                'sites': final_sites, 'geometry': geometry,
                'iterations': solved['iterations'], 'status': solved['status'],
            })
    return _bundle(
        boundary, 'pairwise_euclidean', attempts, _select(candidates), quadrature, mass,
        'same demand objective; arc-gap box retained; every pair has a Euclidean constraint',
    )


def _bundle(boundary, method, attempts, chosen, quadrature, mass, description):
    plan = None
    if chosen is not None:
        sites = np.sort(np.asarray(chosen['sites'], dtype=float) % boundary.length)
        plan = Plan(
            _interpolate(boundary, sites), sites,
            np.array([chosen['cost_after']], dtype=float),
            float(periodic_gaps(sites, boundary.length).max()),
            method, int(chosen['iterations']), str(chosen['status']), float(mass.sum()),
        )
    best_cost = None if plan is None else float(plan.cost[0])
    return {
        'method': method,
        'objective': (
            'demand-weighted mean squared arc distance on the supplied curve density; '
            'lower is better; weights are not rescaled inside the solver'
        ),
        'description': description,
        'quadrature_samples': int(len(quadrature)),
        'global_optimum': False,
        'accepted': plan is not None,
        'outcome': planning_outcome(plan is not None, None),
        'best_cost': best_cost,
        'selected_start': None if chosen is None else chosen['start'],
        'attempts': attempts,
        'plan': plan,
        'unevaluated': {
            'people_clearance': False,
            'wall_clearance': False,
            'path': False,
            'execution': False,
            'safety_filter': False,
        },
    }


def constant_quadrature(boundary, samples):
    """Uniform curve density. This is not the Gaussian image of unit person weights."""
    quadrature, mass, length = quadrature_mass(boundary, np.ones(len(boundary.deployment)), int(samples))
    return quadrature, mass, length
