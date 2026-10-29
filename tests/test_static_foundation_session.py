import json

import numpy as np
import pytest
import yaml

from udc.experiments.static_multigroup import ROOT
from udc.interfaces import Observation, Perception, PedestrianState
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.geometry import ScientificFailure
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.session import StaticDeploymentSession


def frame(name, time=0., reverse=False, demand=(1., 2.)):
    order = [1, 0] if reverse else [0, 1]
    measurement = BoundedMeasurement(Observation(np.array([[10., 10.], [22., 10.]])[order],
        np.array([.2, .2])[order], np.array(demand)[order]), .04, 'bounded')
    return ObservationFrame(name, time, tuple(np.array([42, 7])[order]), measurement)


def session(tmp_path):
    config = yaml.safe_load((ROOT / 'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))['model']
    return StaticDeploymentSession([[2., 2.], [24., 2.]], [40, 30], config,
                                   tmp_path / 'journal', method='counts_phase_guard')


def test_delivery_feedback_row_permutation_and_journal(tmp_path):
    controller = session(tmp_path)
    first = controller.step(frame('one'))
    assert first.metrics['status'] == 'DELIVERED_OBSERVATION_PLAN'
    assert first.metrics['full_task_success'] is None
    second = controller.step(frame('two', controller.next_observation_time_s, reverse=True))
    assert second.metrics['deployment_completed']
    np.testing.assert_array_equal(second.execution.positions[0], first.execution.positions[-1])
    assert tuple(second.metrics['canonical_person_ids']) == (7, 42)
    assert (tmp_path / 'journal/event_000001/receipt.json').is_file()
    with pytest.raises(ValueError):
        controller.positions[0] = 0
    with pytest.raises(ValueError):
        second.execution.positions[0] = 0


def test_changed_static_attribute_halts_and_records_no_motion(tmp_path):
    controller = session(tmp_path)
    controller.step(frame('one'))
    positions = controller.positions.copy()
    record = controller.step(frame('changed', controller.next_observation_time_s, demand=(1., 3.)))
    assert record.metrics['status'] == 'STATIC_ATTRIBUTES_CHANGED'
    assert controller.halted and len(record.execution.velocities) == 0
    np.testing.assert_array_equal(controller.positions, positions)
    metrics = json.loads((tmp_path / 'journal/event_000001/metrics.json').read_text(encoding='utf-8'))
    assert not metrics['deployment_completed']


def test_stale_frame_halts_without_resetting_actual_pose(tmp_path):
    controller = session(tmp_path)
    first = controller.step(frame('one'))
    record = controller.step(frame('old', 0.))
    assert record.metrics['status'] == 'OBSERVATION_RELEASE_VIOLATION'
    np.testing.assert_array_equal(record.execution.positions[0], first.execution.positions[-1])


def test_dynamic_perception_requires_separate_protocol():
    people = PedestrianState(np.array([1]), np.array([[10., 10.]]), np.array([.2]), np.ones(1), np.array([[.1, 0.]]))
    with pytest.raises(ScientificFailure, match='Step 2'):
        ObservationFrame.from_perception('moving', Perception(0., people), .04)


def test_duplicate_person_identity_cannot_enter_controller():
    value = frame('one')
    with pytest.raises(ValueError):
        ObservationFrame('bad', 0., (7, 7), value.measurement)


def test_program_failure_after_motion_keeps_actual_pose_time_and_error_artifacts(tmp_path, monkeypatch):
    from udc.multigroup.execution import Execution
    import udc.multigroup.session as implementation
    controller = session(tmp_path)
    def faulty_executor(start, targets, *args):
        end = start + [0., .1]
        return Execution(np.array([start, end]), np.array([(end - start) / .2]),
                         np.array([0, 1]), 'ARRIVED', (), {'injected_program_error': True})
    monkeypatch.setattr(implementation, 'execute_hybrid', faulty_executor)
    record = controller.step(frame('fault', time=2.))
    assert record.metrics['status'] == 'EXPERIMENT_ERROR'
    assert not record.metrics['deployment_completed']
    assert controller.next_observation_time_s == 2.2
    np.testing.assert_array_equal(controller.positions, np.array([[2., 2.1], [24., 2.1]]))
    assert controller.halted
    folder = tmp_path / 'journal/event_000000'
    assert (folder / 'frame.json').is_file()
    assert (folder / 'observation.npz').is_file()
    assert 'tracking bound' in record.metrics['traceback']


def test_post_delivery_program_error_is_not_ledger_success(tmp_path, monkeypatch):
    from udc.multigroup.observation import StaticTruth
    from udc.analysis.session_audit import audit_journal
    import udc.multigroup.session as implementation
    controller = session(tmp_path)
    def failed_feedback(*args):
        raise RuntimeError('injected feedback failure')
    monkeypatch.setattr(implementation, 'actual_targets', failed_feedback)
    record = controller.step(frame('feedback-fault'))
    assert record.execution.status == 'ARRIVED'
    assert record.metrics['status'] == 'EXPERIMENT_ERROR'
    assert not record.metrics['deployment_completed']
    assert (tmp_path / 'journal/event_000000/runner_error.txt').is_file()
    truth = StaticTruth([[10., 10.], [22., 10.]], [.2, .2], [1., 2.], [0, 1])
    audit = audit_journal(tmp_path / 'journal', truth, (42, 7))
    assert audit['all_match']
    assert audit['physically_delivered_including_program_error'] == 1
    assert audit['delivered'] == 0 and audit['full_success'] == 0


def test_journal_tampering_fails_hash_audit(tmp_path):
    from udc.multigroup.observation import StaticTruth
    from udc.analysis.session_audit import audit_journal
    controller = session(tmp_path)
    controller.step(frame('one'))
    path = tmp_path / 'journal/event_000000/trajectory.npz'
    with path.open('ab') as stream:
        stream.write(b'not_the_original_trajectory')
    truth = StaticTruth([[10., 10.], [22., 10.]], [.2, .2], [1., 2.], [0, 1])
    with pytest.raises(ValueError, match='artifact changed'):
        audit_journal(tmp_path / 'journal', truth, (42, 7))


def test_identity_rejection_keeps_unavailable_world_explicit_in_audit(tmp_path):
    from udc.multigroup.observation import StaticTruth
    from udc.analysis.session_audit import audit_journal
    controller = session(tmp_path)
    controller.step(frame('one'))
    value = frame('two', controller.next_observation_time_s)
    record = controller.step(ObservationFrame(value.frame_id, value.time_s, (42, 99), value.measurement))
    assert record.metrics['status'] == 'STATIC_IDENTITY_CHANGED'
    truth = StaticTruth([[10., 10.], [22., 10.]], [.2, .2], [1., 2.], [0, 1])
    audit = audit_journal(tmp_path / 'journal', truth, (42, 7))
    assert audit['all_match'] and audit['delivered'] == 1
    assert audit['rows'][1]['evaluation']['motion'] is None
    assert audit['rows'][1]['original_service'] is None
