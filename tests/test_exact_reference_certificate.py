from fractions import Fraction
from types import SimpleNamespace
import numpy as np

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.exact_reference_certificate import polyline_loss_bound, exact_observation_certificate, _sqrt_bounds


def square():
    return SimpleNamespace(deployment=np.array([[0.,0.],[4.,0.],[4.,4.],[0.,4.]]))


def test_signed_mass_cancellation_is_certified_without_covering_entire_curve():
    r = polyline_loss_bound(square(), np.ones(4), [[2.,4.]], [[2.,0.]], 1., delta=0., tracking_bound=0.)
    assert r['accept_retained'] and r['upper_loss'] == 0.
    assert r['signed_loss_interval'] == [0., 0.]


def test_linear_demand_gain_and_real_loss_remain_distinct():
    gain = polyline_loss_bound(square(), [1.,3.,5.,7.], [[2.,4.]], [[2.,0.]], 1., tracking_bound=0.)
    assert gain['accept_retained']
    loss = polyline_loss_bound(square(), [1.,3.,5.,7.], [[2.,0.]], [[2.,4.]], 1., tracking_bound=0.)
    assert not loss['accept_retained'] and loss['upper_loss'] >= .125


def test_subdivision_exhaustion_is_not_an_acceptance():
    r = polyline_loss_bound(square(), np.ones(4), [[1.13,.1]], [[2.,.17]], .9,
        delta=0., max_cells=4, max_depth=0)
    assert not r['accept_retained'] and r['reason'] == 'finite_subdivision_budget_exhausted'


def test_square_root_brackets_use_exact_inequalities():
    for v in (Fraction(2), Fraction(1, 9), Fraction(16), Fraction(1, 10**40)):
        lo, hi = _sqrt_bounds(v)
        assert lo * lo <= v <= hi * hi


def test_nonzero_error_is_rejected_before_reference_reconstruction():
    measurement = BoundedMeasurement(Observation(np.array([[1.,1.]]), np.array([.2]), np.ones(1)), 1e-15, 'bounded')
    result = exact_observation_certificate(measurement, None, {}, [], [])
    assert not result['accept_retained']
    assert result['reason'] == 'nonzero_observation_error_original_reference_unknown'
