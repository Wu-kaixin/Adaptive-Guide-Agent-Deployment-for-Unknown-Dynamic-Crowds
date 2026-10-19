"""Deterministic planners on a sampled periodic deployment curve."""
from dataclasses import dataclass, field

import numpy as np

BASELINE_METHODS = ('equal_arc', 'average_cvt', 'mass_cvt', 'distmesh')


@dataclass(frozen=True)
class Plan:
    positions: np.ndarray
    arc_positions: np.ndarray
    cost: np.ndarray
    max_gap: float
    method: str
    iterations: int
    exit_reason: str
    density_total: float
    # Constrained-planner bookkeeping. Baselines keep the defaults.
    concentration: float = 1.0
    equal_arc_fallback: bool = False
    adjustments: tuple = field(default_factory=tuple)
    adapt_concentration: bool = False


def _density(points, people, demand, bandwidth, mode):
    squared = np.sum((points[:, None]-people[None, :])**2, axis=2)
    shifted = squared-squared.min(axis=1, keepdims=True)
    kernel = np.exp(-shifted/(2*bandwidth**2))
    mass = kernel @ demand
    if mode == 'average_cvt':
        return mass/np.maximum(kernel.sum(axis=1), np.finfo(float).tiny)
    # For a ratio the row scale cancels. For mass it must be restored.
    # Apply one common log scale across the curve to avoid underflow without
    # changing relative densities at different arc positions.
    from scipy.special import logsumexp
    log_mass = logsumexp(-squared/(2*bandwidth**2) + np.log(demand)[None, :], axis=1)
    return np.maximum(np.exp(log_mass-log_mass.max()), np.finfo(float).tiny)


def demand_profile(boundary, people, demand, bandwidth, mode='mass_cvt'):
    """Demand density on the sampled deployment curve.

    ``mass_cvt``: Gaussian demand mass ``sum_j d_j K(x(s), p_j)`` divided by
    its curve-wide maximum. ``average_cvt``: kernel-weighted mean demand.
    This is the only way person demand enters a deployment curve.
    """
    if mode not in {'mass_cvt', 'average_cvt'}:
        raise ValueError(f'Unknown density mode: {mode}')
    demand = np.asarray(demand, dtype=float)
    return _density(np.asarray(boundary.deployment), np.asarray(people, dtype=float),
                    demand, float(bandwidth), mode)


def _interpolate(boundary, sites):
    return np.column_stack([np.interp(sites, np.r_[boundary.arc, boundary.length],
        np.r_[boundary.deployment[:, d], boundary.deployment[0, d]]) for d in range(2)])


def quadrature_mass(boundary, density, samples):
    """Midpoint quadrature of a curve density: nodes, per-node mass, length."""
    length = boundary.length
    quadrature = (np.arange(samples)+0.5)*length/samples
    rho = np.interp(quadrature, np.r_[boundary.arc, length], np.r_[density, density[0]])
    return quadrature, rho*length/samples, length


def periodic_cells(quadrature, mass, sites, length):
    """Nearest-site partition on the periodic curve and the weighted cost."""
    delta = (quadrature[:, None]-sites[None, :]+length/2) % length-length/2
    owner = np.argmin(np.abs(delta), axis=1)
    nearest = delta[np.arange(len(delta)), owner]
    return owner, nearest, float(np.sum(mass*nearest**2))


def coverage_statistics(quadrature, mass, sites, length, radius):
    """Demand-weighted mean squared arc distance and the mass farther than ``radius``."""
    _, nearest, cost = periodic_cells(quadrature, mass, np.asarray(sites, dtype=float), length)
    total = float(mass.sum())
    if total <= 0:
        return {'weighted_cost': None, 'uncovered_share': None}
    return {'weighted_cost': cost/total,
            'uncovered_share': float(mass[np.abs(nearest) > radius].sum()/total)}


def periodic_gaps(sites, length):
    """Consecutive arc gaps of sorted periodic sites; the last wraps around."""
    sites = np.asarray(sites, dtype=float)
    return np.diff(np.r_[sites, sites[0]+length])


def project_gaps(gaps, low, high, length):
    """Euclidean projection of ``gaps`` onto {low <= g <= high, sum g = length}.

    The projection is ``clip(g + lam, low, high)`` with the multiplier ``lam``
    found by bisection. Returns ``None`` when the box simplex is empty.
    """
    gaps = np.asarray(gaps, dtype=float)
    count = len(gaps)
    if count*low > length+1e-9 or count*high < length-1e-9:
        return None
    low_lambda, high_lambda = low-gaps.max(), high-gaps.min()
    for _ in range(200):
        middle = 0.5*(low_lambda+high_lambda)
        total = np.clip(gaps+middle, low, high).sum()
        if total < length:
            low_lambda = middle
        else:
            high_lambda = middle
    projected = np.clip(gaps+0.5*(low_lambda+high_lambda), low, high)
    # Distribute the residual rounding on interior gaps so the sum is exact.
    residual = length-projected.sum()
    interior = (projected > low+1e-12) & (projected < high-1e-12)
    if interior.any():
        projected[interior] += residual/interior.sum()
    else:
        projected += residual/count
    return projected


def feasibility_bounds(length, gap_limit, min_separation):
    """Necessary count conditions for a periodic curve of the given length.

    ``max_gap <= gap_limit`` requires ``N >= ceil(L / gap_limit)`` only if every
    feasible plan is a set of arc sites on this one closed curve. Euclidean
    chord length never exceeds arc length, so ``N <= floor(L / d_min)`` is
    necessary for a positive minimum separation. Violating either certifies
    that no admissible target set exists. Satisfying both does not: curvature
    can make a chord shorter than its arc, and obstacles or reachability are
    outside this count.
    """
    if not np.isfinite([length, gap_limit, min_separation]).all() or min(length, gap_limit, min_separation) <= 0:
        raise ValueError('Feasibility bounds need positive finite length, gap limit and separation')
    return {
        'curve_length': float(length),
        'planned_gap_limit': float(gap_limit),
        'min_separation': float(min_separation),
        # Same arithmetic as the controller's CAPACITY_SHORTFALL check.
        'necessary_min_count': int(max(3, np.ceil(length/gap_limit))),
        'necessary_max_count': int(np.floor(length/min_separation)),
    }


def infeasibility_certificate(bounds, count):
    """Strict evidence that ``count`` admits no plan, or ``None`` when unknown."""
    if count < bounds['necessary_min_count']:
        return 'GAP_BOUND'
    if count > bounds['necessary_max_count']:
        return 'SEPARATION_BOUND'
    return None


def plan_coverage(boundary, people, demand, count, bandwidth, method='average_cvt',
                  phase=0., samples=2048, max_iterations=300, density_override=None):
    """Plan sites; each fixed-N history belongs to one deterministic attempt.

    ``density_override`` replaces the Gaussian curve density for a diagnostic
    call. Equal Arc still uses a constant density. The default ``None`` keeps
    the original demand path, including its checks and the order of operations.
    """
    if count < 3:
        raise ValueError('At least three targets are required')
    if method not in BASELINE_METHODS:
        raise ValueError(f'Unknown planning method: {method}')
    points = boundary.deployment
    if density_override is None:
        if not np.isfinite(bandwidth) or bandwidth <= 0:
            raise ValueError('Demand bandwidth must be positive')
        demand = np.asarray(demand, dtype=float)
        if demand.shape != (len(people),) or not np.isfinite(demand).all() or np.any(demand <= 0):
            raise ValueError('Demand must be finite, positive and match the people')
        density = np.ones(len(points)) if method == 'equal_arc' else _density(
            points, np.asarray(people), demand, bandwidth,
            'average_cvt' if method == 'average_cvt' else 'mass_cvt')
    else:
        density = np.asarray(density_override, dtype=float)
        if density.shape != (len(points),) or not np.isfinite(density).all() or np.any(density <= 0):
            raise ValueError('DENSITY_INVALID: override must be finite and positive on the curve')
        if method == 'equal_arc':
            density = np.ones(len(points))
    quadrature, mass, length = quadrature_mass(boundary, density, samples)
    sites = (np.arange(count)+phase)*length/count % length

    owner, delta, cost = periodic_cells(quadrature, mass, sites, length)
    history = [cost]
    if method == 'equal_arc':
        targets = _interpolate(boundary, np.sort(sites))
        return Plan(targets, np.sort(sites), np.array(history), length/count,
                    method, 0, 'DIRECT', float(mass.sum()))
    exit_reason = 'ITERATION_LIMIT'
    for iteration in range(1, max_iterations+1):
        totals = np.bincount(owner, weights=mass, minlength=count)
        if np.any(totals <= 0):
            raise ValueError('PLAN_INVALID: empty coverage cell')
        if method == 'distmesh':
            # A periodic spacing relaxation: equalise integral of sqrt(rho).
            cumulative = np.cumsum(np.sqrt(np.maximum(mass, np.finfo(float).tiny)))
            desired = np.interp((np.arange(count)+.5)*cumulative[-1]/count,
                                cumulative, quadrature)
            move = ((desired-sites+length/2) % length)-length/2
            move *= .35
        else:
            move = np.bincount(owner, weights=mass*delta, minlength=count)/totals
        sites = np.sort((sites+move) % length)
        owner, delta, next_cost = periodic_cells(quadrature, mass, sites, length)
        if method != 'distmesh' and next_cost > cost+1e-9:
            raise ValueError('PLAN_INVALID: coverage cost increased')
        history.append(next_cost)
        if np.max(np.abs(move)) < 1e-5 or cost-next_cost <= 1e-8*max(1., cost):
            exit_reason = 'CONVERGED'
            break
        cost = next_cost
    targets = _interpolate(boundary, sites)
    return Plan(targets, sites, np.array(history),
                float(periodic_gaps(sites, length).max()), method,
                len(history)-1, exit_reason, float(mass.sum()))
