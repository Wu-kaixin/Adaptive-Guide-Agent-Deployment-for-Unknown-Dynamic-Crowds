import numpy as np
import pytest

from udc.analysis.exact_convex_hull import hull_indices, facet_directions
from udc.analysis.convex_transfer_audit import DIRECTIONS, support_violations
from udc.analysis.convex_transfer_replay import _verify_hull_certificate


def test_all_facets_expose_a_real_inclusion_failure_missed_by_sixteen_directions():
    source=np.array([[0.,0.],[2.,-3.],[3.,2.]])
    target=np.array([[1.-3./1024,-1.5-2./1024]])
    assert not support_violations(source,target,directions=DIRECTIONS)
    normals,_=facet_directions(source)
    failed=support_violations(source,target,directions=normals)
    assert [row['direction'] for row in failed]==[[-3,-2]]


def test_exact_hull_keeps_binary_protrusion_and_removes_duplicate_collinear_vertices():
    points=[[0.,0.],[1.,0.],[1.,1.],[0.,1.],[.5,0.],[0.,0.],[.5,-2.**-100]]
    assert set(hull_indices(points))=={0,1,2,3,6}
    normals,_=facet_directions(points)
    assert len(normals)==5
    assert not support_violations(points,points,directions=normals)


def test_collinear_inputs_have_no_silent_2d_facet_certificate():
    assert set(hull_indices([[0.,0.],[1.,1.],[2.,2.]]))=={0,2}
    with pytest.raises(ValueError,match='nondegenerate'):
        facet_directions([[0.,0.],[1.,1.],[2.,2.]])


def test_independent_facet_verifier_rejects_a_missing_hull_corner():
    points=[[0.,0.],[1.,0.],[1.,1.],[0.,1.]]
    normals,indices=facet_directions(points)
    _verify_hull_certificate(points,{'source_hull_vertex_indices':list(indices),'axes_added':False},list(map(list,normals)))
    with pytest.raises(ValueError,match='hull facet'):
        _verify_hull_certificate(points,{'source_hull_vertex_indices':[0,1,2],'axes_added':False},[[0,-1],[1,0],[-1,1]])
