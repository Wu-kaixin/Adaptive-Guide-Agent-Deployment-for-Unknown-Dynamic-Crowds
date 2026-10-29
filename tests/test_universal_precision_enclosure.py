from fractions import Fraction as F

from udc.multigroup.offset_interval import Interval
from udc.multigroup.precision_enclosure import PrecisionModel
from udc.multigroup.universal_precision_enclosure import unknown_precision_box,default_point_buffer_box


def test_unknown_positive_grid_can_double_coordinate_exactly():
    model=PrecisionModel.from_grid(2.)
    assert model.quantize(F(1))==2
    box=unknown_precision_box((Interval(1,1),Interval(-1,-1)))
    assert box[0].lo==0 and box[0].hi>=2
    assert box[1].lo<=-2 and box[1].hi==0


def test_two_independent_grids_can_amplify_fourfold_but_shared_grid_need_not():
    first=PrecisionModel.from_grid(2.);second=PrecisionModel.from_grid(4.)
    assert second.quantize(first.quantize(F(1)))==4
    assert first.quantize(first.quantize(F(1)))==2
    result=unknown_precision_box((Interval(1,1),Interval(0,0)),stages=2)
    assert result[0].hi>=4 and result[1]==Interval(0,0)


def test_zero_subnormal_and_extreme_scale_do_not_require_eta_over_scale_padding():
    from udc.multigroup.precision_enclosure import _rn,_java_round
    tiny=F(1,2**1074)
    # A scaled-map case whose reciprocal grid cannot itself be constructed as
    # finite binary64: test the stated scalar theorem, not native Point API.
    for x in [tiny,-tiny,F(1),F(-1)]:
        q=_rn(_java_round(_rn(x*tiny))/tiny)
        box=unknown_precision_box((Interval(x,x),Interval(0,0)))
        assert box[0].contains(q)


def test_default_point_contract_contains_unquantized_circle_source_and_declared_maps():
    from udc.multigroup.circle_source_enclosure import circular_source_box
    centers=(Interval(1,2),Interval(-2,-1));raw=circular_source_box(centers,.2)
    universal=default_point_buffer_box(centers,.2)
    for source,outer in zip(raw,universal):
        assert outer.lo<=source.lo<=source.hi<=outer.hi
        for grid in [.1,2.,2.00000001,100.]:
            model=PrecisionModel.from_grid(grid)
            for x in [source.lo,source.hi]:
                value=model.quantize(model.quantize(x),map_kind='scaled_noder')
                assert outer.contains(value)
