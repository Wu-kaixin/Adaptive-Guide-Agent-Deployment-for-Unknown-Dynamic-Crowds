import json
import os
import subprocess
import sys
import threading

import numpy as np
import pytest
import yaml

from udc.experiments.static_multigroup import ROOT
from udc.interfaces import Observation
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.session import StaticDeploymentSession
from udc.multigroup.session_artifacts import JournalInUseError


def config():
    return yaml.safe_load((ROOT / 'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))['model']


def create(root):
    return StaticDeploymentSession([[2., 2.], [24., 2.]], [40., 30.], config(), root)


def frame(name, time, reverse=False):
    order = [1, 0] if reverse else [0, 1]
    obs = Observation(np.array([[10., 10.], [22., 10.]])[order],
                      np.array([.2, .2])[order], np.array([1., 2.])[order])
    return ObservationFrame(name, time, tuple(np.array([42, 7])[order]), BoundedMeasurement(obs, .04, 'bounded'))


def committed(root):
    controller = create(root)
    first = controller.step(frame('one', 0.))
    positions, time = controller.positions, controller.next_observation_time_s
    controller.close()
    return first, positions, time


def test_restored_feedback_and_next_decision_match_uninterrupted_session(tmp_path):
    with create(tmp_path / 'reference') as reference:
        reference.step(frame('one', 0.))
        expected = reference.step(frame('two', reference.next_observation_time_s, reverse=True))
    _, positions, time = committed(tmp_path / 'restart')
    with StaticDeploymentSession.restore(tmp_path / 'restart', positions, time) as restored:
        actual = restored.step(frame('two', time, reverse=True))
    for name in ('positions', 'velocities', 'assignment'):
        np.testing.assert_array_equal(getattr(actual.execution, name), getattr(expected.execution, name))
    np.testing.assert_array_equal(actual.targets, expected.targets)
    assert actual.metrics == expected.metrics
    assert (tmp_path / 'restart/event_000001/checkpoint.json').is_file()


def test_live_writer_prevents_restore_and_closed_object_cannot_move(tmp_path):
    controller = create(tmp_path / 'journal')
    first = controller.step(frame('one', 0.))
    with pytest.raises(JournalInUseError):
        StaticDeploymentSession.restore(tmp_path / 'journal', controller.positions, controller.next_observation_time_s)
    positions, time = controller.positions, controller.next_observation_time_s
    controller.close()
    with pytest.raises(RuntimeError, match='Closed session'):
        controller.step(frame('two', time))
    with StaticDeploymentSession.restore(tmp_path / 'journal', positions, time) as restored:
        np.testing.assert_array_equal(restored.positions, first.execution.positions[-1])


def test_feedback_mismatch_or_unrecorded_time_never_resets_physical_state(tmp_path):
    _, positions, time = committed(tmp_path / 'journal')
    with pytest.raises(ValueError, match='no reset'):
        StaticDeploymentSession.restore(tmp_path / 'journal', positions + .01, time)
    with pytest.raises(ValueError, match='committed simulation clock'):
        StaticDeploymentSession.restore(tmp_path / 'journal', positions, time + 1.)
    with StaticDeploymentSession.restore(tmp_path / 'journal', positions, time):
        assert len(list((tmp_path / 'journal').glob('event_*'))) == 1


def test_partial_event_is_preserved_and_never_overwritten_or_resumed(tmp_path):
    _, positions, time = committed(tmp_path / 'journal')
    partial = tmp_path / 'journal/event_000001'
    partial.mkdir()
    path = partial / 'checkpoint.json'
    path.write_bytes(b'partial motion state not committed')
    with pytest.raises(ValueError, match='Incomplete event'):
        StaticDeploymentSession.restore(tmp_path / 'journal', positions, time)
    assert path.read_bytes() == b'partial motion state not committed'


def test_checkpoint_hash_changes_cannot_be_silently_loaded(tmp_path):
    _, positions, time = committed(tmp_path / 'journal')
    path = tmp_path / 'journal/event_000000/checkpoint.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['consumed_frame_ids'] = []
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='artifact changed'):
        StaticDeploymentSession.restore(tmp_path / 'journal', positions, time)


def test_header_cannot_change_even_before_first_committed_event(tmp_path):
    controller = create(tmp_path / 'journal')
    positions = controller.positions
    controller.close()
    path = tmp_path / 'journal/session.json'
    header = json.loads(path.read_text(encoding='utf-8'))
    header['model']['guide_distance_m'] = .1
    path.write_text(json.dumps(header), encoding='utf-8')
    with pytest.raises(ValueError, match='header changed'):
        StaticDeploymentSession.restore(tmp_path / 'journal', positions, 0.)


def test_corrupt_head_prevents_new_motion_while_original_state_is_kept(tmp_path, monkeypatch):
    import udc.multigroup.session as implementation
    controller = create(tmp_path / 'journal')
    controller.step(frame('one', 0.))
    positions, time = controller.positions.copy(), controller.next_observation_time_s
    path = tmp_path / 'journal/event_000000/trajectory.npz'
    with path.open('ab') as stream:
        stream.write(b'changed original bytes')
    called = []
    def unexpected_motion(*args):
        called.append(True)
        raise AssertionError('must be rejected before execution')
    monkeypatch.setattr(implementation, 'execute_hybrid', unexpected_motion)
    with pytest.raises(ValueError, match='artifact changed before motion'):
        controller.step(frame('two', time))
    assert not called and controller.halted
    np.testing.assert_array_equal(controller.positions, positions)
    assert not (tmp_path / 'journal/event_000001').exists()
    controller.close()


def test_scientific_halt_survives_restart_without_automatic_retry(tmp_path):
    controller = create(tmp_path / 'journal')
    controller.step(frame('one', 0.))
    controller.step(frame('stale', 0.))
    positions, time = controller.positions, controller.next_observation_time_s
    controller.close()
    with StaticDeploymentSession.restore(tmp_path / 'journal', positions, time) as restored:
        assert restored.halted
        result = restored.step(frame('new', time))
        assert result.metrics['status'] == 'SESSION_HALTED'
        assert len(result.execution.velocities) == 0


def test_source_drift_is_not_relabelled_as_a_reproducible_restart(tmp_path, monkeypatch):
    _, positions, time = committed(tmp_path / 'journal')
    import udc.multigroup.session_recovery as recovery
    monkeypatch.setattr(recovery, 'implementation_binding', lambda: {'source': 'different'})
    with pytest.raises(ValueError, match='source or environment changed'):
        StaticDeploymentSession.restore(tmp_path / 'journal', positions, time)


def test_os_process_exit_releases_writer_without_deleting_old_events(tmp_path):
    root = tmp_path / 'crashed'
    code = '''import json,os,sys,numpy as np
from udc.interfaces import Observation
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.session import StaticDeploymentSession
controller=StaticDeploymentSession([[2.,2.],[24.,2.]],[40.,30.],json.loads(sys.argv[2]),sys.argv[1])
obs=Observation(np.array([[10.,10.],[22.,10.]]),np.array([.2,.2]),np.array([1.,2.]))
controller.step(ObservationFrame('one',0.,(42,7),BoundedMeasurement(obs,.04,'bounded')))
os._exit(17)
'''
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    child = subprocess.run([sys.executable, '-c', code, str(root), json.dumps(config())],
                           env=environment, capture_output=True, text=True, timeout=30)
    assert child.returncode == 17, child.stderr
    checkpoint = json.loads((root / 'event_000000/checkpoint.json').read_text(encoding='utf-8'))
    with StaticDeploymentSession.restore(root, checkpoint['positions'], checkpoint['time_s']) as restored:
        result = restored.step(frame('two', restored.next_observation_time_s))
        assert result.metrics['deployment_completed']
    assert (root / 'event_000000/receipt.json').is_file()


def test_death_inside_execution_is_not_inferred_as_no_motion_from_old_feedback(tmp_path):
    root = tmp_path / 'in_flight'
    code = '''import json,os,sys,numpy as np
import udc.multigroup.session as implementation
from udc.interfaces import Observation
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.observation import BoundedMeasurement
controller=implementation.StaticDeploymentSession([[2.,2.],[24.,2.]],[40.,30.],json.loads(sys.argv[2]),sys.argv[1])
implementation.execute_hybrid=lambda *args:os._exit(18)
obs=Observation(np.array([[10.,10.],[22.,10.]]),np.array([.2,.2]),np.array([1.,2.]))
controller.step(ObservationFrame('one',0.,(42,7),BoundedMeasurement(obs,.04,'bounded')))
'''
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    child = subprocess.run([sys.executable, '-c', code, str(root), json.dumps(config())],
                           env=environment, capture_output=True, text=True, timeout=30)
    assert child.returncode == 18, child.stderr
    assert (root / 'event_000000/intent.json').is_file()
    assert not (root / 'event_000000/receipt.json').exists()
    with pytest.raises(ValueError, match='Incomplete event'):
        StaticDeploymentSession.restore(root, [[2., 2.], [24., 2.]], 0.)


def test_concurrent_call_and_close_cannot_release_a_second_command(tmp_path, monkeypatch):
    import udc.multigroup.session as implementation
    controller = create(tmp_path / 'journal')
    entered, release = threading.Event(), threading.Event()
    original = implementation.execute_hybrid
    records = []
    def wait_before_motion(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(implementation, 'execute_hybrid', wait_before_motion)
    thread = threading.Thread(target=lambda: records.append(controller.step(frame('one', 0.))))
    thread.start()
    try:
        assert entered.wait(10)
        with pytest.raises(RuntimeError, match='Concurrent'):
            controller.step(frame('two', 0.))
        with pytest.raises(RuntimeError, match='in progress'):
            controller.close()
    finally:
        release.set()
        thread.join(10)
        controller.close()
    assert not thread.is_alive()
    assert len(records) == 1 and records[0].metrics['deployment_completed']
