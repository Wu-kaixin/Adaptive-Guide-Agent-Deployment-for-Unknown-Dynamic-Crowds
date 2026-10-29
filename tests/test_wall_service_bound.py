from types import SimpleNamespace
import numpy as np

from udc.analysis.wall_service_bound import wall_uncovered_bound


def test_wall_obstruction_integrates_union_of_strips_without_double_counting():
    boundary = SimpleNamespace(deployment=np.array([[0.,0.],[4.,0.],[4.,4.],[0.,4.]]))
    bound = wall_uncovered_bound(boundary, np.ones(4), [8.,8.], 2., 1.)
    assert bound['unreachable_arc_length_m'] == 10.
    assert bound['uncovered_lower_bound'] == .625
    assert not bound['runtime_uncertainty_certificate']


def test_wall_obstruction_uses_original_linear_demand_and_vanishes_in_interior():
    boundary = SimpleNamespace(deployment=np.array([[0.,0.],[4.,0.],[4.,4.],[0.,4.]]))
    bound = wall_uncovered_bound(boundary, np.array([1.,3.,5.,7.]), [8.,8.], 2., 1.)
    assert bound['uncovered_lower_bound'] == .53125
    interior = SimpleNamespace(deployment=np.array([[2.,2.],[4.,2.],[4.,4.],[2.,4.]]))
    assert wall_uncovered_bound(interior, np.ones(4), [8.,8.], 2., 1.)['uncovered_lower_bound'] == 0.
