import json
import yaml

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
