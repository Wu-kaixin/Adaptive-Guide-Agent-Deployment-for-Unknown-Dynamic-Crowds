import hashlib
import json

import pytest

from udc.experiments.frozen_resume import pending_units, read_ledger, validate_amendment


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def test_resume_retains_full_unit_candidate_context():
    first = {'task_id': 'low', 'unit_id': 'u', 'budget': 5}
    high = {'task_id': 'high', 'unit_id': 'u', 'budget': 16}
    done = {'task_id': 'done', 'unit_id': 'v', 'budget': 16}
    assert pending_units({'tasks': [first, high, done]}, {'high', 'done'}) == [('u', [first, high])]
    assert pending_units({'tasks': [first, high]}, {'low', 'high'}) == []


def test_completed_artifact_corruption_is_not_retried(tmp_path):
    folder = tmp_path / 'results/t'
    save(folder / 'metrics.json', {'task_id': 't', 'status': 'PATH_NOT_FOUND'})
    expected = hashlib.sha256((folder / 'metrics.json').read_bytes()).hexdigest()
    save(folder / 'receipt.json', {'metrics.json': expected})
    manifest = {'tasks': [{'task_id': 't'}]}
    before = (folder / 'receipt.json').read_bytes()
    rows, preserved, incomplete = read_ledger(tmp_path, manifest)
    assert rows['t']['status'] == 'PATH_NOT_FOUND'
    assert preserved and not incomplete
    assert (folder / 'receipt.json').read_bytes() == before
    save(folder / 'metrics.json', {'task_id': 't', 'status': 'FULL_SUCCESS'})
    with pytest.raises(ValueError, match='hash mismatch'):
        read_ledger(tmp_path, manifest)


def test_incomplete_task_is_identified_without_overwriting(tmp_path):
    folder = tmp_path / 'results/t'
    save(folder / 'metrics.json', {'task_id': 't'})
    before = (folder / 'metrics.json').read_bytes()
    rows, preserved, incomplete = read_ledger(tmp_path, {'tasks': [{'task_id': 't'}]})
    assert not rows and not preserved and incomplete == ['t']
    assert (folder / 'metrics.json').read_bytes() == before


def test_runtime_amendment_must_bind_manifest_and_worker_count(tmp_path):
    save(tmp_path / 'manifest.json', {'tasks': []})
    amendment = {'manifest_sha256': hashlib.sha256((tmp_path / 'manifest.json').read_bytes()).hexdigest(),
                 'new_workers': 32, 'user_authorized_all_cores': True, 'numerical_threads_per_worker': 1}
    validate_amendment(tmp_path, amendment, 32)
    with pytest.raises(ValueError, match='Workers'):
        validate_amendment(tmp_path, amendment, 2)
    amendment['manifest_sha256'] = 'different'
    with pytest.raises(ValueError, match='different manifest'):
        validate_amendment(tmp_path, amendment, 32)


def test_receipt_cannot_point_outside_task(tmp_path):
    save(tmp_path / 'outside.json', {})
    folder = tmp_path / 'results/t'
    save(folder / 'metrics.json', {'task_id': 't'})
    save(folder / 'receipt.json', {'../../outside.json': 'irrelevant'})
    with pytest.raises(ValueError, match='hash mismatch'):
        read_ledger(tmp_path, {'tasks': [{'task_id': 't'}]})
