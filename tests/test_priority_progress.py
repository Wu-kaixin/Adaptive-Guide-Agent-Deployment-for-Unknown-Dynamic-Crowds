"""Priority-yield progress is the active guide's polyline, not the mean."""
from types import SimpleNamespace

import numpy as np
import pytest

from udc.controller import ABCGController
from udc.controller.progress import ordinary_stall, priority_path_stall, stalled_this_step
from udc.safety import Safety


WINDOW = 20
TOLERANCE = 0.03


def _controller():
    safety = Safety(0.75, 0.75, 0.55, 1.0, numeric_tolerance=1e-7)
    config = {
        'waypoint_tolerance': 0.05,
        'position_tolerance': TOLERANCE,
        'gain': 1.5,
        'max_replans': 3,
    }
    controller = ABCGController(config, safety, np.array([30.0, 30.0]), protocol='fixed_n', fixed_n=2)
    controller.assignment = np.array([0, 1])
    controller.plan = SimpleNamespace(positions=np.array([[5.0, 0.0], [0.0, 0.0]]))
    controller.paths = {
        0: np.array([[0.0, 0.0], [5.0, 0.0]]),
        1: np.array([[0.0, 1.0], [0.0, 0.0]]),
    }
    controller.waypoint = {0: 1, 1: 1}
    controller.priority_mode = True
    controller.priority_order = [0, 1]
    controller.priority_index = 0
    controller.replans = 3
    controller._control_step = 1
    return controller


def _reference(controller, guide, position):
    controller._priority_reference = {
        'guide': guide,
        'waypoint': int(controller.waypoint[guide]),
        'path_sha256': controller._path_sha256(guide),
        'remaining_before_m': controller.guide_remaining_path(guide, position),
    }


def _advance(controller, position, step_m):
    """One executed step of guide 0. The budget and the call clock stay put."""
    before = np.array([position, [1.0, 1.0]])
    after = before.copy()
    after[0] = position + step_m
    _reference(controller, 0, before[0])
    budget = controller.replans
    clock = controller._control_step
    stalled = controller.observe_priority_progress(after, 1.0, WINDOW, TOLERANCE)
    assert controller.replans == budget
    assert controller._control_step == clock
    return stalled, after[0]


def test_ordinary_stall_still_needs_the_mean_speed_and_the_mean_path():
    flat = [1.0] * (WINDOW + 1)
    assert ordinary_stall(flat, WINDOW, TOLERANCE, 1.0, 0.01, TOLERANCE)
    assert not ordinary_stall(flat, WINDOW, TOLERANCE, 1.0, 1.0, TOLERANCE)
    moving = [1.0, *([1.0] * (WINDOW - 1)), 1.0 - 0.2]
    assert not ordinary_stall(moving, WINDOW, TOLERANCE, 1.0, 0.0, TOLERANCE)
    assert not ordinary_stall(flat, WINDOW, TOLERANCE, 0.01, 0.0, TOLERANCE)


def test_priority_window_keeps_a_guide_whose_own_path_is_decreasing():
    # Twenty steps of 0.1 m are progress even if every other guide yields.
    samples = [3.0 - 0.1 * step for step in range(WINDOW + 1)]
    assert samples[0] - samples[-1] == 2.0
    assert not priority_path_stall(samples, WINDOW, TOLERANCE, 1.0)
    assert not stalled_this_step(True, False, [1.0] * (WINDOW + 1), WINDOW, TOLERANCE, 1.0, 0.0, TOLERANCE)


def test_priority_window_stalls_when_the_polyline_does_not_decrease():
    samples = [2.0] * (WINDOW + 1)
    assert priority_path_stall(samples, WINDOW, TOLERANCE, 1.0)
    # A large speed is not an argument of this predicate.
    assert priority_path_stall(samples, WINDOW, TOLERANCE, 1.0)
    assert not priority_path_stall(samples, WINDOW, TOLERANCE, 0.01)


def test_lateral_motion_does_not_count_as_priority_progress():
    controller = _controller()
    position = np.array([0.0, 0.0])
    stalled = False
    for _ in range(WINDOW):
        stalled, position = _advance(controller, position, np.array([0.0, 0.1]))
    assert stalled
    assert controller.guide_remaining_path(0, position) > 5.0
    assert len(controller.coordination['progress_reference_changes']) == 1


def test_repeated_samples_of_the_same_guide_do_not_refresh_the_window():
    controller = _controller()
    position = np.array([0.0, 0.0])
    stalled = False
    for _ in range(WINDOW):
        # 0.001 m per step is 0.02 m over the window, below 0.03 m.
        stalled, position = _advance(controller, position, np.array([0.001, 0.0]))
    assert stalled
    assert len(controller._priority_samples) == WINDOW + 1
    assert len(controller.coordination['progress_reference_changes']) == 1
    assert controller.replans == 3


def test_a_reference_change_discards_the_previous_window():
    controller = _controller()
    position = np.array([0.0, 0.0])
    for _ in range(WINDOW):
        stalled, position = _advance(controller, position, np.array([0.0, 0.0]))
    assert stalled
    controller.waypoint[0] = 1
    controller.paths[0] = np.array([[0.0, 0.0], [4.0, 0.0]])
    controller._control_step = 50
    stalled, _position = _advance(controller, np.array([0.0, 0.0]), np.array([0.0, 0.0]))
    assert not stalled
    assert len(controller._priority_samples) == 2
    change = controller.coordination['progress_reference_changes'][-1]
    assert change['kind'] == 'reference_changed'
    assert change['previous_remaining_m'] is not None
    assert controller.replans == 3


def test_handoff_uses_the_measured_position_and_a_displaced_guide_stays_assigned():
    controller = _controller()
    # 0.04 m is outside the 0.03 m arrival tolerance, so guide 0 stays priority.
    assert controller._priority_guide(np.array([[5.04, 0.0], [0.4, 0.0]])) == 0
    assert controller._priority_guide(np.array([[5.0, 0.0], [0.4, 0.0]])) == 1
    assert controller.priority_mode
    # Guide 0 was already passed. It is outside tolerance again when guide 1 arrives.
    assert controller._priority_guide(np.array([[5.05, 0.0], [0.0, 0.0]])) is None
    assert not controller.priority_mode
    outside = controller.coordination['exit_outside_tolerance']
    assert controller.coordination['exit_reason'] == 'queue_finished_with_guides_outside_tolerance'
    assert [item['guide'] for item in outside] == [0]
    assert outside[0]['distance_m'] == pytest.approx(0.05)
    assert np.array_equal(controller.assignment, np.array([0, 1]))
    assert controller.replans == 3
