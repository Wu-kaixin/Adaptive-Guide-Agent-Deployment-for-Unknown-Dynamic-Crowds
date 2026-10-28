"""Total-demand finite-catalog comparator with the same feasible candidates."""
import numpy as np

from .allocation import Allocation, allocation_objective


def total_allocation(catalogs, groups, budget):
    if type(budget) is not int or budget < 0 or not catalogs or len(catalogs) != len(groups):
        raise ValueError('Matching catalogs and a nonnegative integer budget required')
    if any(0 not in catalog for catalog in catalogs):
        raise ValueError('Catalogs must include zero service')
    weights = np.asarray([group.demand_total for group in groups], dtype=float)
    weights /= weights.sum()
    states = {0: (0., 0., ())}
    for weight, catalog in zip(weights, catalogs):
        updated = {}
        for spent, state in states.items():
            for count, candidate in sorted(catalog.items()):
                if spent + count > budget:
                    continue
                value = (state[0] + weight * candidate.uncovered,
                         state[1] + weight * candidate.normalized_cost, state[2] + (count,))
                if spent + count not in updated or value < updated[spent + count]:
                    updated[spent + count] = value
        states = updated
    counts = min(states.items(), key=lambda item: (item[1][0], item[1][1], item[0], item[1][2]))[1][2]
    candidates = tuple(catalog[n] for catalog, n in zip(catalogs, counts))
    return Allocation(counts, candidates, allocation_objective(candidates, weights), budget, sum(counts), 'total')
