from fractions import Fraction as F

import numpy as np
import pytest

from udc.multigroup.interpolation_enclosure import coordinate_error_bounds, support_envelope


def test_real_binary_interpolation_hull_counterexample_is_enclosed_without_waiving_it():
    a = np.array([9.4886130607308115, 16.654921043706963])
    b = np.array([9.398403239070488, 16.56471122204664])
    t = float.fromhex('0x1.36a6827a4fc7cp-1')
    q = (b-a)*t+a
    dot = lambda p: F.from_float(float(p[1]))-F.from_float(float(p[0]))
    assert dot(q)-max(dot(a), dot(b)) == F(1, 2**49)
    envelope = support_envelope([a,b], ((-1,1),))
    assert dot(q) <= envelope.upper_supports[0]
    assert dot(q) > max(dot(a), dot(b))


def test_uniform_rational_coordinate_bound_covers_rounding_cancellation_and_subnormals():
    pairs = [([-1., 2.], [3., -5.]), ([2.**100, 2.**-1074], [-2.**99, 0.]),
             ([9.4886130607308115,16.654921043706963], [9.398403239070488,16.56471122204664])]
    for a,b in pairs:
        a,b = np.array(a),np.array(b)
        error = coordinate_error_bounds([a,b])
        for t in [0., 2.**-52, .25, .5, .606739118033047, np.nextafter(1.,0.), 1.]:
            q=(b-a)*t+a
            for j in range(2):
                exact=F.from_float(float(a[j]))+(F.from_float(float(b[j]))-F.from_float(float(a[j])))*F.from_float(float(t))
                assert abs(F.from_float(float(q[j]))-exact) <= error[j]
                # A hypothetical correctly rounded fused product+sum.
                d_hat=F.from_float(float(b[j]-a[j]))
                fused=F.from_float(float(F.from_float(float(a[j]))+d_hat*F.from_float(float(t))))
                assert abs(fused-exact) <= error[j]


@pytest.mark.parametrize('points', [[[float('nan'),0.],[1.,1.]], [[2.**501,0.],[1.,1.]], [[1.,1.]]])
def test_invalid_or_unbounded_arithmetic_contract_is_rejected(points):
    with pytest.raises(ValueError):
        coordinate_error_bounds(points)
