from fractions import Fraction as F
import pytest

from udc.multigroup.offset_interval import Interval,UncertifiedArithmetic
from udc.multigroup.union_coordinate_enclosure import unary_union_coordinate_box,interpolation_coordinate_box


def test_cascade_budget_counts_components_and_zero_buffer_fallback():
    box=(Interval.point(10.),Interval.point(20.))
    for n,depth in ((1,0),(2,1),(3,2),(8,3),(9,4)):
        r=unary_union_coordinate_box(box,n,quantizers_per_binary=2)
        assert r.maximum_binary_depth==depth
        assert r.serial()['unknown_quantizer_stage_limit']==2*depth
        assert not r.serial()['component_limit_proved']
        assert not r.serial()['service_certificate']
    assert unary_union_coordinate_box(box,1).output_box==box
    assert unary_union_coordinate_box(box,9).serial()['unknown_quantizer_stage_limit']==4
    assert unary_union_coordinate_box(box,9).output_box[0].hi<unary_union_coordinate_box(box,9,quantizers_per_binary=2).output_box[0].hi
    with pytest.raises(ValueError):unary_union_coordinate_box(box,0)


def test_interpolation_on_any_declared_edge_includes_separate_and_fused_sources():
    box=(Interval(F(-3),F(7)),Interval(F(1,10),F(3,5)))
    result,errors=interpolation_coordinate_box(box)
    for j,q in enumerate(box):
        for a,b in ((float(q.lo),float(q.hi)),(float(q.hi),float(q.lo))):
            for t in (0.,.1,.5,.9,1.):
                separate=(b-a)*t+a
                fused=float(F.from_float(b-a)*F.from_float(t)+F.from_float(a))
                assert result[j].contains(F.from_float(separate))
                assert result[j].contains(F.from_float(fused))
        assert errors[j]>0 and result[j].lo<q.lo and result[j].hi>q.hi


def test_interpolation_domain_exhaustion_is_an_explicit_refusal():
    with pytest.raises(UncertifiedArithmetic):
        interpolation_coordinate_box((Interval(F(2**501),F(2**501)),Interval.point(0.)))
