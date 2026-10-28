from dataclasses import fields
import numpy as np
import pytest
import yaml

from udc.experiments.static_multigroup import ROOT
from udc.multigroup.observation import measure
from udc.multigroup.scenarios import make_truth
from udc.multigroup.geometry import construct_groups
from udc.multigroup.phase_retention import phase_pool,GREEDY


@pytest.mark.parametrize('count',[0,5])
def test_cached_base_pool_keeps_every_option_order_and_value_bitwise(count):
    config=yaml.safe_load((ROOT/'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))['model']
    truth=make_truth(1,'ellipse',92,99022);obs=measure(truth,.12,18)
    groups,domain=construct_groups(obs,[40,30],config);group=groups[0]
    base=phase_pool(group,obs,domain,count,config)
    anchors={m:base[0].positions.copy() for m in GREEDY}
    complete=phase_pool(group,obs,domain,count,config,anchors)
    cached=phase_pool(group,obs,domain,count,config,anchors,base_options=base)
    assert len(complete)==len(cached)
    for a,b in zip(complete,cached):
        for field in fields(a):
            x,y=getattr(a,field.name),getattr(b,field.name)
            assert np.array_equal(x,y) if isinstance(x,np.ndarray) else x==y
