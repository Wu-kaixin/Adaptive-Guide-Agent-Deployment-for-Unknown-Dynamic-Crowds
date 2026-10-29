from fractions import Fraction as F
import math
import os

import pytest
import shapely
from shapely.geometry import Point

from udc.multigroup.offset_interval import Interval
from udc.multigroup.precision_enclosure import PrecisionModel,invariant_axis_box


def test_half_ties_are_upward_for_positive_and_negative_binary_values():
    model=PrecisionModel.from_scale(1.)
    assert [model.quantize(F(x,2)) for x in (-5,-3,-1,1,3,5)]==[-2,-1,0,1,2,3]
    # A binary addition before floor is not equivalent near a half tie.
    below=math.nextafter(.5,-math.inf)
    assert model.quantize(F.from_float(below))==0


def test_constructor_grid_and_reported_grid_need_not_match():
    model=PrecisionModel.from_grid(2.00000001)
    assert model.grid==2 and model.reported_grid!=model.grid
    assert model.quantize(F.from_float(1.1))==2
    near_one=PrecisionModel.from_grid(1.00000001)
    assert near_one.grid==1 and near_one.branch=='scaled_fine'


def test_requested_half_grid_is_not_a_sound_displacement_bound():
    grid=1./10000.000009
    model=PrecisionModel.from_grid(grid)
    x=F.from_float(.00005);q=model.quantize(x)
    assert abs(q-x)>F.from_float(grid)/2
    assert abs(q-x)<=model.displacement_bound(Interval(x,x))


def test_monotone_range_and_axis_closure_are_explicitly_verified():
    model=PrecisionModel.from_scale(10.)
    box=(Interval(F(-7,100),F(14,100)),Interval(F(-3,10),F(7,10)))
    closed,steps=invariant_axis_box(box,[model],include_scaled_noder=True)
    assert steps<=3
    for original,new in zip(box,closed):
        assert new.lo<=original.lo<=original.hi<=new.hi
        image=model.range(new)
        assert new.lo<=image.lo<=image.hi<=new.hi


def test_floating_map_preserves_input_and_invalid_models_refuse():
    model=PrecisionModel.from_grid(0.)
    assert model.quantize(F.from_float(.1))==F.from_float(.1)
    with pytest.raises(ValueError):PrecisionModel.from_scale(0.)
    with pytest.raises(ValueError):PrecisionModel.from_grid(-1.)


@pytest.mark.skipif(os.name!='nt' or shapely.geos_version_string!='3.14.1',reason='Pinned measured GEOS only')
def test_actual_point_precision_agrees_with_constructor_and_quantizer():
    for grid in [0.,1e-4,.1,1.,1.00000001,2.00000001,1./10000.000009]:
        model=PrecisionModel.from_grid(grid)
        for x in [-2.5,-.5,-.00005,.00005,.5,1.1]:
            result=shapely.set_precision(Point(x,-x),grid,mode='pointwise')
            assert F.from_float(result.x)==model.quantize(F.from_float(x))
            assert F.from_float(result.y)==model.quantize(F.from_float(-x))
            assert F.from_float(float(shapely.get_precision(result)))==model.reported_grid
