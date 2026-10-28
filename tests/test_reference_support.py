import numpy as np
import pytest
import yaml
from shapely.geometry import LineString

from udc.interfaces import Observation
from udc.multigroup.geometry import construct_groups
from udc.multigroup.observation import BoundedMeasurement, measure
from udc.multigroup.scenarios import make_truth
from udc.multigroup.reference_support import build_support, certificate
from udc.multigroup.evaluation import reference_groups


def config():
    from udc.experiments.static_multigroup import ROOT
    return yaml.safe_load((ROOT/'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))['model']


@pytest.mark.parametrize('shape',['circle','ellipse','concave','irregular'])
@pytest.mark.parametrize('closing_mode',['positive_dilation_only','round_buffer_sagitta'])
def test_original_true_reference_is_enclosed_under_independent_errors(shape, closing_mode):
    truth=make_truth(1,shape,81,93850)
    config_value=config()
    config_value['support_closing_enclosure']=closing_mode
    actual=reference_groups(truth,[40,30],config_value)
    for seed in range(6):
        obs=measure(truth,.12,seed)
        groups,_=construct_groups(obs,[40,30],config_value)
        support=build_support(obs,groups,config_value)
        for true_group,region in zip(actual,support.regions):
            curve=LineString(np.vstack([true_group.boundary.deployment,true_group.boundary.deployment[0]]))
            assert curve.difference(region).is_empty


def test_nontrivial_retention_can_certify_original_reference_without_common_translation():
    config_value=config()
    observation=Observation(np.array([[10.,10.]]),np.array([.2]),np.array([1.]))
    obs=BoundedMeasurement(observation,.12,'independent_bounded')
    groups,_=construct_groups(obs,[40,30],config_value)
    support=build_support(obs,groups,config_value)
    angles=np.arange(16)*2*np.pi/16
    greedy=np.array([10.,10.])+1.6*np.c_[np.cos(angles),np.sin(angles)]
    retained=greedy+np.array([.01,-.01])
    cert=certificate(support,retained,greedy,1.)
    assert cert['accept_retained'] and cert['status']=='ORIGINAL_REFERENCE_SUPPORT_DOMINANCE'
    assert cert['groups'][0]['retained_interval']==[0.,0.]


def test_general_bound_refuses_a_potentially_lost_service_region():
    config_value=config()
    obs=BoundedMeasurement(Observation(np.array([[10.,10.]]),np.array([.2]),np.array([1.])),.12,'independent_bounded')
    groups,_=construct_groups(obs,[40,30],config_value)
    support=build_support(obs,groups,config_value)
    cert=certificate(support,[[20,20]],[[11.6,10]],1.)
    assert not cert['accept_retained'] and cert['groups'][0]['upper_loss']==1


def test_conditional_identity_is_not_a_hidden_default_gate():
    from shapely.geometry import box
    from udc.multigroup.reference_support import ReferenceSupport
    support=ReferenceSupport((box(-.1,-.1,.1,.1),),((0,),),
        {'certified':False,'identity_given_original_reference_defined':True},.12,{'allow_conditional_identity':False})
    cert=certificate(support,[[0,0]],[[0,0]],1.)
    assert cert['status']=='UNCERTIFIED' and not cert['accept_retained']
