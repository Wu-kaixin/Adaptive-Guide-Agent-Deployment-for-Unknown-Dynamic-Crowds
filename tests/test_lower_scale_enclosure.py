from fractions import Fraction as F
import pytest

from udc.multigroup.offset_interval import Interval
from udc.multigroup.lower_scale_enclosure import lower_scale_precision_box
from udc.multigroup.precision_enclosure import PrecisionModel


def test_declared_scale_floor_covers_distinct_scales_and_both_precision_maps():
    box=(Interval(F(-3),F(7)),Interval(F(1,10),F(3,5)))
    bound,_=lower_scale_precision_box(box,100,stages=2)
    for x,y in ((-.125,.12345),(6.9999,.59),(0.,.1)):
        for first,second in ((100.,100.),(100.,137.),(137.,1e6),(1e6,100.)):
            for axis,v in enumerate((x,y)):
                a=PrecisionModel.from_scale(first).quantize(F.from_float(v))
                b=PrecisionModel.from_scale(second).quantize(a,map_kind='scaled_noder')
                assert bound[axis].contains(b)


def test_stage_zero_is_identity_and_tighter_floor_reduces_only_the_bound():
    box=(Interval.point(10.),Interval.point(20.))
    assert lower_scale_precision_box(box,100,stages=0)==(box,(F(0),F(0)))
    loose,_=lower_scale_precision_box(box,100,stages=2)
    tight,_=lower_scale_precision_box(box,10000,stages=2)
    assert all(a.lo<b.lo<=b.hi<a.hi for a,b in zip(loose,tight))


def test_unsupported_coarse_scales_and_implicit_float_floors_refuse():
    box=(Interval.point(0.),Interval.point(0.))
    for floor in (F(1,2),100.):
        with pytest.raises(ValueError):lower_scale_precision_box(box,floor)
