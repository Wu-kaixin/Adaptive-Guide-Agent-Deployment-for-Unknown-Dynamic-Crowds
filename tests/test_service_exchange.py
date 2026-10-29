from types import SimpleNamespace

import numpy as np

from udc.multigroup.service import continuous_service_exchange


def square():
    return SimpleNamespace(deployment=np.array([[0., 0.], [4., 0.], [4., 4.], [0., 4.]]))


def test_disjoint_equal_coverage_can_have_large_exchange_but_zero_net_loss():
    result = continuous_service_exchange(square(), np.ones(4), [[2., 0.]], [[2., 4.]], 1.)
    assert result['lost_mass_fraction'] == .125
    assert result['gained_mass_fraction'] == .125
    assert result['net_uncovered_difference'] == 0.
    assert not result['uncertainty_certificate']


def test_piecewise_linear_demand_keeps_signed_mass_balance():
    result = continuous_service_exchange(square(), np.array([1., 3., 5., 7.]), [[2., 0.]], [[2., 4.]], 1.)
    assert result['lost_mass_fraction'] == .0625
    assert result['gained_mass_fraction'] == .1875
    assert result['net_uncovered_difference'] == -.125


def test_same_layout_has_no_lost_or_gained_mass_even_when_partially_covered():
    robots = [[2., 0.], [2., 4.]]
    result = continuous_service_exchange(square(), np.ones(4), robots, robots, 1.)
    assert result['lost_mass_fraction'] == result['gained_mass_fraction'] == 0.
