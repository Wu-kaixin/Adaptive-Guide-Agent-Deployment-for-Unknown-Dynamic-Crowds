"""Constrained periodic CVT used by two labelled methods.

Both methods minimise the same fixed-count objective on a closed curve,

    J(s) = sum_q w_q rho_q^a * d_L(s_q, S)^2,

with Gaussian demand mass ``rho`` (the Mass CVT density) and exponent ``a``.
Variables are arc sites. Constraints are consecutive arc gaps in
``[d_min, H_p]`` and Euclidean separation ``>= d_min``. Reachability is
checked later by the shared navigation layer.

* ``projected_mass`` fixes ``a = 1`` and applies the gap projection. It is the
  candidate checked on the convex ablation: demand-weighted placement with an
  explicit arc-gap constraint. It has no concentration schedule and no fallback.
* ``ra_abdc`` starts at ``a = 1``. If the projected plan still violates the
  Euclidean separation, ``a`` is halved and the attempt is repeated. ``a = 0``
  is the explicit Equal Arc fallback. That schedule did not run in the convex
  ablation, so it is not evidence of an extra capability.

The projection removes the Lloyd descent guarantee, so neither method claims
optimality. Evaluation always uses the original ``rho``, never ``rho^a``.
"""
from time import perf_counter

import numpy as np

from .planners import (Plan, _density, _interpolate, periodic_cells, periodic_gaps,
                       project_gaps, quadrature_mass)


def _sites_from_gaps(sites, gaps, length):
    cumulative = np.r_[0., np.cumsum(gaps[:-1])]
    shift = float(np.mean(sites - cumulative))
    return np.sort((shift + cumulative) % length)


def _min_separation(boundary, sites):
    points = _interpolate(boundary, sites)
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    return points, float(distances.min())


def _constraint_report(boundary, sites, gap_limit, min_separation):
    _, separation = _min_separation(boundary, sites)
    gaps = periodic_gaps(sites, boundary.length)
    max_gap = float(gaps.max())
    return {
        'arc_positions': np.asarray(sites, dtype=float).tolist(),
        'max_gap': max_gap,
        'min_gap': float(gaps.min()),
        'min_separation': separation,
        'gap_violation': float(max(0.0, max_gap - gap_limit)),
        'separation_violation': float(max(0.0, min_separation - separation)),
    }


def _subsample(values, count=64):
    values = np.asarray(values, dtype=float)
    index = np.linspace(0, len(values) - 1, min(count, len(values))).astype(int)
    return np.round(values[index], 6).tolist()


def _projected_lloyd(boundary, density, count, phase, samples, max_iterations,
                     gap_limit, min_separation):
    """Lloyd steps on ``density`` with a gap projection after every move."""
    quadrature, mass, length = quadrature_mass(boundary, density, samples)
    sites = np.sort((np.arange(count) + phase) * length / count % length)
    projectable = project_gaps(periodic_gaps(sites, length), min_separation,
                               gap_limit, length) is not None
    owner, delta, cost = periodic_cells(quadrature, mass, sites, length)
    history = [cost]
    exit_reason = 'ITERATION_LIMIT'
    before = np.array(sites)
    for _ in range(1, max_iterations + 1):
        totals = np.bincount(owner, weights=mass, minlength=count)
        move = np.zeros(count)
        populated = totals > 0
        move[populated] = np.bincount(
            owner, weights=mass * delta, minlength=count)[populated] / totals[populated]
        candidate = np.sort((sites + move) % length)
        before = np.array(candidate)
        if projectable:
            gaps = project_gaps(periodic_gaps(candidate, length), min_separation,
                                gap_limit, length)
            candidate = _sites_from_gaps(candidate, gaps, length)
        step = np.abs(((candidate - sites + length / 2) % length) - length / 2).max()
        sites = candidate
        owner, delta, next_cost = periodic_cells(quadrature, mass, sites, length)
        history.append(next_cost)
        if step < 1e-5 or abs(cost - next_cost) <= 1e-8 * max(1., cost):
            exit_reason = 'CONVERGED'
            break
        cost = next_cost
    return sites, before, np.array(history), exit_reason, float(mass.sum()), projectable


def _schedule(concentration, adapt, min_concentration, boundary, count, gap_limit, min_separation):
    if not adapt:
        return [float(concentration)]
    values = []
    alpha = float(concentration)
    while alpha >= min_concentration:
        values.append(alpha)
        alpha *= 0.5
    values.append(0.0)
    box_empty = (count * min_separation > boundary.length + 1e-9
                 or count * gap_limit < boundary.length - 1e-9)
    return values[:1] if box_empty else values


def plan_risk_aware(boundary, people, demand, count, bandwidth, gap_limit, min_separation,
                    phase=0., samples=2048, max_iterations=300, concentration=1.0,
                    min_concentration=1e-2, adapt_concentration=True, method='ra_abdc',
                    density_override=None):
    """Plan with gap projection. ``adapt_concentration=False`` is ``projected_mass``.

    ``density_override`` supplies a curve density directly. The default keeps
    the Gaussian demand-mass path and does not change the projection loop.
    """
    if method not in {'ra_abdc', 'projected_mass'}:
        raise ValueError(f'Unknown constrained method: {method}')
    if adapt_concentration != (method == 'ra_abdc'):
        raise ValueError('projected_mass does not adapt concentration; ra_abdc does')
    if count < 3:
        raise ValueError('At least three targets are required')
    if not np.isfinite([gap_limit, min_separation]).all() or min(gap_limit, min_separation) <= 0:
        raise ValueError('Gap limit and separation must be positive')
    if not 0 <= concentration <= 1:
        raise ValueError('Concentration exponent must lie in [0, 1]')
    if density_override is None:
        if not np.isfinite(bandwidth) or bandwidth <= 0:
            raise ValueError('Demand bandwidth must be positive')
        demand = np.asarray(demand, dtype=float)
        people = np.asarray(people, dtype=float)
        if demand.shape != (len(people),) or not np.isfinite(demand).all() or np.any(demand <= 0):
            raise ValueError('Demand must be finite, positive and match the people')
        rho = _density(boundary.deployment, people, demand, bandwidth, 'mass_cvt')
    else:
        rho = np.asarray(density_override, dtype=float)
        if (rho.shape != (len(boundary.deployment),) or not np.isfinite(rho).all()
                or np.any(rho <= 0)):
            raise ValueError('DENSITY_INVALID: override must be finite and positive on the curve')
    original_contrast = float(rho.max() / max(rho.min(), np.finfo(float).tiny))
    adjustments = []
    last = None
    for alpha in _schedule(concentration, adapt_concentration, min_concentration,
                           boundary, count, gap_limit, min_separation):
        started = perf_counter()
        if alpha == 0:
            quadrature, mass, length = quadrature_mass(boundary, np.ones(len(rho)), samples)
            sites = np.sort((np.arange(count) + phase) * length / count % length)
            before = None
            history = np.array([periodic_cells(quadrature, mass, sites, length)[2]])
            exit_reason, total, projectable = 'DIRECT', float(mass.sum()), False
            planning_density = np.ones(len(rho))
        else:
            planning_density = rho ** alpha
            sites, before, history, exit_reason, total, projectable = _projected_lloyd(
                boundary, planning_density, count, phase, samples, max_iterations,
                gap_limit, min_separation)
        positions, separation = _min_separation(boundary, sites)
        max_gap = float(periodic_gaps(sites, boundary.length).max())
        accepted = bool(max_gap <= gap_limit + 1e-9 and separation >= min_separation)
        after = _constraint_report(boundary, sites, gap_limit, min_separation)
        before_report = None if before is None else _constraint_report(
            boundary, before, gap_limit, min_separation)
        shift = None
        if before is not None:
            delta = ((sites - before + boundary.length / 2) % boundary.length) - boundary.length / 2
            shift = float(np.mean(np.abs(delta)))
        adjustments.append({
            'concentration': alpha,
            'adapt_concentration': adapt_concentration,
            'max_gap': max_gap,
            'min_separation': separation,
            'iterations': len(history) - 1,
            'exit_reason': exit_reason,
            'planner_seconds': perf_counter() - started,
            'gap_projection': ('applied' if projectable else
                               'equal_arc_direct' if alpha == 0 else 'infeasible_box'),
            'accepted': accepted,
            'fallback': alpha == 0.0,
            'fallback_reason': (None if alpha != 0.0 else
                                'euclidean_separation_failed_at_every_positive_concentration'),
            'original_density_contrast': original_contrast,
            'planning_density_contrast': float(
                planning_density.max() / max(planning_density.min(), np.finfo(float).tiny)),
            'original_density_subsample': _subsample(rho),
            'planning_density_subsample': _subsample(planning_density),
            'evaluation_density': 'original mass_cvt gaussian demand mass; concentration does not change it',
            'before_projection': before_report,
            'after_projection': after,
            'projection_shift': shift,
            # True when this attempt is the a=1 projected Lloyd step shared with RA-ABDC.
            'same_path_as_ra_abdc_unit_concentration': (not adapt_concentration and alpha == 1.0),
        })
        last = Plan(positions, sites, history, max_gap, method, len(history) - 1,
                    exit_reason, total, alpha, alpha == 0.0, tuple(adjustments),
                    adapt_concentration)
        if accepted:
            return last
    return last


def plan_projected_mass(boundary, people, demand, count, bandwidth, gap_limit, min_separation,
                        phase=0., samples=2048, max_iterations=300, density_override=None):
    """Mass CVT density with the RA-ABDC gap projection and no concentration schedule."""
    return plan_risk_aware(
        boundary, people, demand, count, bandwidth, gap_limit, min_separation, phase,
        samples, max_iterations, concentration=1.0, adapt_concentration=False,
        method='projected_mass', density_override=density_override)
