from fractions import Fraction as F
import os

import numpy as np
import pytest
import shapely

from udc.multigroup.circle_source_enclosure import circular_source_box
from udc.multigroup.geometry import outer_disk
from udc.multigroup.offset_interval import Interval,UncertifiedArithmetic
from udc.multigroup.precision_enclosure import PrecisionModel


def test_final_rounding_can_exceed_ideal_real_radius_expansion():
    result=circular_source_box((Interval.point(1.),Interval.point(2.)),.1)
    assert result[0].hi>F(1)+F.from_float(.1)
    assert result[0].hi==F.from_float(1.1)


def test_both_fused_and_separate_expressions_have_the_same_outer_extrema():
    box=(Interval(F(1),F(2)),Interval(F(-2),F(-1)));d=F.from_float(.3)
    result=circular_source_box(box,float(d))
    for j in (0,1):
        for center in [float(box[j].lo),float(box[j].hi),float((box[j].lo+box[j].hi)/2)]:
            for t in [-1.,-.5,0.,.5,1.]:
                separate=center+float(d)*t
                fused=float(F.from_float(center)+d*F.from_float(t))
                assert result[j].contains(F.from_float(separate))
                assert result[j].contains(F.from_float(fused))


def test_precision_is_an_explicit_separate_coordinate_operation():
    point=(Interval.point(1.1),Interval.point(-1.1))
    model=PrecisionModel.from_grid(2.00000001)
    result=circular_source_box(point,0.,precision=model)
    assert result==(Interval(2,2),Interval(-2,-2))


def test_empty_binary_input_interval_and_overflow_refuse():
    tiny=Interval(F(1)+F(1,2**60),F(1)+F(2,2**60))
    with pytest.raises(UncertifiedArithmetic):circular_source_box((tiny,Interval.point(0.)),1.)
    with pytest.raises(UncertifiedArithmetic):circular_source_box((Interval.point(1.7e308),Interval.point(0.)),1.7e308)


@pytest.mark.skipif(os.name!='nt' or shapely.geos_version_string!='3.14.1',reason='Pinned measured GEOS only')
def test_actual_initial_outer_disk_is_inside_conditional_source_box_on_fixed_inputs():
    # Finite full-buffer observations, not a proof every fallback path is covered.
    radius=.25;distance=radius/np.cos(np.pi/64)+1e-12
    for center in [[1.,2.],[1.125,2.375],[-2.,-3.]]:
        box=tuple(Interval.point(v) for v in center);result=circular_source_box(box,distance)
        coordinates=np.asarray(outer_disk(center,radius).exterior.coords)
        assert all(q.contains(F.from_float(float(v))) for p in coordinates for q,v in zip(result,p))
