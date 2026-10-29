import json
import yaml
import numpy as np
import pytest

from udc.experiments.sequential_deployment import prepare
from udc.experiments.static_multigroup import ROOT


def test_parallel_pattern_jobs_do_not_inflate_independent_physical_blocks(tmp_path):
    design=yaml.safe_load((ROOT/'configs/step1/sequential_smoke_v3.yaml').read_text(encoding='utf-8'))
    design.update(namespace=90171,seeds={'start':4103,'count':3},split_pattern_workers=True,
        patterns=['independent_jitter','slow_common_bias','oscillatory_shape_bias'])
    protocol=tmp_path/'protocol.yaml';protocol.write_text(yaml.safe_dump(design),encoding='utf-8')
    result=prepare(protocol,tmp_path/'study')
    manifest=json.loads((tmp_path/'study/manifest.json').read_text(encoding='utf-8'))
    assert result['physical_blocks']==3 and len(manifest['work_items'])==9
    assert len({u['physical_id'] for u in manifest['work_items']})==3
    assert result['step_records']==1359


def test_long_horizon_keeps_full_denominator_and_physical_blocks(tmp_path):
    design=yaml.safe_load((ROOT/'configs/step1/foundation_sequence_long_development_v1.yaml').read_text(encoding='utf-8'))
    design.update(namespace=90172,seeds={'start':4105,'count':3},group_counts=[1],shapes=['concave'])
    protocol=tmp_path/'protocol.yaml';protocol.write_text(yaml.safe_dump(design),encoding='utf-8')
    result=prepare(protocol,tmp_path/'study')
    manifest=json.loads((tmp_path/'study/manifest.json').read_text(encoding='utf-8'))
    assert result['physical_blocks']==3 and len(manifest['work_items'])==9
    assert result['step_records']==16227  # 9 pattern jobs * (3 nominal + 9 * 200).
    assert all(t['steps']==200 for t in manifest['tasks'] if t['kind']=='episode')
    for unit in manifest['units']:
        for series in unit['series'].values():
            with np.load(tmp_path/'study'/series['path'],allow_pickle=False) as data:
                assert len(data['positions'])==201


def test_long_horizon_cannot_silently_change_frozen_confirmation(tmp_path):
    design=yaml.safe_load((ROOT/'configs/step1/foundation_sequence_confirmation_v1.yaml').read_text(encoding='utf-8'))
    design['steps']=200
    protocol=tmp_path/'protocol.yaml';protocol.write_text(yaml.safe_dump(design),encoding='utf-8')
    with pytest.raises(ValueError,match='Frozen protocol horizon'):
        prepare(protocol,tmp_path/'study')
    assert not (tmp_path/'study').exists()
