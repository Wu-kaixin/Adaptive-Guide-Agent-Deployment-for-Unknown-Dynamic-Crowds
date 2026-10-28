"""Finite-catalog max-min service allocation, including zero robots per group."""
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Point

from udc.deployment.planners import (demand_profile, quadrature_mass, periodic_cells,
                                     periodic_gaps, project_gaps, _interpolate)
from .geometry import ScientificFailure


@dataclass(frozen=True)
class Candidate:
    count: int
    sites: np.ndarray
    positions: np.ndarray
    uncovered: float
    cost_m2: float
    normalized_cost: float
    max_gap_m: float
    phase: int


@dataclass(frozen=True)
class Allocation:
    counts: tuple[int, ...]
    candidates: tuple[Candidate, ...]
    objective: tuple[float, float, float]
    available: int
    active: int
    policy: str


def service_statistics(quadrature, mass, sites, length, service_radius):
    if not len(sites):
        # A declared bounded no-service penalty; U=1 is the primary criterion.
        return 1.0, length * length / 4, length
    _, nearest, cost = periodic_cells(quadrature, mass, sites, length)
    return (float(mass[np.abs(nearest) > service_radius].sum() / mass.sum()),
            float(cost / mass.sum()), float(periodic_gaps(np.sort(sites), length).max()))


def _lloyd_sites(boundary, quadrature, mass, count, phase, config):
    length = boundary.length
    sites = (np.arange(count) + phase) * length / count
    # When full coverage is count-infeasible, its gap constraint is not softened:
    # this separate partial-service optimizer uses only the minimum arc spacing.
    upper = config['planned_gap_m'] if count * config['planned_gap_m'] >= length else length
    for _ in range(config['lloyd_iterations']):
        owner, delta, _ = periodic_cells(quadrature, mass, sites, length)
        totals = np.bincount(owner, weights=mass, minlength=count)
        move = np.divide(np.bincount(owner, weights=mass * delta, minlength=count), totals,
                         out=np.zeros(count), where=totals > 0)
        next_sites = np.sort((sites + move) % length)
        if count > 1:
            gaps = project_gaps(periodic_gaps(next_sites, length), config['guide_distance_m'], upper, length)
            if gaps is not None:
                cumulative = np.r_[0., np.cumsum(gaps[:-1])]
                next_sites = np.sort((cumulative + np.mean(next_sites - cumulative)) % length)
        difference = np.max(np.abs((next_sites - sites + length / 2) % length - length / 2))
        sites = next_sites
        if difference < 1e-5:
            break
    return sites


def build_catalog(groups, measurement, domain, max_budget, layout, config, *, admission=None):
    if layout not in ('equal_arc', 'projected_mass'):
        raise ValueError(f'Unknown layout {layout}')
    obs = measurement.observation
    catalogs = []
    for group in groups:
        boundary = group.boundary
        rows = np.asarray(group.rows, dtype=int)
        density = demand_profile(boundary, obs.positions[rows], obs.demand[rows], config['bandwidth_m'])
        quadrature, mass, length = quadrature_mass(boundary, density, config['planning_samples'])
        catalog = {0: Candidate(0, np.empty(0), np.empty((0, 2)), 1., length ** 2 / 4, .25, length, -1)}
        max_count = min(max_budget, int(np.floor(length / config['guide_distance_m'])))
        for count in range(1, max_count + 1):
            candidates = []
            for phase_index in range(config['phase_bins']):
                phase = phase_index / config['phase_bins']
                sites = ((np.arange(count) + phase) * length / count if layout == 'equal_arc' else
                         _lloyd_sites(boundary, quadrature, mass, count, phase, config))
                positions = _interpolate(boundary, sites)
                if not all(domain.free.covers(Point(point)) for point in positions):
                    continue
                if count > 1:
                    distances = np.linalg.norm(positions[:, None] - positions[None, :], axis=2)
                    distances[np.diag_indices(count)] = np.inf
                    if distances.min() < config['guide_distance_m']:
                        continue
                if admission is not None and not admission(positions):
                    continue
                uncovered, cost, gap = service_statistics(quadrature, mass, sites, length, config['max_gap_m'] / 2)
                candidates.append(Candidate(count, sites, positions, uncovered, cost, cost / length ** 2, gap, phase_index))
            if candidates:
                catalog[count] = min(candidates, key=lambda c: (c.uncovered, c.normalized_cost, c.phase))
        catalogs.append(catalog)
    return tuple(catalogs)


def allocation_objective(candidates, demand_weights):
    weights = np.asarray(demand_weights, dtype=float)
    weights = weights / weights.sum()
    return (max(c.uncovered for c in candidates),
            float(sum(w * c.uncovered for w, c in zip(weights, candidates))),
            float(sum(w * c.normalized_cost for w, c in zip(weights, candidates))))


def _minimax(catalogs, budget, weights):
    # First solve the bottleneck exactly. Keeping just one lexicographic state
    # per budget before fixing the bottleneck is WRONG when later groups mask
    # an earlier maximum. The second DP has additive objectives only.
    thresholds = sorted({c.uncovered for catalog in catalogs for c in catalog.values()})
    threshold = next(value for value in thresholds
                     if sum(min((n for n, c in catalog.items() if c.uncovered <= value), default=budget + 1)
                            for catalog in catalogs) <= budget)
    states = {0: (0., 0., ())}
    for weight, catalog in zip(weights, catalogs):
        next_states = {}
        for spent, (uncovered, cost, choices) in states.items():
            for count, candidate in sorted(catalog.items()):
                if spent + count > budget or candidate.uncovered > threshold:
                    continue
                value = (uncovered + weight * candidate.uncovered,
                         cost + weight * candidate.normalized_cost, choices + (count,))
                old = next_states.get(spent + count)
                if old is None or value < old:
                    next_states[spent + count] = value
        states = next_states
    return min(states.items(), key=lambda item: (item[1][0], item[1][1], item[0], item[1][2]))[1][2]


def _proportional(catalogs, budget, weights):
    """Hamilton-like deficit allocation, skipping unavailable finite counts."""
    ideal = budget * np.asarray(weights, dtype=float) / np.sum(weights)
    counts = [0] * len(catalogs)
    while True:
        remaining = budget - sum(counts)
        options = []
        for k, catalog in enumerate(catalogs):
            possible = [n for n in catalog if counts[k] < n <= counts[k] + remaining]
            if possible:
                next_count = min(possible)
                options.append(((ideal[k] - counts[k]) / (next_count - counts[k]), -k, next_count))
        if not options:
            return tuple(counts)
        _, negative_k, count = max(options)
        counts[-negative_k] = count


def allocate(catalogs, groups, budget, policy='minimax'):
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        raise ValueError('Resource budget must be a nonnegative integer')
    if not catalogs or len(catalogs) != len(groups) or any(0 not in c for c in catalogs):
        raise ValueError('Each observed group needs a catalog including zero service')
    demand = np.asarray([g.demand_total for g in groups], dtype=float)
    if policy == 'minimax':
        counts = _minimax(catalogs, budget, demand / demand.sum())
    elif policy in ('perimeter', 'demand'):
        weights = demand if policy == 'demand' else np.array([g.boundary.length for g in groups])
        counts = _proportional(catalogs, budget, weights)
    else:
        raise ValueError(f'Unknown allocation policy: {policy}')
    candidates = tuple(catalog[n] for catalog, n in zip(catalogs, counts))
    return Allocation(counts, candidates, allocation_objective(candidates, demand), budget, sum(counts), policy)


def selected_targets(allocation, config):
    positions = [c.positions for c in allocation.candidates if c.count]
    targets = np.vstack(positions) if positions else np.empty((0, 2))
    group_ids = np.repeat(np.arange(len(allocation.counts)), allocation.counts)
    if len(targets) > 1:
        distances = np.linalg.norm(targets[:, None] - targets[None, :], axis=2)
        distances[np.diag_indices(len(targets))] = np.inf
        if distances.min() < config['guide_distance_m']:
            raise ScientificFailure('TARGET_SEPARATION_INVALID', 'Global target separation failed')
    return targets, group_ids
