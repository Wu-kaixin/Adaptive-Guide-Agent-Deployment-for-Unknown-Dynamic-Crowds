"""Observation-only fixed-count phase comparisons and bounded service retention.

Candidate generation stays at the original 256-node Lloyd method. Continuous
scoring changes selection only. No truth or true reference enters this module.
"""
from dataclasses import dataclass

import numpy as np
from shapely.geometry import LineString, Point

from udc.deployment.planners import demand_profile, quadrature_mass, _interpolate
from .allocation import _lloyd_sites, service_statistics
from .service import continuous_arc_service, continuous_euclidean_service
from .geometry import ScientificFailure


METHODS = ('sampled_arc_greedy', 'continuous_arc_greedy', 'continuous_euclidean_greedy',
           'euclidean_retain_0.002', 'euclidean_retain_0.005')
GREEDY = METHODS[:3]


@dataclass(frozen=True)
class PhaseOption:
    source: str
    sites: np.ndarray
    positions: np.ndarray
    sampled_arc_u: float
    continuous_arc_u: float
    continuous_euclidean_u: float
    normalized_arc_cost: float


def admissible(positions, domain, minimum_distance):
    positions = np.asarray(positions).reshape(-1, 2)
    if not all(domain.free.covers(Point(p)) for p in positions):
        return False
    if len(positions) > 1:
        distances = np.linalg.norm(positions[:, None] - positions[None], axis=2)
        np.fill_diagonal(distances, np.inf)
        if distances.min() < minimum_distance:
            return False
    return True


def phase_pool(group, measurement, domain, count, config, nominal_layouts=None, *, base_options=None):
    """All scoring methods get exactly the same feasible phase/anchor options."""
    if type(count) is not int or count < 0:
        raise ValueError('Fixed count must be a nonnegative integer')
    if base_options is not None:
        if any(len(option.sites)!=count or option.source.startswith('anchor_') for option in base_options):
            raise ValueError('Cache must contain only same-count base phase options')
        if not nominal_layouts:
            return tuple(base_options)
    index = np.asarray(group.rows)
    obs = measurement.observation
    boundary = group.boundary
    density = demand_profile(boundary, obs.positions[index], obs.demand[index], config['bandwidth_m'])
    arcs, mass, length = quadrature_mass(boundary, density, config['planning_samples'])
    candidates = []
    if count:
        for phase in range(config['phase_bins'] if base_options is None else 0):
            sites = _lloyd_sites(boundary, arcs, mass, count, phase / config['phase_bins'], config)
            candidates.append((f'phase_{phase}', sites))
        if nominal_layouts:
            line = LineString(np.vstack([boundary.deployment, boundary.deployment[0]]))
            for method in GREEDY:
                points = np.asarray(nominal_layouts[method]).reshape(-1, 2)
                if len(points) != count:
                    raise ValueError('Projected anchor must preserve the fixed robot count')
                sites = np.sort([line.project(Point(point)) for point in points])
                candidates.append(('anchor_' + method, sites))
    else:
        if base_options is None:candidates.append(('zero_service', np.empty(0)))
    options = list(base_options) if base_options is not None else []
    evaluated = {}
    for source, sites in candidates:
        key=sites.tobytes()
        if key in evaluated:
            positions,sampled,arc_value,euclidean_value,cost=evaluated[key]
            options.append(PhaseOption(source,sites,positions,sampled,arc_value,euclidean_value,cost))
            continue
        positions = _interpolate(boundary, sites)
        if not admissible(positions, domain, config['guide_distance_m']):
            continue
        sampled, cost, _ = service_statistics(arcs, mass, sites, length, config['max_gap_m'] / 2)
        arc_value=continuous_arc_service(boundary,density,sites,config['max_gap_m']/2)['uncovered']
        euclidean_value=continuous_euclidean_service(boundary,density,positions,config['max_gap_m']/2)['uncovered']
        normalized=cost/length**2
        evaluated[key]=(positions,sampled,arc_value,euclidean_value,normalized)
        options.append(PhaseOption(source,sites,positions,sampled,arc_value,euclidean_value,normalized))
    if not options:
        raise ScientificFailure('FIXED_COUNT_LAYOUT_UNAVAILABLE', 'No shared candidate preserves fixed count',
                                group_id=group.group_id, count=count)
    return tuple(options)


def select_phase(options, method):
    if method not in METHODS:
        raise ValueError('Unknown phase selection method')
    field = ('sampled_arc_u' if method == 'sampled_arc_greedy' else
             'continuous_arc_u' if method == 'continuous_arc_greedy' else 'continuous_euclidean_u')
    best = min(options, key=lambda c: (getattr(c, field), c.normalized_arc_cost, c.source))
    selected, retained = best, False
    tolerance = 0.
    if method.startswith('euclidean_retain_'):
        tolerance = float(method.rsplit('_', 1)[1])
        incumbent = next((c for c in options if c.source == 'anchor_continuous_euclidean_greedy'), None)
        if incumbent is not None and incumbent.continuous_euclidean_u <= best.continuous_euclidean_u + tolerance:
            selected, retained = incumbent, True
    slack = selected.continuous_euclidean_u - min(c.continuous_euclidean_u for c in options)
    return selected, {'retained': retained, 'tolerance': tolerance, 'observed_euclidean_slack': slack,
                      'scope': 'assigned group on the CURRENT shared observed finite pool; not true-reference or global optimality'}


def choose_groups(pools, method):
    selected, evidence = zip(*(select_phase(pool, method) for pool in pools))
    positions = np.vstack([c.positions for c in selected])
    group_ids = np.concatenate([np.full(len(c.positions), k, int) for k, c in enumerate(selected)])
    return positions, group_ids, selected, evidence
