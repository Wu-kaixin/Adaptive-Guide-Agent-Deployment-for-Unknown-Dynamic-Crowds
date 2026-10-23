"""Counterexamples for saved-config binding, preflight integrity and crash prefixes."""
import json
import shutil

import numpy as np
import pytest

from udc.execution import execute_preflight, reevaluate_execution
from udc.execution.fixed_paths import EXECUTION_POLICY as RUNTIME_POLICY
from udc.preflight.pipeline import _artifact_hashes, compact_execution_input, run_preflight
from udc.preflight.review import reevaluate_preflight


@pytest.fixture(scope='module')
def accepted(tmp_path_factory):
    preflight = tmp_path_factory.mktemp('preflight')
    execution = tmp_path_factory.mktemp('execution')
    assert run_preflight(compact_execution_input(), preflight)['status'] == 'PREFLIGHT_ACCEPTED'
    record = execute_preflight(preflight, execution)
    assert record['status'] == 'SUCCESS'
    return preflight, execution


def test_review_uses_the_saved_config_when_the_module_constant_changes(accepted, monkeypatch):
    _preflight, execution = accepted
    monkeypatch.setitem(RUNTIME_POLICY, 'position_tolerance_m', 1e-12)
    review = reevaluate_execution(execution)
    assert review['consistent'] is True
    assert review['used_live_defaults'] is False
    saved = json.loads((execution / 'effective_config.json').read_text(encoding='utf-8'))
    assert saved['execution']['position_tolerance_m'] == 0.03
    assert saved['provenance']['source_identity'].startswith('working tree')
    assert saved['provenance']['git_head'] != saved['provenance']['working_tree_source_sha256']


def test_yaml_mismatch_is_refused_before_motion(accepted, tmp_path, monkeypatch):
    monkeypatch.setitem(RUNTIME_POLICY, 'deadline_s', 60.0)
    refused = execute_preflight(accepted[0], tmp_path / 'mismatch')
    assert refused['status'] == 'CONFIG_MISMATCH'
    assert refused['motion_executed'] is False
    assert refused['steps'] == 0
    assert reevaluate_execution(tmp_path / 'mismatch')['consistent'] is True


def test_missing_required_preflight_file_is_not_treated_as_accepted(accepted, tmp_path):
    copied = tmp_path / 'dropped'
    shutil.copytree(accepted[0], copied)
    (copied / 'paths.json').unlink()
    review = reevaluate_preflight(copied)
    assert review['consistent'] is False
    assert review['checks']['required_files'] is False


def test_same_guides_with_wrong_group_or_floor_fail_review(accepted, tmp_path):
    copied = tmp_path / 'groups'
    shutil.copytree(accepted[0], copied)
    paths = json.loads((copied / 'paths.json').read_text(encoding='utf-8'))
    paths['pairs'][0]['group_id'] = 'b' if paths['pairs'][0]['group_id'] == 'a' else 'a'
    (copied / 'paths.json').write_text(json.dumps(paths), encoding='utf-8')
    _refresh_hash(copied)
    review = reevaluate_preflight(copied)
    assert review['consistent'] is False
    assert review['checks']['allocation'] is False
    floor_dir = tmp_path / 'floor'
    shutil.copytree(accepted[0], floor_dir)
    floor_path = floor_dir / 'groups' / 'a' / 'floor.json'
    floor = json.loads(floor_path.read_text(encoding='utf-8'))
    floor['minimum_guides'] = floor['minimum_guides'] + 100
    floor_path.write_text(json.dumps(floor), encoding='utf-8')
    _refresh_hash(floor_dir)
    assert reevaluate_preflight(floor_dir)['checks']['floors'] is False


def test_path_length_mismatch_is_not_zipped_away(accepted, tmp_path):
    copied = tmp_path / 'short'
    shutil.copytree(accepted[0], copied)
    payload = json.loads((copied / 'paths.json').read_text(encoding='utf-8'))
    payload['paths'] = payload['paths'][:-1]
    (copied / 'paths.json').write_text(json.dumps(payload), encoding='utf-8')
    _refresh_hash(copied)
    review = reevaluate_preflight(copied)
    assert review['consistent'] is False
    assert review['checks']['paths'] is False


def test_trajectory_identity_time_and_absolute_displacement(accepted, tmp_path):
    execution = tmp_path / 'moved'
    shutil.copytree(accepted[1], execution)
    data = np.load(execution / 'trajectory.npz', allow_pickle=False)
    positions = np.array(data['positions'], copy=True)
    positions[1:] += 1e-5
    guide_ids = np.array(data['guide_ids'], copy=True)
    guide_ids[0] = int(guide_ids[0]) + 1000
    np.savez_compressed(
        execution / 'trajectory.npz', time=data['time'], positions=positions,
        nominal_velocity=data['nominal_velocity'], safe_velocity=data['safe_velocity'],
        guide_ids=guide_ids, person_ids=data['person_ids'],
        person_positions=data['person_positions'], targets=data['targets'],
    )
    review = reevaluate_execution(execution)
    assert review['consistent'] is False
    assert review['checks']['displacement'] is False
    assert review['checks']['trajectory_identity'] is False
    original = reevaluate_execution(accepted[1])
    assert original['consistent'] is True
    assert original['checks']['displacement'] is True


def test_arrival_after_the_saved_deadline_is_not_protocol_success(accepted, tmp_path):
    execution = tmp_path / 'late'
    shutil.copytree(accepted[1], execution)
    config = json.loads((execution / 'effective_config.json').read_text(encoding='utf-8'))
    config['execution']['deadline_s'] = 0.2
    (execution / 'effective_config.json').write_text(json.dumps(config), encoding='utf-8')
    review = reevaluate_execution(execution)
    assert review['consistent'] is False
    assert review['checks']['within_deadline'] is False
    assert review['physical_acceptance'] is not True or review['checks']['within_deadline'] is False


def test_exception_after_steps_keeps_the_executed_prefix(accepted, tmp_path, monkeypatch):
    calls = {'n': 0}

    def explode(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] > 3:
            raise RuntimeError('injected after three committed steps')
        return original(*args, **kwargs)

    import udc.controller.abcg as controller
    original = controller.safe_velocity
    monkeypatch.setattr(controller, 'safe_velocity', explode)
    failed = execute_preflight(accepted[0], tmp_path / 'prefix')
    assert failed['status'] == 'EXPERIMENT_ERROR'
    assert failed['orchestration_status'] == 'EXPERIMENT_ERROR'
    assert failed['deployment_success'] is False
    assert failed['steps'] == 3
    assert failed['motion_executed'] is True
    assert failed['executed_path_length_m'] > 0
    data = np.load(tmp_path / 'prefix' / 'trajectory.npz', allow_pickle=False)
    assert len(data['positions']) == 4
    assert len(data['safe_velocity']) == 3
    assert len(data['time']) == 4
    review = reevaluate_execution(tmp_path / 'prefix')
    assert review['process_status'] == 'EXPERIMENT_ERROR'
    assert review['physical_acceptance'] is False
    assert review['checks']['displacement'] is True
    assert (tmp_path / 'prefix' / 'execution_error.txt').is_file()


def test_zero_step_rejection_is_reviewed_from_evidence(tmp_path):
    from udc.preflight.pipeline import FrozenInput
    base = compact_execution_input()
    positions = np.array(base.guide_positions, copy=True)
    positions[0] = (12.0, 3.0)
    positions[1] = (12.2, 3.0)
    request = FrozenInput(
        base.room, base.person_ids, base.positions, base.radii, base.demand, base.groups,
        base.guide_ids, positions,
    )
    preflight = tmp_path / 'preflight'
    assert run_preflight(request, preflight)['status'] == 'PREFLIGHT_ACCEPTED'
    record = execute_preflight(preflight, tmp_path / 'illegal')
    assert record['status'] == 'ILLEGAL_INITIAL_STATE'
    assert record['steps'] == 0
    assert record['executed_path_length_m'] is None
    review = reevaluate_execution(tmp_path / 'illegal')
    assert review['consistent'] is True
    assert review['physical_acceptance'] is None
    assert review['checks']['illegal_initial'] is True


def _refresh_hash(directory):
    record = json.loads((directory / 'preflight.json').read_text(encoding='utf-8'))
    record['artifact_sha256'] = _artifact_hashes(directory)
    (directory / 'preflight.json').write_text(json.dumps(record), encoding='utf-8')
