"""RA-ABDC candidate: demand-concentrated periodic CVT under explicit constraints.

Problem for a fixed number ``N`` of guides on a closed deployment curve of
length ``L`` (arc parameter ``s``):

* variables   ``s_1 < ... < s_N`` in ``[0, L)``;
* objective   ``J_a(s) = sum_q w_q rho_q**a * d_L(s_q, S)**2`` with the same
  Gaussian demand mass ``rho`` as Mass CVT and a concentration exponent
  ``a`` in ``[0, 1]`` (``a = 1`` is the full demand weight, ``a = 0`` is uniform);
* constraints  consecutive arc gaps ``g_i <= H_p`` (planned gap limit),
  arc gaps ``g_i >= d_min`` (necessary for the Euclidean separation),
  Euclidean separation ``|x(s_i) - x(s_j)| >= d_min``.  Reachability is
  checked afterwards by the unchanged navigation layer.

Algorithm (deliberately simple and fully recorded):

1. Lloyd steps on ``rho**a``; after every step the gap vector is projected
   onto ``{d_min <= g <= H_p, sum g = L}``.  This keeps the gap constraint
   satisfied while demand pulls sites into the hotspot.
2. If the converged plan still violates the Euclidean separation, ``a`` is
   halved and step 1 is repeated.  ``a = 0`` reproduces Equal Arc and is
   reported as ``equal_arc_fallback``.
3. Every attempt is stored in ``Plan.adjustments``.  No descent or optimality
   claim is made for the projected iteration; only the final constraint
   checks and the recorded cost history are evidence.
"""
import numpy as np

from .planners import (Plan, _density, _interpolate, periodic_cells, periodic_gaps,
                       project_gaps, quadrature_mass)


def _sites_from_gaps(sites, gaps, length):
    cumulative = np.r_[0., np.cumsum(gaps[:-1])]
    shift = float(np.mean(sites-cumulative))
    return np.sort((shift+cumulative) % length)


def _min_separation(boundary, sites):
    points = _interpolate(boundary, sites)
    distances = np.linalg.norm(points[:, None]-points[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    return points, float(distances.min())


def _projected_lloyd(boundary, density, count, phase, samples, max_iterations,
                     gap_limit, min_separation):
    quadrature, mass, length = quadrature_mass(boundary, density, samples)
    sites = np.sort((np.arange(count)+phase)*length/count % length)
    projectable = project_gaps(periodic_gaps(sites, length), min_separation,
                               gap_limit, length) is not None
    owner, delta, cost = periodic_cells(quadrature, mass, sites, length)
    history = [cost]
    exit_reason = 'ITERATION_LIMIT'
    for _ in range(1, max_iterations+1):
        totals = np.bincount(owner, weights=mass, minlength=count)
        move = np.zeros(count)
        populated = totals > 0
        move[populated] = np.bincount(owner, weights=mass*delta, minlength=count)[populated]/totals[populated]
        candidate = np.sort((sites+move) % length)
        if projectable:
            gaps = project_gaps(periodic_gaps(candidate, length), min_separation,
                                gap_limit, length)
            candidate = _sites_from_gaps(candidate, gaps, length)
        step = np.abs(((candidate-sites+length/2) % length)-length/2).max()
        sites = candidate
        owner, delta, next_cost = periodic_cells(quadrature, mass, sites, length)
        history.append(next_cost)
        if step < 1e-5 or abs(cost-next_cost) <= 1e-8*max(1., cost):
            exit_reason = 'CONVERGED'
            break
        cost = next_cost
    return sites, np.array(history), exit_reason, float(mass.sum()), projectable


def plan_risk_aware(boundary, people, demand, count, bandwidth, gap_limit, min_separation,
                    phase=0., samples=2048, max_iterations=300, concentration=1.0,
                    min_concentration=1e-2):
    """Return the most concentrated plan in the recorded schedule that passes the checks.

    If no exponent passes, the last attempt (``a = 0``, Equal Arc) is returned
    so the caller can reject it with the same rule used for every method.
    """
    if count < 3:
        raise ValueError('At least three targets are required')
    if not np.isfinite(bandwidth) or bandwidth <= 0:
        raise ValueError('Demand bandwidth must be positive')
    if not np.isfinite([gap_limit, min_separation]).all() or min(gap_limit, min_separation) <= 0:
        raise ValueError('Gap limit and separation must be positive')
    if not 0 <= concentration <= 1:
        raise ValueError('Concentration exponent must lie in [0, 1]')
    demand = np.asarray(demand, dtype=float)
    people = np.asarray(people, dtype=float)
    if demand.shape != (len(people),) or not np.isfinite(demand).all() or np.any(demand <= 0):
        raise ValueError('Demand must be finite, positive and match the people')
    rho = _density(boundary.deployment, people, demand, bandwidth, 'mass_cvt')
    schedule = []
    alpha = float(concentration)
    while alpha >= min_concentration:
        schedule.append(alpha)
        alpha *= 0.5
    schedule.append(0.0)
    if count*min_separation > boundary.length+1e-9 or count*gap_limit < boundary.length-1e-9:
        # The box simplex is empty: no exponent can repair this, so one
        # attempt is recorded and the caller rejects it with its usual rule.
        schedule = schedule[:1]
    adjustments = []
    last = None
    for alpha in schedule:
        if alpha == 0:
            # Explicit Equal Arc fallback: the direct construction, not a
            # Lloyd run on a discretised uniform density.
            quadrature, mass, length = quadrature_mass(boundary, np.ones(len(rho)), samples)
            sites = np.sort((np.arange(count)+phase)*length/count % length)
            history = np.array([periodic_cells(quadrature, mass, sites, length)[2]])
            exit_reason, total, projectable = 'DIRECT', float(mass.sum()), True
        else:
            sites, history, exit_reason, total, projectable = _projected_lloyd(
                boundary, rho**alpha, count, phase, samples, max_iterations, gap_limit, min_separation)
        positions, separation = _min_separation(boundary, sites)
        max_gap = float(periodic_gaps(sites, boundary.length).max())
        accepted = bool(max_gap <= gap_limit+1e-9 and separation >= min_separation)
        adjustments.append({
            'concentration': alpha, 'max_gap': max_gap, 'min_separation': separation,
            'iterations': len(history)-1, 'exit_reason': exit_reason,
            'gap_projection': 'applied' if projectable else 'infeasible_box',
            'accepted': accepted,
        })
        last = Plan(positions, sites, history, max_gap, 'ra_abdc', len(history)-1,
                    exit_reason, total, alpha, alpha == 0.0, tuple(adjustments))
        if accepted:
            return last
    return last
