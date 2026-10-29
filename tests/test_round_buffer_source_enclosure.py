from fractions import Fraction as F

from udc.multigroup.offset_interval import Interval
from udc.multigroup.round_buffer_source_enclosure import closing_auxiliary_box,round_buffer_source_box


def test_closing_arithmetic_does_not_preserve_an_ideal_singleton_convex_hull():
    box=(Interval.point(.1),Interval.point(.7))
    value=closing_auxiliary_box(box,box,80)
    assert value[0].lo<F.from_float(.1)
    assert value[1].hi>F.from_float(.7)
    for j,x in enumerate([.1,.7]):assert value[j].contains(F.from_float((80*x+x)/81))


def test_positive_and_negative_round_distances_have_same_outer_source_range():
    box=(Interval(1,2),Interval(3,4))
    positive=round_buffer_source_box(box,.4);negative=round_buffer_source_box(box,-.4)
    assert positive==negative
    for original,raw,whole in zip(box,positive.raw_box,positive.output_box):
        assert whole.lo<=raw.lo<=original.lo<=original.hi<=raw.hi<=whole.hi
    assert positive.serial()['all_inputs_defined'] is False
    assert positive.serial()['service_certificate'] is False


def test_both_numerator_associations_are_enclosed_at_crossed_extrema():
    offset=(Interval(F(-2),F(3)),Interval(F(4),F(5)));center=(Interval(0,1),Interval(-2,1))
    for k in [1,80]:
        bounds=closing_auxiliary_box(offset,center,k)
        for j in (0,1):
            for x in [offset[j].lo,offset[j].hi]:
                for y in [center[j].lo,center[j].hi]:
                    separate=(k*float(x)+float(y))/(k+1)
                    fused=float(F.from_float(float(k*x+y))/F(k+1))
                    assert bounds[j].contains(F.from_float(separate))
                    assert bounds[j].contains(F.from_float(fused))
