"""Conditional LP checks; no controller/original-world guarantee is implied."""
from fractions import Fraction as F
import pytest

from udc.multigroup.mass_ambiguity import declared_mass_budget


def test_common_total_and_signed_gain_can_prove_budget_separate_extrema_miss():
    # Total=100 forces neutral+gain >=99 when loss<=1. The separate bound
    # (loss_hi-gain_lo)/total_lo = 1/100 > .002 misses this implication.
    r = declared_mass_budget([1, -1, 0], [0, 0, 0], [1, 100, 98], 100, 100, F(1, 500))
    assert r['max_budget_residual'] == F(-1, 5)
    assert r['maximizing_masses'] == (1, 1, 98)
    assert r['budget_holds_for_declared_mass_set']
    assert not r['original_reference_enclosed'] and not r['runtime_service_certificate']


def test_threshold_equality_and_an_exact_overbudget_vector():
    for loss, accepts in ((F(1, 5), True), (F(1, 5) + F(1, 10**20), False)):
        r = declared_mass_budget([1, 0], [loss, 100-loss], [loss, 100-loss], 100, 100, F(1, 500))
        assert r['budget_holds_for_declared_mass_set'] is accepts


def test_inconsistent_or_zero_total_information_cannot_vacuously_certify():
    empty = declared_mass_budget([1], [1], [2], 3, 4, F(1, 500))
    zero = declared_mass_budget([-1], [0], [1], 0, 1, F(1, 500))
    assert empty['status'] == 'EMPTY_DECLARED_SET'
    assert zero['status'] == 'NO_POSITIVE_TOTAL_LOWER'
    assert not empty['budget_holds_for_declared_mass_set']
    assert not zero['budget_holds_for_declared_mass_set']
    with pytest.raises(ValueError):
        declared_mass_budget([1], [0.], [1], 1, 1, F(1, 500))
