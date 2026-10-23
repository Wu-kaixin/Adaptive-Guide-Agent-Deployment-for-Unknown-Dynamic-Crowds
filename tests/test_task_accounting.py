"""Program-error accounting: one directory, one ledger row."""
import json
from pathlib import Path

import numpy as np
import pytest

from udc.analysis.summary import summarize_results
from udc.experiments.accounting import ledger_row
from udc.experiments.runner import RESULT_FILES, _resume_initialization_failure, _resume_result, reevaluate_result
from udc.experiments.validation import _run_task, reevaluate_tree, summarize

ROOT = Path(__file__).resolve().parents[1]
KNOWN_AUDIT = ROOT / 'runs' / 'summary-error-audit-94tjgmgt'


def _task(directory, resume=False):
    return {
        'scenario': 'square', 'config': 'unused.yaml', 'seed': 0,
        'method': 'equal_arc', 'protocol': 'fixed_n', 'fixed_n': 4,
        'expected_statuses': None, 'output': str(directory),
    }


def _metrics(success, status, time=5.0):
    return {
        'scene': 'square', 'method': 'equal_arc', 'protocol': 'fixed_n',
        'success': success, 'termination_status': status, 'simulation_time': time,
        'active_guides': 4, 'planned_max_gap': 1.2, 'actual_max_gap': 1.4,
        'max_gap_limit': 2.0, 'max_tracking_error': 0.01,
        'minimum_net_clearances': {'guides': 0.2, 'crowd': 0.1, 'walls': 0.4},
        'wall_time': 0.5, 'task_sha256': 'hash-1', 'task_request_sha256': 'request-1',
        'failure_stage': 'execution', 'schema_version': '1.2.0',
    }


def _write_metrics(directory, payload, state_status='completed'):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'metrics.json').write_text(json.dumps(payload), encoding='utf-8')
    (directory / 'state.json').write_text(json.dumps({
        'status': state_status, 'success': payload['success'], 'task_sha256': 'hash-1',
    }), encoding='utf-8')
    return payload


def test_error_before_metrics_stays_in_the_denominator(tmp_path, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError('before any metrics')

    monkeypatch.setattr('udc.experiments.validation.experiment', explode)
    row = _run_task(_task(tmp_path / 'early'), resume=False)
    assert row['termination_status'] == 'EXPERIMENT_ERROR'
    assert row['success'] is False and row['completed'] is False
    assert row['physical_success'] is None
    assert not (tmp_path / 'early' / 'metrics.json').exists()
    assert 'before any metrics' in (tmp_path / 'early' / 'runner_error.txt').read_text(encoding='utf-8')
    report = summarize_results(tmp_path)
    assert report['tasks'] == 1 and report['successful'] == 0
    group = report['groups']['square|equal_arc|fixed_n']
    assert group['statuses'] == {'EXPERIMENT_ERROR': 1}
    assert group['successful_time_count'] == 0
    assert group['success_rate'] == 0.0


def test_success_saved_then_exception_is_one_program_error(tmp_path, monkeypatch):
    directory = tmp_path / 'late'
    saved = {}

    def save_then_fail(*_args, **_kwargs):
        payload = _metrics(True, 'SUCCESS', 12.5)
        _write_metrics(directory, payload, state_status='completed')
        saved['metrics'] = (directory / 'metrics.json').read_bytes()
        saved['state'] = (directory / 'state.json').read_bytes()
        raise RuntimeError('after metrics were saved')

    monkeypatch.setattr('udc.experiments.validation.experiment', save_then_fail)
    row = _run_task(_task(directory), resume=False)
    assert row['termination_status'] == 'EXPERIMENT_ERROR'
    assert row['success'] is False and row['completed'] is False
    assert row['physical_success'] is True
    assert row['physical_termination_status'] == 'SUCCESS'
    assert row['physical_simulation_time'] == 12.5
    assert row['simulation_time'] is None
    assert (directory / 'metrics.json').read_bytes() == saved['metrics']
    assert (directory / 'state.json').read_bytes() == saved['state']
    assert json.loads((directory / 'state.json').read_text(encoding='utf-8'))['status'] == 'completed'
    scene = (directory / 'runner_error.txt').read_bytes()
    assert b'after metrics were saved' in scene

    def second_failure(*_args, **_kwargs):
        raise RuntimeError('later observation')

    monkeypatch.setattr('udc.experiments.validation.experiment', second_failure)
    again = _run_task(_task(directory), resume=True)
    assert again['termination_status'] == 'EXPERIMENT_ERROR' and again['success'] is False
    assert (directory / 'runner_error.txt').read_bytes() == scene
    assert b'later observation' in (directory / 'later_process_error.txt').read_bytes()
    assert (directory / 'metrics.json').read_bytes() == saved['metrics']
    assert (directory / 'state.json').read_bytes() == saved['state']

    report = summarize_results(tmp_path)
    assert report['tasks'] == 1 and report['successful'] == 0
    assert report['physical_success_excluded'] == 1
    group = report['groups']['square|equal_arc|fixed_n']
    assert group['statuses'] == {'EXPERIMENT_ERROR': 1}
    assert group['successful_time_count'] == 0
    batch_dir = tmp_path / 'batch'
    batch_dir.mkdir()
    batch = summarize([row], batch_dir, 'hash', 'development', 1)
    assert batch['tasks'] == 1 and batch['successful'] == 0
    assert batch['experiment_errors'] == 1
    assert batch['termination_counts'] == {'EXPERIMENT_ERROR': 1}


def test_clean_success_and_scientific_failure_keep_separate_times(tmp_path, monkeypatch):
    calls = {'n': 0}

    def run(*_args, **_kwargs):
        calls['n'] += 1
        success = calls['n'] == 1
        status = 'SUCCESS' if success else 'TIMEOUT'
        directory = Path(_args[1])
        _write_metrics(directory, _metrics(success, status, 8.0 if success else 40.0))
        return None

    monkeypatch.setattr('udc.experiments.validation.experiment', run)
    success_row = _run_task(_task(tmp_path / 'ok'), resume=False)
    failure_row = _run_task(_task(tmp_path / 'late'), resume=False)
    assert success_row['success'] is True and success_row['termination_status'] == 'SUCCESS'
    assert success_row['simulation_time'] == 8.0 and success_row['expectation_met'] is True
    assert failure_row['success'] is False and failure_row['termination_status'] == 'TIMEOUT'
    assert failure_row['completed'] is True and failure_row['simulation_time'] == 40.0
    report = summarize_results(tmp_path)
    assert report['tasks'] == 2 and report['successful'] == 1
    group = report['groups']['square|equal_arc|fixed_n']
    assert group['statuses'] == {'SUCCESS': 1, 'TIMEOUT': 1}
    assert group['successful_time_count'] == 1
    assert group['successful_time_mean'] == 8.0
    assert group['success_rate'] == 0.5


def test_existing_output_refusal_does_not_create_an_error_scene(tmp_path, monkeypatch):
    directory = tmp_path / 'kept'
    payload = _metrics(True, 'SUCCESS', 3.0)
    _write_metrics(directory, payload)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    def refuse(*_args, **_kwargs):
        raise FileExistsError('Use an empty output directory or --resume: kept')

    monkeypatch.setattr('udc.experiments.validation.experiment', refuse)
    row = _run_task(_task(directory), resume=False)
    assert row['output_refusal'] is True and row['expectation_met'] is False
    assert row['success'] is True and row['termination_status'] == 'SUCCESS'
    assert not (directory / 'runner_error.txt').exists()
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    report = summarize_results(tmp_path)
    assert report['successful'] == 1 and report['process_errors'] == 0


def test_resume_does_not_treat_an_unabsorbed_scene_as_success(tmp_path):
    directory = tmp_path / 'resume'
    directory.mkdir()
    payload = _metrics(True, 'SUCCESS', 6.0)
    (directory / 'metrics.json').write_text(json.dumps(payload), encoding='utf-8')
    (directory / 'state.json').write_text(json.dumps({
        'status': 'completed', 'task_sha256': 'hash-1',
    }), encoding='utf-8')
    (directory / 'config.yaml').write_text('a: 1\n', encoding='utf-8')
    (directory / 'planning.json').write_text('[]\n', encoding='utf-8')
    np.savez_compressed(directory / 'trajectory.npz', positions=np.zeros((1, 1, 2)))
    assert set(path.name for path in directory.iterdir()) >= RESULT_FILES
    assert _resume_result(directory, 'hash-1')['success'] is True
    initial = tmp_path / 'initial'
    initial.mkdir()
    for name in ('config.yaml', 'planning.json', 'trajectory.npz'):
        if name.endswith('.npz'):
            np.savez_compressed(initial / name, positions=np.zeros((1, 1, 2)))
        else:
            (initial / name).write_text((directory / name).read_text(encoding='utf-8'), encoding='utf-8')
    initial_metrics = _metrics(False, 'INITIALIZATION_INVALID', 0.0)
    initial_metrics['failure_stage'] = 'initialization'
    (initial / 'metrics.json').write_text(json.dumps(initial_metrics), encoding='utf-8')
    (initial / 'state.json').write_text((directory / 'state.json').read_text(encoding='utf-8'), encoding='utf-8')
    assert _resume_initialization_failure(initial, 'request-1')['termination_status'] == 'INITIALIZATION_INVALID'
    (initial / 'runner_error.txt').write_text('post-save\n', encoding='utf-8')
    assert _resume_initialization_failure(initial, 'request-1') is None
    (directory / 'runner_error.txt').write_text('post-save\n', encoding='utf-8')
    assert _resume_result(directory, 'hash-1') is None
    assert json.loads((directory / 'metrics.json').read_text(encoding='utf-8'))['success'] is True


def test_old_error_marker_is_one_row_and_keeps_its_success_flag(tmp_path):
    absorbed = tmp_path / 'absorbed'
    _write_metrics(absorbed, _metrics(False, 'EXPERIMENT_ERROR', 9.0), state_status='completed')
    (absorbed / 'runner_error.txt').write_text('rewritten metrics\n', encoding='utf-8')
    flagged = tmp_path / 'flagged'
    _write_metrics(flagged, _metrics(True, 'EXPERIMENT_ERROR', 4.0))
    (flagged / 'runner_error.txt').write_text('status already error\n', encoding='utf-8')
    report = summarize_results(tmp_path)
    assert report['tasks'] == 2
    group = report['groups']['square|equal_arc|fixed_n']
    assert group['statuses'] == {'EXPERIMENT_ERROR': 2}
    assert group['successful'] == 1
    assert group['successful_time_count'] == 1
    assert group['successful_time_mean'] == 4.0
    assert ledger_row(absorbed)['absorbed_program_error'] is True
    assert ledger_row(absorbed)['unabsorbed_program_error'] is False


def test_orchestration_sidecar_does_not_override_runner_metrics(tmp_path):
    directory = tmp_path / 'side'
    _write_metrics(directory, _metrics(True, 'SUCCESS', 2.0))
    (directory / 'orchestration_error.json').write_text('{"status":"EXPERIMENT_ERROR"}\n', encoding='utf-8')
    report = summarize_results(tmp_path)
    assert report['successful'] == 1
    assert report['groups']['square|equal_arc|fixed_n']['statuses'] == {'SUCCESS': 1}


def test_reevaluation_keeps_physical_result_and_program_error(tmp_path):
    directory = tmp_path / 'physical'
    _write_metrics(directory, _metrics(True, 'SUCCESS', 7.0))
    (directory / 'runner_error.txt').write_text('after save\n', encoding='utf-8')
    np.savez_compressed(directory / 'trajectory.npz', boundary_deployment=np.zeros((0, 2)))
    metrics_bytes = (directory / 'metrics.json').read_bytes()
    scene_bytes = (directory / 'runner_error.txt').read_bytes()
    offline = reevaluate_result(directory)
    assert offline['success'] is False
    assert (directory / 'metrics.json').read_bytes() == metrics_bytes
    assert (directory / 'runner_error.txt').read_bytes() == scene_bytes
    report = reevaluate_tree(tmp_path)
    assert report['runs'] == 1 and report['consistent'] == 0
    result = report['results'][0]
    assert result['ledger_success'] is False
    assert result['ledger_status'] == 'EXPERIMENT_ERROR'
    assert result['process_error'] is True
    assert result['unabsorbed_program_error'] is True
    assert result['original_success'] is True
    assert (directory / 'runner_error.txt').read_bytes() == scene_bytes


@pytest.mark.skipif(not (KNOWN_AUDIT / 'audit_result.json').is_file(),
                    reason='local synthetic audit artifact is not in this checkout')
def test_known_synthetic_audit_directory_is_not_rewritten():
    files = [path for path in KNOWN_AUDIT.rglob('*') if path.is_file()]
    before = {path: path.read_bytes() for path in files}
    report = summarize_results(KNOWN_AUDIT)
    assert {path: path.read_bytes() for path in files} == before
    assert report['tasks'] == 1 and report['successful'] == 0
    assert report['physical_success_excluded'] == 1
    group = report['groups']['synthetic_scene|equal_arc|fixed_n']
    assert group['statuses'] == {'EXPERIMENT_ERROR': 1}
    assert group['successful_time_count'] == 0
