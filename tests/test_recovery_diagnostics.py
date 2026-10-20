import json
from pathlib import Path
from dataclasses import replace

import numpy as np
import pytest
from shapely import from_wkb
from shapely.geometry import Point, Polygon, LineString

from udc.navigation import paths as p
from udc.analysis.recovery_diagnostics import matching_evidence


def contact_fixture():
    data=json.loads((Path(__file__).parent/'fixtures/recovery_boundary_contact.json').read_text(encoding='utf-8'))
    room,physical,obstacle=[from_wkb(bytes.fromhex(data[k])) for k in
        ('room_polygon_wkb','physical_obstacle_wkb','conservative_obstacle_wkb')]
    domain=p.NavigationDomain(room,physical,obstacle,room.difference(obstacle),room.difference(physical),
        .55,.55,.1,.11,'full-precision-boundary-contact')
    return data,domain


def test_ulp_projection_contact_enters_graph_with_true_state_prefix_and_full_validation():
    data,domain=contact_fixture(); start=np.array(data['start']); original=start.copy(); target=np.array(data['target'])
    assert domain.physical_free.covers(Point(start)) and not domain.free.covers(Point(start))
    assert abs(domain.signed_free_distance(start))<1e-14
    planner,prefix=p._prepare_start(start,domain)
    assert np.array_equal(start,original) and np.array_equal(prefix,start)
    assert domain.free.covers(Point(planner)) and np.linalg.norm(planner-start)<1.01e-8
    assert p.segment_in_free(start,planner,domain)
    graph=p._visibility_graph(domain.free,domain.conservative_obstacle)
    path,_=p._shortest(planner,target,domain.free,domain.conservative_obstacle,graph=graph)
    assert path is not None
    full=np.vstack([prefix,path])
    assert np.array_equal(full[0],original) and np.array_equal(full[-1],target)
    assert all(p.segment_in_free(a,b,domain) and domain.physical_free.covers(LineString([a,b])) for a,b in zip(full[:-1],full[1:]))


def test_existing_installed_suffix_is_an_independent_missing_query_witness():
    data,domain=contact_fixture(); witness=np.array(data['witness_suffix'])
    assert np.array_equal(witness[0],data['start']) and np.array_equal(witness[-1],data['target'])
    assert all(p.segment_in_free(a,b,domain) for a,b in zip(witness[:-1],witness[1:]))


def test_normal_fallback_cannot_cross_physical_free_or_wall_boundary():
    data,domain=contact_fixture(); start=np.array(data['start'])
    # The physical free region still contains the real start, but excludes every
    # outward candidate. Numerical contact cannot authorize crossing this wall.
    restricted=domain.physical_free.intersection(Polygon([(0,0),(30,0),(30,start[1]),(0,start[1])]))
    guarded=replace(domain,physical_free=restricted,free=domain.free.intersection(restricted),geometry_sha256='different-physical-domain')
    assert p._boundary_node(start,guarded) is None


def test_graphs_for_distinct_domains_do_not_share_old_edges():
    room=np.array([10.,10.]); guides=np.array([[1.,5.]]); targets=np.array([[9.,5.]])
    short=Polygon([(4.,4.),(6.,4.),(6.,6.),(4.,6.)])
    wall=Polygon([(4.,0.),(6.,0.),(6.,10.),(4.,10.)])
    first=p.navigate_and_assign(guides,targets,room,.2,short,.2)
    assert first.reachable[0,0]
    with pytest.raises(ValueError,match='PATH_UNREACHABLE'):
        p.navigate_and_assign(guides,targets,room,.2,wall,.2)
    again=p.navigate_and_assign(guides,targets,room,.2,short,.2)
    assert np.array_equal(first.assignment,again.assignment)
    assert np.array_equal(first.paths[0],again.paths[0])


def test_missing_query_row_has_finite_graph_witness_without_claiming_unreachability():
    found=np.ones((4,4),bool); found[2]=False; cost=np.where(found,1.,np.inf)
    result=matching_evidence(found,cost)
    assert result['maximum_size']==3 and result['unmatched_sources']==[2]
    assert result['hall_witness']['sources']==[2] and result['hall_witness']['neighbors']==[]
    assert result['hall_witness']['deficit']==1 and not result['hungarian_all_finite']
    assert 'finite' in result['hall_witness']['scope']


def test_orchestration_error_keeps_original_runner_metrics(monkeypatch,tmp_path):
    from udc.experiments import recovery_diagnostics as module
    task={'task_id':'test','original_status':'SUCCESS','original_physical_success':True}
    def failed(root,row,attempt):
        directory=root/attempt/row['task_id']; directory.mkdir(parents=True)
        (directory/'metrics.json').write_text('{"success":true,"termination_status":"SUCCESS"}',encoding='utf-8')
        raise RuntimeError('post-run audit rejected')
    monkeypatch.setattr(module,'execute',failed)
    result=module.safe_execute((str(tmp_path),task,'audit_fixture'))
    assert result['status']=='EXPERIMENT_ERROR'
    directory=tmp_path/'audit_fixture'/'test'
    assert json.loads((directory/'metrics.json').read_text())=={'success':True,'termination_status':'SUCCESS'}
    assert (directory/'orchestration_error.json').exists()
