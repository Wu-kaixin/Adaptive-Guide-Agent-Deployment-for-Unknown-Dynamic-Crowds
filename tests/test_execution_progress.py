"""Path-following consistency at a free-space gate.

Recovery accounting and unsafe installation are covered by test_replan_domain.
These tests only add the gate decision introduced for the elongated stall.
"""
from types import SimpleNamespace

import numpy as np
from shapely.geometry import Point, Polygon

from udc.controller import ABCGController
from udc.interfaces import Observation
from udc.navigation import segment_in_free
from udc.safety import Safety


class _Domain:
    """L-shaped free space whose corner shortcut leaves the corridor."""

    def __init__(self):
        horizontal = Polygon([(0.0, 0.97), (1.05, 0.97), (1.05, 1.03), (0.0, 1.03)])
        vertical = Polygon([(0.995, 1.0), (1.02, 1.0), (1.02, 2.2), (0.995, 2.2)])
        self.free = horizontal.union(vertical)
        self.physical_free = Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
        self.conservative_obstacle = self.physical_free.difference(self.free)
        self.geometry_sha256 = 'test-gate'

    def signed_free_distance(self, point):
        geometry = Point(np.asarray(point, dtype=float))
        distance = float(self.free.boundary.distance(geometry))
        return distance if self.free.covers(geometry) else -distance


def _controller(domain=None):
    domain = _Domain() if domain is None else domain
    safety = Safety(0.75, 0.75, 0.2, 1.0, numeric_tolerance=1e-7)
    config = {
        'waypoint_tolerance': 0.05,
        'position_tolerance': 0.03,
        'gain': 1.5,
        'max_replans': 3,
    }
    controller = ABCGController(config, safety, np.array([10.0, 10.0]), protocol='fixed_n', fixed_n=1)
    controller.navigation_domain = domain
    controller.assignment = np.array([0, -1])
    controller.plan = SimpleNamespace(positions=np.array([[1.0, 2.0], [8.0, 8.0]]))
    gate = np.array([1.0, 1.0])
    nxt = np.array([1.0, 2.0])
    controller.paths = {0: np.vstack([np.array([0.2, 1.0]), gate, nxt])}
    controller.waypoint = {0: 1, 1: 1}
    return controller, gate, nxt


def test_free_space_gate_does_not_take_an_illegal_immediate_step():
    domain = _Domain()
    controller, gate, nxt = _controller(domain)
    position = np.array([0.96, 1.0])
    assert domain.signed_free_distance(position) > controller.safety.numeric_tolerance
    assert segment_in_free(position, gate, domain)
    assert segment_in_free(gate, nxt, domain)
    assert not segment_in_free(position, nxt, domain)
    index, velocity = controller._tracking_velocity(position, controller.paths[0], 1, domain, 0.1)
    assert index == 1
    np.testing.assert_allclose(velocity, (gate - position) / 0.1)
    # The same illegal shortcut on the boundary keeps the proportional command.
    boundary = np.array([0.96, 1.03])
    assert domain.signed_free_distance(boundary) <= controller.safety.numeric_tolerance
    assert not segment_in_free(boundary, nxt, domain)
    legacy_index, legacy_velocity = controller._tracking_velocity(
        boundary, controller.paths[0], 1, domain, 0.1)
    assert legacy_index == 1
    np.testing.assert_allclose(legacy_velocity, 1.5 * (gate - boundary))


def test_a_guide_on_the_vertex_takes_the_legal_installed_edge():
    domain = _Domain()
    controller, gate, nxt = _controller(domain)
    assert segment_in_free(gate, nxt, domain)
    index, velocity = controller._tracking_velocity(gate, controller.paths[0], 1, domain, 0.1)
    assert index == 2
    np.testing.assert_allclose(velocity, 1.5 * (nxt - gate))


def test_priority_mode_does_not_command_other_guides():
    domain = _Domain()
    controller, gate, _nxt = _controller(domain)
    controller.assignment = np.array([0, 1])
    controller.paths[1] = np.array([[8.0, 8.0], [8.2, 8.0]])
    controller.priority_mode = True
    controller.priority_order = [0, 1]
    positions = np.array([[0.96, 1.0], [8.0, 8.0]])
    observation = Observation(np.array([[5.0, 5.0]]), np.array([0.2]), np.ones(1))
    nominal, result = controller.step(observation, positions, 0.1)
    assert np.linalg.norm(nominal[1]) == 0.0
    assert np.linalg.norm(nominal[0]) > 0.0
    assert result.velocity.shape == positions.shape


def test_disabling_the_follow_repair_restores_the_legacy_gate():
    domain = _Domain()
    controller, gate, _nxt = _controller(domain)
    controller.follow_valid_step = False
    position = np.array([0.96, 1.0])
    index, velocity = controller._tracking_velocity(position, controller.paths[0], 1, domain, 0.1)
    assert index == 1
    np.testing.assert_allclose(velocity, 1.5 * (gate - position))


def test_budget_exhaustion_is_recorded_without_installing_another_path():
    domain = _Domain()
    controller, _gate, _nxt = _controller(domain)
    controller.replans = controller.config['max_replans']
    positions = np.array([[0.96, 1.0], [8.0, 8.0]])
    observation = Observation(np.array([[5.0, 5.0]]), np.array([0.2]), np.ones(1))
    assert controller.recover(observation, positions, trigger_reason='stall_window') is None
    event = controller.recovery_attempts[-1]
    assert event['classification'] == 'budget_exhausted'
    assert event['budget_consumed'] == 3
    assert event['budget_remaining'] == 0
    assert event['installation_succeeded'] is False
    assert event['trigger_reason'] == 'stall_window'
    assert controller.paths[0].shape == (3, 2)
