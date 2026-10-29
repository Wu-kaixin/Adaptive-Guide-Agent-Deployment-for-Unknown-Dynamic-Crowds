from fractions import Fraction as F
import os

import pytest
import shapely

from udc.multigroup.offset_interval import point_box,Interval
from udc.multigroup.intersector_enclosure import enclosure


def test_normal_and_endpoint_fallback_regions_are_both_retained():
    boxes=tuple(point_box(p) for p in [[0.,0.],[10.,10.],[5.,10.],[10.,5.]])
    result=enclosure(boxes,precision_model='none')
    assert result.contains([7.5,7.5])
    assert result.contains([0.,0.])
    assert not all(q.contains(0) for q in result.normal_box)
    assert not result.contains([-1.,5.])
    assert result.support_upper((1,1))==20


def test_disjoint_envelopes_do_not_become_an_existence_claim():
    result=enclosure(tuple(point_box(p) for p in [[0.,0.],[1.,1.],[3.,3.],[4.,4.]]),precision_model='none')
    assert result.normal_box is None
    assert result.serial()['intersection_may_be_empty'] is True
    assert result.contains([3.,3.])


def test_axis_box_is_invariant_under_normal_and_fallback_point_creation():
    endpoints=tuple(point_box(p,.001) for p in [[1.,2.],[4.,5.],[2.,4.],[3.,3.]])
    result=enclosure(endpoints,precision_model='floating')
    axis=result.outer_axis_box()
    assert all(axis[j].lo<=q.lo<=q.hi<=axis[j].hi for p in result.components for j,q in enumerate(p))
    assert axis[0].lo==F(1)-F.from_float(.001)
    with pytest.raises(ValueError,match='precision'):
        enclosure(endpoints,precision_model='fixed')


@pytest.mark.skipif(os.name!='nt' or shapely.geos_version_string!='3.14.1',reason='Measured bundled Windows GEOS 3.14.1 only')
def test_actual_c_api_proper_endpoint_collinear_and_disjoint_outputs():
    from udc.analysis.native_geos_segment import NativeSegmentIntersector
    fixtures=[([[0.,0.],[2.,2.],[0.,2.],[2.,0.]],True),
        ([[0.,0.],[1.,1.],[1.,1.],[2.,0.]],True),
        ([[0.,0.],[2.,2.],[1.,1.],[3.,3.]],True),
        ([[0.,0.],[1.,1.],[3.,3.],[4.,4.]],False)]
    with NativeSegmentIntersector() as native:
        for points,exists in fixtures:
            value=native.intersect(points)
            assert (value is not None)==exists
            if value is not None:assert enclosure(tuple(point_box(p) for p in points),precision_model='none').contains(value)
