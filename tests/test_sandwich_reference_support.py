import numpy as np
import pytest
import yaml
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from udc.experiments.static_multigroup import ROOT
from udc.interfaces import Observation
from udc.multigroup.geometry import construct_groups
from udc.multigroup.observation import BoundedMeasurement, measure
from udc.multigroup.scenarios import make_truth
from udc.multigroup.evaluation import reference_groups
from udc.multigroup.reference_support import build_support
from udc.multigroup.sandwich_reference_support import fill_holes


def model():
    return yaml.safe_load((ROOT / 'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))['model']


@pytest.mark.parametrize('shape', ['circle', 'ellipse', 'concave', 'irregular'])
@pytest.mark.parametrize('epsilon', [0., .04, .12])
def test_original_reference_remains_enclosed_and_region_never_widens(shape, epsilon):
    config = model()
    truth = make_truth(1, shape, 81, 93850)
    reference = reference_groups(truth, [40, 30], config)
    for seed in (7, 19):
        measurement = measure(truth, epsilon, seed)
        groups, _ = construct_groups(measurement, [40, 30], config)
        base = build_support(measurement, groups, config)
        tighter = build_support(measurement, groups, config | {'support_enclosure': 'monotone_sandwich_v1'})
        for actual, original, region in zip(reference, base.regions, tighter.regions):
            line = LineString(np.vstack([actual.boundary.deployment, actual.boundary.deployment[0]]))
            assert line.difference(region).is_empty
            assert region.difference(original).is_empty
            assert region.area <= original.area


def test_component_fill_does_not_drop_disconnected_people():
    islands = unary_union([Point(0, 0).buffer(1), Point(5, 0).buffer(1)])
    filled = fill_holes(islands)
    assert filled.covers(islands)
    assert len(filled.geoms) == 2


def test_large_error_with_empty_guaranteed_inner_set_is_supported():
    config = model() | {'support_enclosure': 'monotone_sandwich_v1'}
    measurement = BoundedMeasurement(Observation(np.array([[10., 10.]]), np.array([.2]), np.ones(1)), .3, 'bounded')
    groups, _ = construct_groups(measurement, [40, 30], config)
    result = build_support(measurement, groups, config)
    assert not result.regions[0].is_empty
    assert result.evidence['groups'][0]['monotone_sandwich']['excluded_inner_area_m2'] == 0
