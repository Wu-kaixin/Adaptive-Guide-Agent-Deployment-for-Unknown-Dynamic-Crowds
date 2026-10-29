from fractions import Fraction as F
import math

from udc.multigroup.finite_offset_enclosure import finite_normal_limit,finite_offset_box,MIN_POSITIVE_LENGTH
from udc.multigroup.offset_interval import Interval,point_box


def test_finite_tiny_edge_can_exceed_ideal_unit_normal_displacement():
    dx=math.nextafter(math.sqrt(1.5)*float(MIN_POSITIVE_LENGTH),0.)
    q=dx*dx;length=math.sqrt(q);distance=.4
    displacement=(distance*dx)/length
    assert q==float.fromhex('0x0.0000000000001p-1022')
    assert displacement>distance
    assert F.from_float(displacement)<=finite_normal_limit(F.from_float(distance))


def test_conditional_bound_does_not_certify_coincident_endpoint_family():
    box=point_box([1.,2.],.12)
    result=finite_offset_box(box,box,Interval.point(.4),-1)
    assert result.serial()['all_inputs_defined'] is False
    assert result.serial()['status']=='CONDITIONAL_FINITE_OUTPUT_ENCLOSURE'
    assert result.serial()['service_certificate'] is False


def test_regular_normal_and_zero_distance_are_bounded():
    a=point_box([1.,1.]);b=point_box([2.,2.])
    result=finite_offset_box(a,b,Interval.point(.4),1)
    off=.4/math.sqrt(2.)
    for point,limits in zip([[1-off,1+off],[2-off,2+off]],result.endpoints):
        assert all(q.contains(F.from_float(v)) for q,v in zip(limits,point))
    assert finite_normal_limit(0)==0
