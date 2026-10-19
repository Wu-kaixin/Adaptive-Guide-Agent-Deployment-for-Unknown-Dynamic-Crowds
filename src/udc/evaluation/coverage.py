"""Demand-weighted coverage and hotspot diagnostics shared by every method.

These numbers are diagnostics. They do not enter the nine acceptance
criteria. Every method is scored on the same original Mass-CVT Gaussian
demand density. A planner may concentrate that density with an exponent;
the exponent is not applied here.
"""
import numpy as np
from shapely.geometry import Point, Polygon

from udc.deployment import coverage_statistics, demand_profile, periodic_gaps, quadrature_mass
from udc.deployment.planners import periodic_cells


def arc_positions(boundary, points):
    line = Polygon(boundary.deployment).exterior
    return np.array([line.project(Point(p)) for p in np.asarray(points, dtype=float)])


def hotspot_window(boundary, quadrature, people, mask):
    """Legacy window: nodes whose nearest person lies in the demand disk.

    Interior people lose to anyone closer to the curve, so an ellipse can show
    an empty window while the Gaussian excess is still concentrated on one side.
    Cross-shape comparison uses ``excess_coverage`` instead.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != (len(people),) or not mask.any() or mask.all():
        return None
    curve = np.column_stack([np.interp(quadrature, np.r_[boundary.arc, boundary.length],
                                       np.r_[boundary.deployment[:, d], boundary.deployment[0, d]])
                             for d in range(2)])
    nearest = np.argmin(np.sum((curve[:, None]-np.asarray(people)[None, :])**2, axis=2), axis=1)
    return mask[nearest]


def _site_report(quadrature, mass, sites, length, half_gap, window):
    stats = coverage_statistics(quadrature, mass, sites, length, half_gap)
    report = {'demand_weighted_cost': stats['weighted_cost'],
              'uncovered_demand_share': stats['uncovered_share'],
              'uniform_cost': coverage_statistics(quadrature, np.ones_like(mass), sites,
                                                  length, half_gap)['weighted_cost']}
    if window is None:
        report.update(hotspot_guide_fraction=None, hotspot_concentration=None,
                      hotspot_mean_spacing=None, hotspot_max_gap=None)
        return report
    # Fractional guide count in the window: each guide contributes the share
    # of its nearest-site cell that lies inside the window. Equal spacing gives
    # exactly the window's arc fraction, so concentration 1.0 means "no bias".
    sites = np.sort(np.asarray(sites, dtype=float))
    owner, _, _ = periodic_cells(quadrature, np.ones_like(mass), sites, length)
    cell_nodes = np.bincount(owner, minlength=len(sites)).astype(float)
    inside_nodes = np.bincount(owner, weights=window.astype(float), minlength=len(sites))
    fractional = float(np.sum(inside_nodes[cell_nodes > 0]/cell_nodes[cell_nodes > 0]))
    arc_fraction = float(window.mean())
    guide_fraction = fractional/len(sites)
    gaps = periodic_gaps(sites, length)
    midpoints = (sites+gaps/2) % length
    mid_node = np.clip(np.round(midpoints/length*len(quadrature)-0.5).astype(int), 0, len(quadrature)-1)
    hot_gaps = gaps[window[mid_node]]
    report.update(
        hotspot_guide_fraction=guide_fraction,
        hotspot_concentration=(guide_fraction/arc_fraction if arc_fraction > 0 else None),
        hotspot_mean_spacing=(arc_fraction*length/fractional if fractional > 0 else None),
        hotspot_max_gap=(float(hot_gaps.max()) if len(hot_gaps) else None),
    )
    return report


def distmesh_gap_prediction(boundary, people, demand, bandwidth, samples, count, gap_limit):
    """Gap implied by the DistMesh-inspired fixed point, which equalises the integral of sqrt(rho).

    At that fixed point the local spacing is ``h(s) = (1/N) * integral(sqrt(rho)) / sqrt(rho(s))``,
    so the widest gap sits at the density minimum. The relaxation has no gap
    cap, which is why it exceeds the planned limit as soon as the density
    contrast is large enough for a given ``N``.
    """
    rho = demand_profile(boundary, np.asarray(people, dtype=float), demand, bandwidth, 'mass_cvt')
    quadrature, mass, length = quadrature_mass(boundary, rho, int(samples))
    root = np.sqrt(np.maximum(mass, np.finfo(float).tiny))
    density_on_nodes = mass/(length/len(quadrature))
    integral = float(root.sum()*np.sqrt(length/len(quadrature)))
    predicted = integral/count/np.sqrt(float(density_on_nodes.min()))
    return {
        'density_contrast': float(density_on_nodes.max()/density_on_nodes.min()),
        'sqrt_density_contrast': float(np.sqrt(density_on_nodes.max()/density_on_nodes.min())),
        'predicted_max_gap': float(predicted),
        'uniform_gap': float(length/count),
        'count_for_limit': int(np.ceil(integral/np.sqrt(float(density_on_nodes.min()))/float(gap_limit))),
    }


def excess_weights(demand, mask, multiplier=None):
    """Person-level demand above the frozen background. Zero when there is no hotspot.

    With a declared multiplier and no clipping, a masked person's background is
    ``demand / multiplier``. Otherwise the background is the median demand
    outside the mask. This uses only the frozen field, not any guide position.
    """
    demand = np.asarray(demand, dtype=float)
    if mask is None:
        return np.zeros(len(demand)), False
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != demand.shape or not mask.any() or mask.all():
        return np.zeros(len(demand)), False
    excess = np.zeros(len(demand))
    if multiplier is not None and np.isfinite(multiplier) and multiplier > 1:
        excess[mask] = np.maximum(demand[mask] - demand[mask] / float(multiplier), 0.0)
    else:
        background = float(np.median(demand[~mask]))
        excess[mask] = np.maximum(demand[mask] - background, 0.0)
    return excess, bool(np.any(excess > 0))


def _log_gaussian_mass(points, people, weights, bandwidth):
    """Log of ``sum_j w_j exp(-||x-p_j||^2 / (2 h^2))`` at each curve sample."""
    weights = np.asarray(weights, dtype=float)
    active = weights > 0
    if not np.any(active):
        return np.full(len(points), -np.inf)
    squared = np.sum((np.asarray(points)[:, None] - np.asarray(people)[None, active]) ** 2, axis=2)
    from scipy.special import logsumexp
    return logsumexp(-squared / (2 * float(bandwidth) ** 2) + np.log(weights[active]), axis=1)


def excess_coverage(boundary, people, demand, mask, bandwidth, samples, targets,
                    final_positions=None, multiplier=None, curve_tolerance=None,
                    actual_curve_distance=None):
    """Coverage of the original demand and of the hotspot excess, on one quadrature.

    Costs are mean squared arc distance in m^2. Smaller is better. The excess
    weights and the arc where excess mass is at least half the local mass are
    fixed before any guide position is considered. Uniform demand has no excess,
    so the hotspot fields are not applicable. Arc distance uses the existing
    curve projection; ``actual_off_curve`` records when the executed points leave
    the curve by more than ``curve_tolerance``.
    """
    people = np.asarray(people, dtype=float)
    demand = np.asarray(demand, dtype=float)
    excess, applicable = excess_weights(demand, mask, multiplier)
    points = np.asarray(boundary.deployment, dtype=float)
    log_total = _log_gaussian_mass(points, people, demand, bandwidth)
    shift = float(np.max(log_total))
    total_rho = np.exp(log_total - shift)
    if applicable:
        log_excess = _log_gaussian_mass(points, people, excess, bandwidth)
        excess_rho = np.exp(np.clip(log_excess - shift, -80.0, None))
    else:
        excess_rho = np.zeros(len(points))
    background_rho = np.maximum(total_rho - excess_rho, 0.0)
    quadrature, total_mass, length = quadrature_mass(boundary, total_rho, int(samples))
    excess_mass = quadrature_mass(boundary, excess_rho, int(samples))[1]
    background_mass = quadrature_mass(boundary, background_rho, int(samples))[1]
    local = np.divide(excess_mass, total_mass, out=np.zeros_like(excess_mass), where=total_mass > 0)
    window = local >= 0.5 if applicable else None
    report = {
        'evaluation_density': 'original gaussian demand mass; shared shift; excess is demand above background',
        'excess_applicable': applicable,
        'excess_demand_share': (None if not applicable else float(excess_mass.sum() / total_mass.sum())),
        'excess_arc_rule': 'quadrature nodes whose excess mass is at least half the local demand mass',
        'excess_arc_fraction': (None if window is None else float(window.mean())),
        'lower_is_better': ['demand_weighted_cost', 'excess_weighted_cost', 'background_weighted_cost', 'max_gap'],
    }
    groups = [('planned', targets)]
    if final_positions is not None and len(np.asarray(final_positions)) == len(np.asarray(targets)):
        groups.append(('actual', final_positions))
    off_curve = None
    if actual_curve_distance is not None and curve_tolerance is not None:
        off_curve = bool(float(actual_curve_distance) > float(curve_tolerance))
    report['actual_off_curve'] = off_curve
    for prefix, positions in groups:
        if positions is None or len(np.asarray(positions)) == 0:
            continue
        sites = arc_positions(boundary, positions)
        for label, mass in (('demand', total_mass), ('excess', excess_mass), ('background', background_mass)):
            stats = coverage_statistics(quadrature, mass, sites, length, np.inf)
            key = 'demand_weighted_cost' if label == 'demand' else f'{label}_weighted_cost'
            report[f'{prefix}_{key}'] = None if not applicable and label != 'demand' else stats['weighted_cost']
        if window is None or not window.any():
            report[f'{prefix}_excess_concentration'] = None
            report[f'{prefix}_max_gap'] = float(periodic_gaps(np.sort(sites), length).max())
            continue
        fractional = _site_report(quadrature, total_mass, sites, length, np.inf, window)
        report[f'{prefix}_excess_concentration'] = fractional['hotspot_concentration']
        report[f'{prefix}_max_gap'] = float(periodic_gaps(np.sort(sites), length).max())
    return report


def coverage_diagnostics(boundary, people, demand, mask, bandwidth, samples, gap_limit,
                         targets, final_positions):
    """Planned (targets) and actual (final positions) coverage under one demand density."""
    people = np.asarray(people, dtype=float)
    rho = demand_profile(boundary, people, demand, bandwidth, 'mass_cvt')
    quadrature, mass, length = quadrature_mass(boundary, rho, int(samples))
    window = None if mask is None else hotspot_window(boundary, quadrature, people, mask)
    half_gap = float(gap_limit)/2
    report = {'coverage_density': 'mass_cvt gaussian demand mass, shared by all methods',
              'hotspot_arc_fraction': None if window is None else float(window.mean()),
              'hotspot_demand_share': None if window is None else float(mass[window].sum()/mass.sum())}
    for prefix, points in (('planned', targets), ('actual', final_positions)):
        sites = arc_positions(boundary, points)
        for key, value in _site_report(quadrature, mass, sites, length, half_gap, window).items():
            report[f'{prefix}_{key}'] = value
    return report
