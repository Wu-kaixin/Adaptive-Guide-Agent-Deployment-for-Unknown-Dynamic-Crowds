import json
import hashlib
import numpy as np

import pytest

from udc.analysis.input_exposure import audit, check_content


def test_nested_existing_namespace_cannot_be_called_unexposed(tmp_path):
    folder = tmp_path / 'old'
    folder.mkdir()
    (folder / 'manifest.json').write_text(json.dumps({'design': {'namespace': 1234}}), encoding='utf-8')
    with pytest.raises(ValueError, match='already exposed'):
        audit(tmp_path, 1234, 3400, 20, tmp_path / 'audit.json')
    result = json.loads((tmp_path / 'audit.json').read_text(encoding='utf-8'))
    assert result['manifest_count'] == 1
    assert not result['namespace_available_in_indexed_local_manifests']


def test_reserved_input_range_is_rejected_before_inventory(tmp_path):
    with pytest.raises(ValueError, match='reserved'):
        audit(tmp_path, 1234, 4999, 3, tmp_path / 'audit.json')
    assert not (tmp_path / 'audit.json').exists()


def test_changing_guide_initialization_does_not_create_new_people_sample(tmp_path):
    old = tmp_path / 'old'
    old.mkdir()
    people = dict(positions=np.array([[1., 2.]]), radii=np.array([.2]), demand=np.array([1.]))
    np.savez_compressed(old / 'truth.npz', **people, guides=[[0., 0.]])
    (old / 'manifest.json').write_text(json.dumps({'namespace': 1, 'truth': 'truth.npz'}), encoding='utf-8')
    audit(tmp_path, 2, 3400, 20, tmp_path / 'inventory.json')
    new = tmp_path / 'new'
    new.mkdir()
    np.savez_compressed(new / 'truth.npz', **people, guides=[[10., 10.]])
    (new / 'manifest.json').write_text(json.dumps({'design': {'namespace': 2}, 'units': [
        {'unit_id': 'same_people', 'truth': 'truth.npz',
         'truth_sha256': hashlib.sha256((new / 'truth.npz').read_bytes()).hexdigest()}]}), encoding='utf-8')
    with pytest.raises(ValueError, match='overlap'):
        check_content(new, tmp_path / 'inventory.json', tmp_path / 'overlap.json')
    result = json.loads((tmp_path / 'overlap.json').read_text(encoding='utf-8'))
    assert result['old_people_overlaps'] == ['same_people']
