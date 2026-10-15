"""Demand-weighted coverage and hotspot diagnostics shared by every method.

These numbers are diagnostics. They do not enter the nine acceptance
criteria and they use one common definition for all deployment methods:
the Mass-CVT Gaussian demand density on the deployment curve.
"""
import numpy as np
from shapely.geometry import Point, Polygon

from udc.deployment import coverage_statistics, demand_profile, periodic_gaps, quadrature_mass
from udc.deployment.planners import periodic_cells


def arc_positions(boundary, points):
    line = Polygon(boundary.deployment).exterior
    return np.array([line.project(Point(p)) for p in np.asarray(points, dtype=float)])


def hotspot_window(boundary, quadrature, people, mask):
    """Quadrature nodes whose nearest person belongs to the demand region."""
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
