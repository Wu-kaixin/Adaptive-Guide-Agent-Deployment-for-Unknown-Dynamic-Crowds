import itertools
from types import SimpleNamespace

import numpy as np
import pytest

from udc.multigroup.allocation import Candidate, allocation_objective
from udc.multigroup.total_allocation import total_allocation


@pytest.mark.parametrize('budget', [0, 1, 3, 7])
def test_total_dp_matches_exhaustive_nonmonotone_missing_catalog(budget):
    rng = np.random.default_rng(19)
    catalogs = [{n: Candidate(n, np.zeros(n), np.zeros((n, 2)), float(rng.uniform()),
                              .1, float(rng.uniform()), 2., 0) for n in ns}
                for ns in [(0, 1, 3), (0, 2, 3), (0, 1, 2)]]
    groups = [SimpleNamespace(demand_total=w) for w in [1., 2., 4.]]
    result = total_allocation(catalogs, groups, budget)
    feasible = [counts for counts in itertools.product(*(sorted(c) for c in catalogs)) if sum(counts) <= budget]
    def key(counts):
        obj = allocation_objective([c[n] for c, n in zip(catalogs, counts)], [1., 2., 4.])
        return obj[1], obj[2], sum(counts), counts
    assert result.counts == min(feasible, key=key)
