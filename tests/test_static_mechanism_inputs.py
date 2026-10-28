from pathlib import Path

import numpy as np
import yaml

from udc.experiments.static_mechanism import prepare


def test_hotspot_changes_spatial_demand_without_moving_truth_or_mean_weight(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = yaml.safe_load((root / 'configs/step1/static_mechanism_v2.yaml').read_text(encoding='utf-8'))
    spec['group_counts'] = [2]
    spec['seeds']['count'] = 1
    spec['size_patterns'] = {'equal': [1., 1., 1., 1., 1.]}
    protocol = tmp_path / 'protocol.yaml'
    protocol.write_text(yaml.safe_dump(spec), encoding='utf-8')
    study = tmp_path / 'study'
    manifest = prepare(protocol, study)
    arrays = []
    errors = []
    for unit in manifest['units']:
        with np.load(study / unit['truth'], allow_pickle=False) as truth:
            arrays.append({k: truth[k].copy() for k in truth.files})
        with np.load(study / unit['observation'], allow_pickle=False) as observation:
            errors.append(observation['positions'] - arrays[-1]['positions'])
    np.testing.assert_array_equal(arrays[0]['positions'], arrays[1]['positions'])
    np.testing.assert_array_equal(errors[0], errors[1])
    assert not np.array_equal(arrays[0]['demand'], arrays[1]['demand'])
    for array in arrays:
        for k in [0, 1]:
            assert np.isclose(array['demand'][array['group_ids'] == k].mean(), 1.)
