"""Known-answer geometry tests, not additional crowd experiment samples."""
from fractions import Fraction
from math import cos, pi

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import Point, Polygon, box

from udc.boundary import audit_person_disk_coverage
from udc.boundary.coverage import (person_disk_polygons, radial_discretization_error,
    exact_point_location, exact_point_boundary_distance, exact_convex_reference_gap_enclosure)


def audit(region, center=(0.,0.), radius=.2):
    return audit_person_disk_coverage(region,[center],[radius])


@pytest.mark.parametrize('radius',[0.,.05,.2,.7])
def test_reference_and_ideal_sagitta(radius):
    for q in (16,32):
        error=radial_discretization_error(radius,q)
        assert error==pytest.approx(radius*(1-cos(pi/(4*q))),abs=1e-16)
        polygon=person_disk_polygons([[0.,0.]],[radius],q)[0]
        if radius:
            vertices=np.asarray(polygon.exterior.coords)
            midpoint=(vertices[0]+vertices[1])/2
            assert radius-np.linalg.norm(midpoint)==pytest.approx(error,abs=1e-16)
        else:
            assert polygon.geom_type=='Point'
    assert audit(box(-1,-1,1,1),radius=radius)['candidate_accepted']


def test_containment_and_exact_boundary_tangency():
    assert audit(box(-1,-1,1,1))['candidate_accepted']
    result=audit(box(-1,-1,1,1),(.8,0.),.2)
    assert result['candidate_accepted'] and result['persons'][0]['de9im']
    assert result['coverage_acceptance_tolerance_m']==0
    assert not result['approximate_acceptance_enabled']


@pytest.mark.parametrize('missing',[1e-4,1e-9])
def test_small_real_gap_is_rejected(missing):
    result=audit(box(-1,-1,.2-missing,1))
    assert not result['candidate_accepted']
    assert result['persons'][0]['exact_outside_vertex_witness'] is not None


def test_even_one_ulp_gap_is_not_silently_tolerated():
    result=audit(box(-1,-1,np.nextafter(.2,-np.inf),1))
    assert not result['candidate_accepted']
    assert result['persons'][0]['classification']=='REJECT_EXACT_SAVED_COORDINATE_WITNESS'


def test_tiny_area_long_slit_not_accepted_by_area_or_vertices():
    region=box(-1,-1,1,1).difference(box(.01,-.2,.0100000001,.2))
    reference=person_disk_polygons([[0,0]],[.5])[0]
    assert all(region.covers(Point(p)) for p in reference.exterior.coords)
    result=audit(region,radius=.5)
    failure=result['persons'][0]
    assert not result['candidate_accepted'] and failure['difference_area_m2']<1e-9
    assert failure['difference_length_m']>.5


def test_all_vertices_inside_but_edge_crosses_open_notch():
    disk=person_disk_polygons([[0,0]],[1.])[0]
    coordinates=np.asarray(disk.exterior.coords)
    midpoint=(coordinates[0]+coordinates[1])/2
    notch=box(midpoint[0]-.0002,midpoint[1]-.0001,2.1,midpoint[1]+.0001)
    region=box(-2,-2,2,2).difference(notch)
    assert not region.interiors
    assert all(region.covers(Point(p)) for p in disk.exterior.coords)
    assert not audit(region,radius=1.)['candidate_accepted']


def test_hole_in_disk_interior_is_checked():
    region=Polygon([[-1,-1],[1,-1],[1,1],[-1,1]],
        holes=[[[-.05,-.05],[.05,-.05],[.05,.05],[-.05,.05]]])
    result=audit(region,radius=.5)
    assert result['region_holes']==1 and not result['candidate_accepted']
    assert result['persons'][0]['exact_outside_vertex_witness'] is None
    assert exact_point_location((0.,0.),region)==-1
    assert exact_point_location((.05,0.),region)==0


@pytest.mark.parametrize('scale',[.001,1.,100.])
@pytest.mark.parametrize('angle',[0.,17.,90.])
def test_controlled_transform_with_declared_clearance(scale,angle):
    # Margin dwarfs polygon sagitta; no claim of exact invariance for a rotated
    # tangential disk because a newly generated P32 stays axis-aligned.
    for accepted,region in [(True,box(-1,-1,1,1)),(False,box(-1,-1,.1,1))]:
        transformed=affinity.translate(affinity.rotate(affinity.scale(region,xfact=scale,yfact=scale,origin=(0,0)),angle,origin=(0,0)),30.,-12.)
        assert audit(transformed,(30.,-12.),.2*scale)['candidate_accepted']==accepted


def test_distinct_radii_and_components_remain_explicit():
    region=box(-1,-1,1,1)
    result=audit_person_disk_coverage(region,[[-.6,0.],[.6,0.]],[.2,.5])
    assert [r['raw_strict_covers'] for r in result['persons']]==[True,False]
    multipolygon=box(-2,-1,-.5,1).union(box(.5,-1,2,1))
    result=audit_person_disk_coverage(multipolygon,[[-1.,0.],[1.,0.]],[.2,.2])
    assert result['candidate_accepted'] and result['region_components']==2
    # Coverage does not assert support for one controller curve.


def test_exact_saved_float_distance_certificate():
    region=box(0,0,1,1); point=(-.01,.5)
    result=exact_point_boundary_distance(point,region)
    value=result['distance_squared_m2']
    assert Fraction(int(value['numerator']),int(value['denominator']))==Fraction(.01)**2
    bounds=result['distance_bracket']
    assert Fraction(bounds['lower_m'])<=Fraction(.01)<Fraction(bounds['upper_m'])
    assert result['exact_location']==-1


def test_invalid_reference_does_not_become_acceptance():
    with pytest.raises(ValueError): audit_person_disk_coverage(box(0,0,1,1),[[0,0]],[-.2])
    with pytest.raises(ValueError): audit_person_disk_coverage(Polygon(),[[0,0]],[.2])


def test_trace_accounts_for_disconnected_intermediate_disk_union():
    from udc.experiments.coverage_contract import polygon_structure
    multi=Point(0,0).buffer(.2).union(Point(1,0).buffer(.2))
    assert polygon_structure(multi)=={'holes':0,'components':2}


def test_exact_gap_enclosure_catches_interior_hole_without_vertex_witness():
    reference=box(-.5,-.5,.5,.5)
    region=box(-1,-1,1,1).difference(box(-.01,-.2,.01,.2))
    proof=exact_convex_reference_gap_enclosure(reference,region)
    assert proof['reference_outside_open_subsegments']==0
    assert proof['region_boundary_subsegments_in_reference_interior']==4
    assert not proof['no_gap_boundaries_found']


def test_exact_gap_enclosure_checks_all_segments_and_tangency():
    reference=box(-.5,-.5,.5,.5)
    assert exact_convex_reference_gap_enclosure(reference,box(-.5,-1,1,1))['no_gap_boundaries_found']
    proof=exact_convex_reference_gap_enclosure(reference,box(-1,-1,.49,1))
    assert proof['reference_outside_open_subsegments']==3
    assert proof['region_boundary_subsegments_in_reference_interior']==1
    assert proof['gap_enclosing_box_exact']['display_minimum'][0]==.49


def test_frozen_diagnostic_config_cannot_silently_enable_acceptance():
    from pathlib import Path
    import yaml
    from udc.experiments.coverage_contract import validate_contract_spec
    spec=yaml.safe_load(Path('configs/step1/coverage_geometry_contract_v1.yaml').read_text())
    validate_contract_spec(spec)
    for key,value in [('approximate_acceptance_enabled',True),('coverage_acceptance_tolerance_m',1e-7),
            ('controller_executions',1),('canonical_reference_quad_segs',16)]:
        with pytest.raises(ValueError):validate_contract_spec(dict(spec,**{key:value}))
