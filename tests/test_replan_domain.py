"""Conservative free-space rules shared by the initial plan and later replans."""
import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from udc.controller import ABCGController
from udc.environment import Environment
from udc.experiments.config import load_config
from udc.navigation import navigate_and_assign, navigation_domain, route_assigned, segment_in_free
from udc.safety import Safety, is_safe, path_clearances, safe_velocity

ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]


def _domain():
    crowd = Polygon([(4, 3), (6, 3), (6, 7), (4, 7)])
    return navigation_domain(np.array([10., 10.]), 0.2, crowd, 0.2), crowd


def test_initial_plan_and_replan_use_the_same_domain():
    domain, crowd = _domain()
    again = navigation_domain(np.array([10., 10.]), 0.2, crowd, 0.2)
    assert domain.geometry_sha256 == again.geometry_sha256
    guides = np.array([[2., 5.]])
    targets = np.array([[8., 5.]])
    assigned = navigate_and_assign(guides, targets, np.array([10., 10.]), 0.2, crowd, 0.2)
    replanned, _ = route_assigned(guides, targets, np.array([0]), np.array([10., 10.]), 0.2, crowd, 0.2)
    np.testing.assert_allclose(assigned.paths[0], replanned[0])
    assert domain.component_index(guides[0]) == 0
    changed = navigation_domain(np.array([10., 10.]), 0.2, Polygon([(4, 3), (6.5, 3), (6.5, 7), (4, 7)]), 0.2)
    assert changed.geometry_sha256 != domain.geometry_sha256


def test_guides_are_not_obstacles_and_boundary_points_stay_accepted():
    domain, _crowd = _domain()
    untouched = domain.geometry_sha256
    # A different guide position cannot change the cached geometry inputs.
    other = navigation_domain(np.array([10., 10.]), 0.2, Polygon([(4, 3), (6, 3), (6, 7), (4, 7)]), 0.2)
    assert other.geometry_sha256 == untouched
    vertex = np.asarray(domain.conservative_obstacle.exterior.coords[0], dtype=float)
    assert domain.free.covers(Point(vertex))
    assert domain.signed_free_distance(vertex) == pytest.approx(0.0, abs=1e-8)
    stored = np.load(__import__('io').BytesIO(_npz(vertex)), allow_pickle=False)['point']
    assert domain.free.covers(Point(stored))
    inward = _inward(domain, 1e-4)
    assert inward is not None
    assert not domain.free.covers(Point(inward))
    assert domain.physical_free.covers(Point(inward))


def test_other_guides_block_replans_and_the_planned_guide_does_not():
    _domain_value, crowd = _domain()
    start = np.array([2., 8.5])
    target = np.array([[8., 8.5], [2., 2.]])
    guides = np.array([start, [5., 8.5]])
    limits = Safety(0.75, 0.75, 0.2, 1.)
    alone, _ = route_assigned(
        guides[:1], target, np.array([0]), np.array([10., 10.]), 0.2, crowd, 0.2,
        people=np.array([[1., 1.]]), limits=limits,
    )
    blocked, _ = route_assigned(
        guides, target, np.array([0, -1]), np.array([10., 10.]), 0.2, crowd, 0.2,
        people=np.array([[1., 1.]]), limits=limits,
    )
    assert len(alone[0]) == 2
    assert len(blocked[0]) > 2
    np.testing.assert_allclose(blocked[0][0], start)


def test_nanometre_boundary_point_is_planned_without_opening_the_shell():
    domain, crowd = _domain()
    start = _inward(domain, 1e-9)
    assert start is not None
    assert domain.signed_free_distance(start) < 0
    assert domain.signed_free_distance(start) > -1e-7
    target = np.array([[8., 5.]])
    guides = np.array([start])
    assigned = navigate_and_assign(guides, target, np.array([10., 10.]), 0.2, crowd, 0.2)
    assert assigned.assignment[0] == 0
    np.testing.assert_allclose(assigned.paths[0][0], start)
    deep = _inward(domain, 0.04)
    with pytest.raises(ValueError, match='PATH_UNREACHABLE'):
        navigate_and_assign(np.array([deep]), target, np.array([10., 10.]), 0.2, crowd, 0.2)
    assert segment_in_free(start, assigned.paths[0][1], domain)
    assert not segment_in_free(deep, target[0], domain)


def test_shell_connection_keeps_the_true_start_and_rejects_an_unsafe_segment():
    domain, crowd = _domain()
    start = _inward(domain, 0.04)
    assert start is not None
    target = np.array([8., 5.])
    guides = np.array([start])
    people = np.array([[1., 1.]])
    limits = Safety(0.3, 0.3, 0.1, 1.0)
    paths, _ = route_assigned(
        guides, target.reshape(1, 2), np.array([0]), np.array([10., 10.]), 0.2, crowd, 0.2,
        people=people, limits=limits,
    )
    np.testing.assert_allclose(paths[0][0], start)
    assert domain.free.covers(Point(paths[0][1]))
    assert not domain.free.covers(Point(paths[0][0]))
    assert segment_in_free(paths[0][1], paths[0][-1], domain) or domain.free.covers(Point(paths[0][1]))
    end = np.array(guides, copy=True)
    end[0] = paths[0][1]
    assert is_safe(path_clearances(guides, end, people, np.array([10., 10.])), limits)
    blocked = np.array([0.5 * (start + paths[0][1])])
    with pytest.raises(ValueError, match='planning-margin connection fails physical clearance'):
        route_assigned(
            guides, target.reshape(1, 2), np.array([0]), np.array([10., 10.]), 0.2, crowd, 0.2,
            people=blocked, limits=Safety(0.75, 0.75, 0.1, 1.0),
        )
    deep = np.array(crowd.representative_point().coords[0])
    with pytest.raises(ValueError, match='outside conservative free space'):
        route_assigned(
            deep.reshape(1, 2), target.reshape(1, 2), np.array([0]), np.array([10., 10.]), 0.2, crowd, 0.2,
            people=people, limits=limits,
        )
    assert not segment_in_free(start, target, domain)


def test_rejected_replan_does_not_replace_the_path_or_command_unfiltered_motion():
    cfg = load_config(ROOT / 'configs/step1/square.yaml')
    env = Environment(cfg)
    observation = env.observe()
    guides = env.initialize_guides(cfg['guides'])
    control, guide = cfg['controller'], cfg['guides']
    safety = Safety(
        2 * guide['radius'] + control['clearance'],
        guide['radius'] + float(observation.radii.max()) + control['clearance'],
        guide['radius'] + control['clearance'], guide['max_speed'], control['safety_numeric_tolerance'],
    )
    controller = ABCGController(control, safety, env.scene.size)
    controller.prepare(observation, guides)
    paths = controller.paths
    active = int(np.flatnonzero(controller.assignment >= 0)[0])
    positions = np.array(guides, copy=True)
    positions[active] = np.asarray(Polygon(controller.boundary.crowd).representative_point().coords[0])
    with pytest.raises(ValueError, match='unsafe actual state'):
        controller.recover(observation, positions)
    # Preflight rejects this actual state before any query or budget consumption.
    assert controller.replans == 0
    assert controller.recovery_attempts[-1]['classification']=='unsafe_actual_state'
    assert controller.recovery_attempts[-1]['query_domain']=='not_queried'
    assert controller.recovery_attempts[-1]['before']==controller.recovery_attempts[-1]['after']
    assert controller.paths is paths
    assert controller.margin_connections == []

    domain = controller._domain()
    outside = _inward(domain, 0.04)
    nominal = np.zeros_like(guides)
    direction = outside - _outward_from(domain, outside)
    nominal[0] = direction / np.linalg.norm(direction)
    # Place the probe guide on the shell and command it deeper into the obstacle.
    probe = np.array(guides, copy=True)
    probe[0] = outside
    people = observation.positions
    result = safe_velocity(
        probe[:1], nominal[:1], people, env.scene.size, 0.1, safety,
        navigation_obstacle=domain.conservative_obstacle,
    )
    end = probe[0] + 0.1 * result.velocity[0]
    assert domain.signed_free_distance(end) + 1e-7 >= min(0.0, domain.signed_free_distance(probe[0]))
    assert not np.allclose(result.velocity[0], nominal[0])


def _recovery_fixture(budget=3):
    from udc.boundary import Boundary
    from udc.deployment import Plan
    cfg=load_config(ROOT / 'configs/step1/static_50.yaml')['controller']
    cfg['max_replans']=budget
    controller=ABCGController(cfg,Safety(0.75,0.75,0.55,1.,1e-7),np.array([10.,10.]))
    crowd=np.array([[2.,2.],[3.,2.],[3.,3.],[2.,3.]])
    ring=np.array([[1.,1.],[9.,1.],[9.,9.],[1.,9.]])
    controller.boundary=Boundary(crowd,ring,np.arange(4)*8.,32.)
    positions=np.array([[4.,8.],[5.,8.]])
    targets=np.array([[5.,8.],[4.,8.]])
    controller.plan=Plan(targets,np.array([0.,1.]),np.array([0.]),1.,'equal_arc',0,'fixture',1.)
    controller.assignment=np.array([0,1])
    controller.paths={i:np.array([positions[i],targets[i]]) for i in range(2)}
    controller.waypoint={0:1,1:1}
    from udc.interfaces import Observation
    obs=Observation(np.array([[2.5,2.5]]),np.array([0.2]),np.ones(1))
    return controller,obs,positions,targets


@pytest.mark.parametrize('budget',[1,3])
def test_occupied_target_replan_can_reach_existing_reassignment_with_same_targets(budget):
    controller,obs,positions,targets=_recovery_fixture(budget)
    if budget==1:
        old_paths=controller.paths
        with pytest.raises(ValueError,match='PATH_UNREACHABLE'):
            controller.recover(obs,positions)
        assert controller.paths is old_paths
        assert controller.replans==1
        np.testing.assert_array_equal(controller.assignment,[0,1])
        assert controller.recover(obs,positions) is None
        assert controller.recovery_attempts[-1]['classification']=='budget_exhausted'
        assert controller.recovery_attempts[-1]['budget_remaining']==0
        return
    assert controller.recover(obs,positions)=='reachable_reassignment'
    assert controller.replans==2 <= controller.config['max_replans']
    np.testing.assert_array_equal(controller.assignment,[1,0])
    np.testing.assert_array_equal(controller.plan.positions,targets)
    np.testing.assert_array_equal(positions,[[4.,8.],[5.,8.]])
    assert [a['accepted'] for a in controller.recovery_attempts]==[False,True]
    assert controller.recovery_attempts[1]['path_validation']
    failed,accepted=controller.recovery_attempts
    assert failed['classification']=='current_guide_occupation'
    assert failed['diagnosis']['release_guaranteed'] is False
    assert failed['diagnosis']['occupants'][0]['guide']==1
    assert failed['query_domain']=='static_with_current_guides'
    assert accepted['query_domain']=='static'
    assert accepted['query_succeeded'] and accepted['validation_succeeded'] and accepted['installation_succeeded']
    assert accepted['budget_consumed']==2 and accepted['budget_remaining']==1
    assert failed['before']==failed['after']
    assert accepted['before']['targets_sha256']==accepted['after']['targets_sha256']
    for guide,path in controller.paths.items():
        np.testing.assert_array_equal(path[0],positions[guide])
        np.testing.assert_array_equal(path[-1],targets[controller.assignment[guide]])


@pytest.mark.parametrize('target,kind',[([5.,5.],'static_endpoint_invalid'),
                                      ([8.,5.],'static_components_disconnected')])
def test_static_failures_are_not_current_guide_occupation(target,kind):
    from udc.navigation.paths import NavigationFailure
    barrier=Polygon([(4.,-1.),(6.,-1.),(6.,11.),(4.,11.)])
    with pytest.raises(NavigationFailure) as captured:
        route_assigned(np.array([[2.,5.]]),np.array([target]),np.array([0]),np.array([10.,10.]),0.2,barrier,0.2)
    assert captured.value.diagnosis['kind']==kind
    assert captured.value.diagnosis['query_domain']=='static'


def test_only_search_failure_does_not_certify_infeasibility(monkeypatch):
    import udc.navigation.paths as module
    _,crowd=_domain()
    monkeypatch.setattr(module,'_shortest',lambda *a,**k:(None,float('inf')))
    with pytest.raises(module.NavigationFailure) as captured:
        route_assigned(np.array([[2.,8.5]]),np.array([[8.,8.5]]),np.array([0]),np.array([10.,10.]),0.2,crowd,0.2)
    assert captured.value.diagnosis['kind']=='search_not_found'
    assert captured.value.diagnosis['infeasibility_proven'] is False


@pytest.mark.parametrize('kind',['incomplete_path_set','unsafe_candidate_path'])
def test_failed_candidate_validation_never_partially_installs(monkeypatch,kind):
    import udc.controller.abcg as module
    from udc.navigation.paths import NavigationResult,NavigationFailure
    controller,obs,positions,targets=_recovery_fixture()
    old_paths=controller.paths
    if kind=='incomplete_path_set':
        candidate={0:old_paths[0]}
    else:
        candidate={i:np.array([positions[i],obs.positions[0],targets[i]]) for i in range(2)}
    monkeypatch.setattr(module,'route_assigned',lambda *a,**k:(candidate,{}))
    nav=NavigationResult(controller.assignment.copy(),tuple(candidate.values()),np.ones(len(candidate)),np.ones((2,2),bool))
    monkeypatch.setattr(module,'navigate_and_assign',lambda *a,**k:nav)
    with pytest.raises(NavigationFailure) as captured:
        controller.recover(obs,positions)
    assert captured.value.diagnosis['kind']==kind
    assert controller.paths is old_paths
    assert controller.replans==2
    assert len(controller.recovery_attempts)==2
    assert all(attempt['query_succeeded'] and not attempt['installation_succeeded']
               and attempt['before']==attempt['after'] for attempt in controller.recovery_attempts)


def test_persistent_query_failures_terminate_with_accounted_budget(monkeypatch):
    import udc.controller.abcg as module
    from udc.navigation.paths import NavigationFailure
    controller,obs,positions,_=_recovery_fixture()
    calls=[]
    def fail(*a,**k):
        calls.append(1)
        raise NavigationFailure('search_not_found','no route found in bounded query',infeasibility_proven=False)
    monkeypatch.setattr(module,'route_assigned',fail)
    monkeypatch.setattr(module,'navigate_and_assign',fail)
    with pytest.raises(NavigationFailure):
        controller.recover(obs,positions)
    assert len(calls)==2 and controller.replans==2
    assert controller.recovery_attempts[-1]['budget_remaining']==1
    assert all(not attempt['accepted'] for attempt in controller.recovery_attempts)


def test_static_visibility_edges_are_shared_per_assignment_and_rebuilt_for_changed_geometry(monkeypatch):
    import udc.navigation.paths as module
    calls=[]
    original=module._visibility_graph
    def record(*args,**kwargs):
        calls.append(args[0].wkb)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'_visibility_graph',record)
    _,crowd=_domain()
    guides=np.array([[2.,4.],[2.,6.]])
    targets=np.array([[8.,4.],[8.,6.]])
    first=navigate_and_assign(guides,targets,np.array([10.,10.]),0.2,crowd,0.2)
    assert len(calls)==1
    assert first.reachable.all()
    assert len(first.paths)==2
    changed=crowd.buffer(0.1)
    navigate_and_assign(guides,targets,np.array([10.,10.]),0.2,changed,0.2)
    assert len(calls)==2 and calls[0]!=calls[1]


def _npz(point):
    import io
    buffer = io.BytesIO()
    np.savez(buffer, point=np.asarray(point, dtype=np.float64))
    buffer.seek(0)
    return buffer.read()


def _inward(domain, distance):
    boundary = domain.conservative_obstacle.exterior
    for index, vertex in enumerate(boundary.coords[:-1]):
        nxt = np.asarray(boundary.coords[index + 1], dtype=float)
        start = np.asarray(vertex, dtype=float)
        edge = nxt - start
        length = np.linalg.norm(edge)
        if length < 1e-6:
            continue
        tangent = edge / length
        normal = np.array([tangent[1], -tangent[0]])
        midpoint = 0.5 * (start + nxt)
        for sign in (1.0, -1.0):
            candidate = midpoint + sign * normal * distance
            if (domain.conservative_obstacle.covers(Point(candidate))
                    and domain.physical_free.covers(Point(candidate))
                    and not domain.free.covers(Point(candidate))):
                return candidate
    return None


def _outward_from(domain, point):
    boundary = domain.conservative_obstacle.boundary
    return np.asarray(boundary.interpolate(boundary.project(Point(point))).coords[0], dtype=float)
