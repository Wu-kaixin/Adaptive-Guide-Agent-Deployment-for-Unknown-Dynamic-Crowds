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
    with pytest.raises(ValueError, match='outside conservative free space'):
        controller.recover(observation, positions)
    assert controller.replans == 0
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
