"""Joint pair projection and the priority along-path nominal."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.optimize import minimize
from shapely.geometry import Polygon

from udc.controller import ABCGController
from udc.interfaces import Observation
from udc.safety import Safety, is_safe, path_clearances, safe_velocity
from udc.safety.coordinate import joint_progress_velocity
from udc.safety.filter import _planning_progress_ok


def _limits():
    return Safety(0.75, 0.75, 0.2, 1.0, numeric_tolerance=1e-7)


def _open_room():
    return np.array([[5.0, 5.0], [5.75, 5.0]]), np.array([[5.0, 9.0]]), np.array([10.0, 10.0])


def test_pair_constraint_splits_the_relative_velocity():
    """One tight pair: the projection moves both guides, equally and oppositely."""
    guides, people, room = _open_room()
    limits = _limits()
    nominal = np.array([[1.0, 0.0], [0.0, 0.0]])
    result = safe_velocity(guides, nominal, people, room, 0.1, limits)
    assert result.status == 'SOLVED'
    # n = (1, 0) points from guide 0 to guide 1. Closing at 1 m/s is split in half.
    np.testing.assert_allclose(result.velocity, [[0.5, 0.0], [0.5, 0.0]], atol=1e-6)
    checked = path_clearances(guides, guides + 0.1 * result.velocity, people, room)
    assert is_safe(checked, limits)
    assert checked['guides'] >= limits.guide_distance - limits.numeric_tolerance


def test_conflicting_nominals_project_near_zero_but_one_sided_motion_does_not():
    guides, people, room = _open_room()
    limits = _limits()
    head_on = safe_velocity(guides, np.array([[1.0, 0.0], [-1.0, 0.0]]), people, room, 0.1, limits)
    assert head_on.status == 'SOLVED'
    assert np.linalg.norm(head_on.velocity) < 1e-4

    yielded = safe_velocity(guides, np.array([[1.0, 0.0], [0.0, 0.0]]), people, room, 0.1, limits)
    assert yielded.velocity[0, 0] > 0.4
    assert yielded.velocity[1, 0] > 0.4
    reference = _project_joint(np.array([[1.0, 0.0], [0.0, 0.0]]), guides, people, room, 0.1, limits)
    assert reference['success']
    assert abs(reference['objective'] - 0.5 * np.sum((yielded.velocity - [[1.0, 0.0], [0.0, 0.0]]) ** 2)) < 1e-5
    np.testing.assert_allclose(reference['velocity'], yielded.velocity, atol=1e-4)


def _project_joint(nominal, guides, people, room, dt, limits):
    """Independent Euclidean projection onto the same linear rows and speed balls."""
    from udc.safety.filter import _obstacle_support
    del _obstacle_support
    n = len(guides)
    rows, bounds = [], []

    def add(i, normal, bound, j=None):
        row = np.zeros(2 * n)
        row[2 * i:2 * i + 2] = normal
        if j is not None:
            row[2 * j:2 * j + 2] = -normal
        rows.append(row)
        bounds.append(bound)

    for i in range(n):
        for j in range(i):
            delta = guides[i] - guides[j]
            distance = float(np.linalg.norm(delta))
            bound = (limits.guide_distance + 1e-8 - distance) / dt
            if bound > -2 * limits.max_speed:
                add(i, delta / distance, bound, j)
    matrix = np.asarray(rows, float)
    bound_array = np.asarray(bounds, float)
    x0 = nominal.ravel().copy()

    def objective(vector):
        return 0.5 * float(np.sum((vector - x0) ** 2))

    constraints = [{'type': 'ineq', 'fun': lambda vector, row=row, bound=bound: float(row @ vector - bound)}
                   for row, bound in zip(matrix, bound_array)]
    for guide in range(n):
        constraints.append({
            'type': 'ineq',
            'fun': lambda vector, guide=guide: float(
                limits.max_speed ** 2 - vector[2 * guide:2 * guide + 2] @ vector[2 * guide:2 * guide + 2]),
        })
    solved = minimize(objective, x0, constraints=constraints, method='SLSQP',
                      options={'maxiter': 80, 'ftol': 1e-12, 'disp': False})
    return {'success': bool(solved.success), 'velocity': solved.x.reshape(n, 2),
            'objective': objective(solved.x)}


def test_unconverged_projection_is_not_installed(monkeypatch):
    def fake(nominal, matrix, bounds, limits):
        return np.array([[4.0, 0.0], [-4.0, 0.0]]), 1.0, 200, False

    monkeypatch.setattr('udc.safety.filter._project_velocity', fake)
    guides, people, room = _open_room()
    result = safe_velocity(guides, np.array([[1.0, 0.0], [0.0, 0.0]]), people, room, 0.1, _limits())
    assert result.fallback_used
    assert result.status == 'NUMERICAL_FALLBACK'
    np.testing.assert_allclose(result.velocity, 0.0)


def test_linear_planning_clearance_does_not_install_a_penetrating_segment():
    """The nearest-boundary halfplane can miss a side wall of a notch."""
    notch = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]).difference(
        Polygon([(0.3, 0.4), (1.7, 0.4), (1.7, 2.0), (0.3, 2.0)]))
    position = np.array([[0.36, 0.45]])
    nominal = np.array([[-1.0, 0.0]])
    people = np.array([[8.0, 8.0]])
    room = np.array([10.0, 10.0])
    limits = _limits()
    end = position + 0.1 * nominal
    assert not _planning_progress_ok(position[0], end[0], notch)
    result = safe_velocity(position, nominal, people, room, 0.1, limits, navigation_obstacle=notch)
    executed = position + 0.1 * result.velocity
    assert _planning_progress_ok(position[0], executed[0], notch)
    assert not np.allclose(result.velocity, nominal)
    assert is_safe(path_clearances(position, executed, people, room), limits)


class _Box:
    def __init__(self):
        self.free = Polygon([(0.5, 0.5), (9.5, 0.5), (9.5, 9.5), (0.5, 9.5)])
        self.physical_free = Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
        self.conservative_obstacle = self.physical_free.difference(self.free)
        self.geometry_sha256 = 'box'

    def signed_free_distance(self, point):
        from shapely.geometry import Point
        geometry = Point(np.asarray(point, dtype=float))
        distance = float(self.free.boundary.distance(geometry))
        return distance if self.free.covers(geometry) else -distance


def _priority_controller():
    domain = _Box()
    safety = _limits()
    config = {
        'waypoint_tolerance': 0.05,
        'position_tolerance': 0.03,
        'gain': 1.5,
        'max_replans': 3,
    }
    controller = ABCGController(config, safety, np.array([10.0, 10.0]), protocol='fixed_n', fixed_n=2)
    controller.navigation_domain = domain
    controller.assignment = np.array([0, 1])
    controller.plan = SimpleNamespace(positions=np.array([[2.3, 5.0], [8.0, 8.0]]))
    controller.paths = {
        0: np.array([[2.0, 5.0], [2.3, 5.0]]),
        1: np.array([[2.75, 5.0], [8.0, 8.0]]),
    }
    controller.waypoint = {0: 1, 1: 1}
    controller.priority_mode = True
    controller.priority_order = [0, 1]
    controller.priority_index = 0
    controller.replans = 3
    return controller


def test_priority_command_lets_the_blocking_guide_move_and_does_not_refresh_the_budget():
    controller = _priority_controller()
    positions = np.array([[2.0, 5.0], [2.75, 5.0]])
    observation = Observation(np.array([[8.0, 8.0]]), np.array([0.2]), np.ones(1))
    nominal, result = controller.step(observation, positions, 0.1)
    assert controller.replans == 3
    assert controller.coordination['steps'] == 1
    assert controller.coordination['fallback_steps'] == 0
    # 0.30 m aim at dt 0.1 requests the speed cap, not gain * distance = 0.45.
    assert np.linalg.norm(nominal[0]) == pytest.approx(1.0)
    assert np.linalg.norm(nominal[1]) == 0.0
    assert result.velocity[0, 0] > 0.4
    assert result.velocity[1, 0] > 0.4
    checked = path_clearances(positions, positions + 0.1 * result.velocity, observation.positions, controller.room)
    assert is_safe(checked, limits := _limits())
    assert checked['guides'] >= limits.guide_distance - 1e-7
    assert controller.coordination['max_other_filtered_speed_m_s'] > 0.4


def test_illegal_priority_aim_keeps_the_slower_command_and_the_budget():
    controller = _priority_controller()
    controller.paths[0] = np.array([[0.7, 5.0], [0.2, 5.0]])
    positions = np.array([[0.7, 5.0], [6.0, 6.0]])
    observation = Observation(np.array([[8.0, 8.0]]), np.array([0.2]), np.ones(1))
    nominal, _result = controller.step(observation, positions, 0.1)
    assert controller.coordination['illegal_aim_steps'] == 1
    assert controller.replans == 3
    distance = float(np.linalg.norm(np.array([0.2, 5.0]) - positions[0]))
    proportional = min(1.0, 1.5 * distance)
    assert proportional < 1.0
    assert np.linalg.norm(nominal[0]) == pytest.approx(proportional)
    assert np.linalg.norm(nominal[1]) == 0.0


def test_progress_constraint_keeps_the_arrival_step_and_moves_the_neighbor():
    guides, people, room = _open_room()
    limits = _limits()
    nominal = np.array([[1.0, 0.0], [0.0, 0.0]])
    plain = safe_velocity(guides, nominal, people, room, 0.1, limits)
    assert plain.velocity[0, 0] < 0.9
    enforced, _elapsed = joint_progress_velocity(
        guides, nominal, people, room, 0.1, limits, None, 0, np.array([1.0, 0.0]), 1.0)
    assert enforced is not None
    assert enforced.fallback_used is False
    assert enforced.velocity[0, 0] > 0.99
    assert enforced.velocity[1, 0] > 0.9
    assert np.linalg.norm(enforced.velocity, axis=1).max() <= 1.0 + 1e-8
    checked = path_clearances(guides, guides + 0.1 * enforced.velocity, people, room)
    assert is_safe(checked, limits)


def test_progress_solve_rejects_a_penetrating_segment_and_a_failed_solver(monkeypatch):
    notch = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]).difference(
        Polygon([(0.3, 0.4), (1.7, 0.4), (1.7, 2.0), (0.3, 2.0)]))
    position = np.array([[0.36, 0.45]])
    nominal = np.array([[-1.0, 0.0]])
    people = np.array([[8.0, 8.0]])
    room = np.array([10.0, 10.0])
    limits = _limits()
    rejected, elapsed = joint_progress_velocity(
        position, nominal, people, room, 0.1, limits, notch, 0, np.array([-1.0, 0.0]), 1.0)
    assert rejected is None
    assert elapsed >= 0.0

    def boom(*args, **kwargs):
        raise ValueError('solver failed')

    monkeypatch.setattr('udc.safety.coordinate.minimize', boom)
    failed, _elapsed = joint_progress_velocity(
        position, nominal, people, room, 0.1, limits, None, 0, np.array([-1.0, 0.0]), 1.0)
    assert failed is None


def test_failed_progress_solve_does_not_replace_the_filter_or_the_budget(monkeypatch):
    controller = _priority_controller()
    monkeypatch.setattr('udc.controller.abcg.joint_progress_velocity', lambda *args, **kwargs: (None, 0.01))
    positions = np.array([[2.0, 5.0], [2.75, 5.0]])
    observation = Observation(np.array([[8.0, 8.0]]), np.array([0.2]), np.ones(1))
    nominal, result = controller.step(observation, positions, 0.1)
    assert controller.replans == 3
    assert controller.coordination['enforce_rejected'] == 1
    assert controller.coordination['enforce_installed'] == 0
    assert np.linalg.norm(nominal[1]) == 0.0
    assert result.velocity[0, 0] == pytest.approx(0.5, abs=1e-4)
    assert not result.fallback_used
