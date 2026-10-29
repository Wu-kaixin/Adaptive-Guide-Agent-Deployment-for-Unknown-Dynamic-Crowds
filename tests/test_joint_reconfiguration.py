from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from udc.multigroup.joint_reconfiguration import execute, interval_audit


def setup():
    return SimpleNamespace(free=box(.4, .4, 9.6, 9.6)), dict(
        guide_distance_m=.75, wall_distance_m=.4, guide_radius_m=.2,
        clearance_m=.35, dt_s=.1, max_speed_m_s=1., deadline_s=100., hold_steps=2)


def test_crossing_pair_rejected_even_with_safe_endpoints():
    domain, config = setup()
    report = interval_audit([[2, 5], [8, 5]], [[8, 5], [2, 5]], domain, [10, 10], config)
    assert not report['safe']
    assert report['minimum_pair_distance_m'] == 0
    assert report['closest_pair_interval_fraction'] == .5


def test_collective_translation_passes_and_obeys_speed_and_hold():
    domain, config = setup()
    start = np.array([[2., 3.], [3., 3.]])
    target = start + [1., 0.]
    result = execute(start, target, domain, [10, 10], config)
    assert result.status == 'ARRIVED'
    np.testing.assert_array_equal(result.positions[-1], target[result.assignment])
    assert np.linalg.norm(result.velocities, axis=2).max() <= 1. + 1e-12
    assert np.all(result.velocities[-2:] == 0)
    assert all(interval_audit(a, b, domain, [10, 10], config)['safe']
               for a, b in zip(result.positions[:-1], result.positions[1:]))


def test_idle_robot_remains_an_obstacle():
    domain, config = setup()
    report = interval_audit([[2, 5], [5, 5]], [[8, 5], [5, 5]], domain, [10, 10], config)
    assert not report['safe']


def test_static_hole_rejected_before_motion():
    domain, config = setup()
    domain.free = domain.free.difference(box(4, 4, 6, 6))
    result = execute([[2, 5]], [[8, 5]], domain, [10, 10], config)
    assert result.status == 'JOINT_CANDIDATE_REJECTED'
    assert len(result.positions) == 1


def test_budget_includes_hold_and_never_extends_deadline():
    domain, config = setup()
    config['deadline_s'] = .2
    result = execute([[2, 5]], [[3, 5]], domain, [10, 10], config)
    assert result.status == 'TIMEOUT'
    assert len(result.positions) == 1


def test_invalid_coordinates_are_program_errors():
    domain, config = setup()
    with pytest.raises(ValueError):
        execute([[float('nan'), 1]], [[2, 2]], domain, [10, 10], config)


def test_assignment_cannot_omit_duplicate_or_invent_a_target():
    domain, config = setup()
    with pytest.raises(ValueError):
        execute([[2, 3], [3, 3]], [[2, 4], [3, 4]], domain, [10, 10], config,
                fixed_assignment=np.array([0, 0]))


def test_explicit_reassignment_checked_for_pair_crossing():
    domain, config = setup()
    result = execute([[2, 5], [8, 5]], [[2, 5], [8, 5]], domain, [10, 10], config,
                     fixed_assignment=np.array([1, 0]))
    assert result.status == 'JOINT_CANDIDATE_REJECTED'
    assert len(result.positions) == 1


def test_reassigned_candidate_preserves_targets_and_safety():
    from udc.multigroup.joint_reconfiguration import execute_reassigned
    domain, config = setup()
    result = execute_reassigned([[2, 3], [3, 3]], [[3, 3], [4, 3]], domain, [10, 10], config)
    assert result.status == 'ARRIVED'
    assert sorted(result.assignment.tolist()) == [0, 1]
    np.testing.assert_array_equal(result.positions[-1], np.array([[3, 3], [4, 3]])[result.assignment])


def test_observed_uncertainty_is_checked_on_complete_interval():
    from udc.interfaces import Observation
    from udc.multigroup.observation import BoundedMeasurement
    domain, config = setup()
    measurement = BoundedMeasurement(Observation(np.array([[5., 5.]]), np.array([.2]), np.ones(1)), .12, 'bounded')
    report = interval_audit([[2, 5.7]], [[8, 5.7]], domain, [10, 10], config, measurement)
    assert not report['safe']
    assert not report['static_segments_ok']
