from fractions import Fraction as F

import pytest

from udc.multigroup.offset_interval import Interval
from udc.multigroup.precision_enclosure import PrecisionModel
from udc.multigroup.snapround_enclosure import snapround_output_box,scaled_snapround_output_box


def test_one_noder_image_need_not_contain_the_original_unrounded_box():
    # A noder's output image is stronger than an invariant box that must also
    # contain its input. It still makes no assertion that any node exists.
    box=(Interval(F(1,10),F(2,10)),Interval(F(-2,10),F(-1,10)))
    result=snapround_output_box(box,PrecisionModel.from_scale(1.))
    assert result==(Interval(0,0),Interval(0,0))


def test_scaled_wrapper_uses_scale_even_when_point_quantizer_uses_internal_grid():
    model=PrecisionModel.from_grid(2.00000001)
    box=(Interval.point(1.1),Interval.point(-1.1))
    direct=snapround_output_box(box,model)
    scaled=scaled_snapround_output_box(box,model)
    assert direct[0]==Interval(2,2)
    assert direct!=scaled
    assert scaled[0].lo==F.from_float(2.00000001)


def test_unit_scale_wrapper_and_input_contract():
    model=PrecisionModel.from_scale(1.)
    box=(Interval.point(.5),Interval.point(-.5))
    assert scaled_snapround_output_box(box,model)==snapround_output_box(box,model)
    for operation in [snapround_output_box,scaled_snapround_output_box]:
        with pytest.raises(ValueError):operation(box,PrecisionModel.from_grid(0.))
