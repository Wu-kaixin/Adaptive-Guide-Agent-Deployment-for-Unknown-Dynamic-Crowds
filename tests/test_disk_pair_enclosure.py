from fractions import Fraction as F

from udc.multigroup.disk_pair_enclosure import disk_pair_box
from udc.multigroup.offset_interval import Interval


def test_diagonal_lens_has_exact_axis_extrema_at_circle_intersections():
    box,evidence=disk_pair_box((0,0),1,(1,1),1)
    # The lens intersection points are (0,1) and (1,0), giving exact extrema.
    assert box==(Interval(0,1),Interval(0,1))
    assert evidence['kind']=='intersecting_circles'


def test_lens_curved_extremum_requires_pairwise_constraint():
    box,_=disk_pair_box((0,0),1,(0,1),1)
    assert box[0].hi<1 and box[0].lo>-1
    assert box[0].hi**2>=F(3,4)
    assert box[1]==Interval(0,1)


def test_empty_nested_and_tangent_disks_are_exact():
    assert disk_pair_box((0,0),1,(F(3,2),F(3,2)),1)[0] is None
    assert disk_pair_box((0,0),2,(F(1,4),0),1)[0]==(Interval(F(-3,4),F(5,4)),Interval(-1,1))
    assert disk_pair_box((0,0),1,(2,0),1)[0]==(Interval(1,1),Interval(0,0))
    assert disk_pair_box((0,0),0,(0,0),0)[0]==(Interval(0,0),Interval(0,0))
