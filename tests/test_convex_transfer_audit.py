from fractions import Fraction

import numpy as np

from udc.analysis.convex_transfer_audit import exact_supports, support_violations


def test_exact_support_retains_sub_ulp_dot_product_violation():
    # Both float x+y evaluations round to 1, but the binary-coordinate support
    # of the target is strictly larger by 2**-54. A float argmax misses it.
    source = np.array([[1., 0.]])
    target = np.array([[1., 2.**-54]])
    assert float(target[0, 0] + target[0, 1]) == 1.
    failures = support_violations(source, target, directions=((1, 1),))
    assert len(failures) == 1
    assert Fraction(failures[0]['excess_fraction']) == Fraction(1, 2**54)


def test_exact_dilated_support_compares_squared_irrational_direction_limit():
    source = np.array([[0., 0.]])
    # For direction (1,1), the permitted support increment is sqrt(2).
    assert not support_violations(source, [[.5, .5]], radius=1., directions=((1, 1),))
    assert support_violations(source, [[.75, .75]], radius=1., directions=((1, 1),))


def test_support_includes_every_point_without_float_argmax_selection():
    scores = exact_supports([[1., 0.], [1., 2.**-54]], directions=((1, 1),))
    assert scores == [(Fraction(1) + Fraction(1, 2**54), 1)]
