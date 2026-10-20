"""Raw Gaussian demand, explicit phase scoring and immutable paired pilot inputs."""
import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon
import yaml

from udc.boundary import estimate_disk_union
from udc.deployment.admissible import admissible_phase
from udc.deployment.planners import (coverage_statistics, demand_profile, periodic_cells,
    plan_coverage, quadrature_mass)
from udc.environment import Environment
from udc.experiments import demand_pilot as pilot
from udc.experiments.nonconvex_rebuild import draw_cell, load_protocol, rejected_result
from udc.experiments.runner import reevaluate_result
from udc.navigation import navigation_domain


@pytest.fixture(scope='module')
def seed51():
    generator,base = load_protocol('configs/step1/nonconvex_execution_confirmation_v3.yaml')
    scene = next(s for s in base['scenes'] if s['name']=='elongated')
    data,_ = draw_cell(generator,base,scene,51)
    b = estimate_disk_union(data['people'],1.3,.1,data['room'],.55,.75,data['radii'],
                            closing_radius=.3,quad_segs=16)
    plan = plan_coverage(b,None,None,11,None,'equal_arc',density_override=np.ones(len(b.arc)))
    domain = navigation_domain(data['room'],.55,Polygon(b.crowd),.55)
    return data,b,plan,domain


def test_constant_phase_behavior_preserved(seed51):
    _,b,plan,domain = seed51
    candidate,report = admissible_phase(b,plan,domain,np.ones(len(b.arc)),phase_bins=256,
        gap_limit=1.94,separation=.75,quadrature_samples=2048)
    assert candidate is not None
    assert report['selected_phase_m'] == pytest.approx(-b.length/256)
    assert report['cost_before'] == pytest.approx(report['cost_after'],abs=1e-12)
    assert len(report['attempts'])==256


def test_nonuniform_phase_ranked_on_raw_density_not_constant_or_transformed(seed51):
    data,b,plan,domain = seed51
    demand = np.where(data['people'][:,0]<13.2,5.,1.)
    rho = demand_profile(b,data['people'],demand,1.2)
    _,report = admissible_phase(b,plan,domain,rho,phase_bins=256,gap_limit=1.94,
                               separation=.75,quadrature_samples=2048)
    q,mass,length = quadrature_mass(b,rho,2048)
    feasible=[]
    for a in report['attempts']:
        sites = (plan.arc_positions+a['phase_m']) % length
        # Direct periodic distance formula independently checks saved candidate cost.
        delta = (q[:,None]-sites[None,:]+length/2)%length-length/2
        expected = float((mass*np.min(delta**2,axis=1)).sum()/mass.sum())
        assert a['quadrature_cost']==pytest.approx(expected,abs=1e-12)
        if a['accepted']:
            feasible.append((expected,abs(a['phase_m']),a['phase_m']))
    assert np.ptp([a['quadrature_cost'] for a in report['attempts']])>1e-3
    assert report['selected_phase_m']==min(feasible)[2]
    assert report['cost_after']<=report['cost_before']
    assert report['selected_phase_m'] != pytest.approx(-length/256)
    assert report['constant_density_exact_cost'] is None


def test_kernel_scale_normalization_and_periodic_seam(seed51):
    data,b,plan,_ = seed51
    weights=np.where(data['people'][:,0]<13.2,5.,1.)
    rho=demand_profile(b,data['people'],weights,1.2)
    squared=np.sum((b.deployment[:,None]-data['people'][None,:])**2,axis=2)
    direct=np.exp(-squared/(2*1.2**2))@weights
    np.testing.assert_allclose(rho,direct/direct.max(),rtol=1e-13)
    np.testing.assert_allclose(demand_profile(b,data['people'],weights*1e80,1.2),rho,rtol=1e-12)
    q,mass,length=quadrature_mass(b,rho,2048)
    assert q[0]>0 and q[-1]<length
    # Last quadrature node interpolates to rho[0], not to rho[-1] as a clamp.
    expected=np.interp(q[-1],[b.arc[-1],length],[rho[-1],rho[0]])
    assert mass[-1]*2048/length==pytest.approx(expected)
    a=coverage_statistics(q,mass,plan.arc_positions,length,np.inf)
    c=coverage_statistics(q,mass*1e80,plan.arc_positions+length,length,np.inf)
    assert a['weighted_cost']==pytest.approx(c['weighted_cost'],rel=1e-13)
    assert np.ptp(demand_profile(b,data['people'],np.ones(len(weights)),1.2))>.1


def test_freeze_36_pairs_windows_before_targets_and_detect_mutation(tmp_path):
    root=tmp_path/'pilot'
    pilot.freeze(root)
    lock=pilot.locked(root)
    rows=pilot.read(root/'manifest.json')['tasks']
    assert len(rows)==36 and len({r['physical_key'] for r in rows})==6
    assert len({r['condition_key'] for r in rows})==12
    assert not (root/'plans').exists() and not (root/'results').exists()
    for condition,record in lock['conditions'].items():
        group=[r for r in rows if r['condition_key']==condition]
        assert len(group)==3 and len({r['input_sha256'] for r in group})==1
        source=lock['sources'][record['physical_key']]
        data=pilot.arrays(Path(source['source_cell'])/'input.npz')
        field=pilot.arrays(root/'conditions'/condition/'demand.npz')
        cfg=yaml.safe_load((root/'conditions'/condition/'input.yaml').read_text())
        observed=Environment(cfg).observe()
        np.testing.assert_array_equal(observed.demand,field['demand'])
        np.testing.assert_array_equal(observed.positions,data['people'])
        assert record['density']['minimum_rho']<1
        if record['demand']=='ones':
            assert not field['excess_window'].any() and not record['density']['excess_applicable']
        else:
            assert field['excess_window'].any()
    rebuilt=lock['sources']['tight_u__seed_59']
    assert rebuilt['origin']=='protocol_reconstruction_not_original_recovery'
    assert rebuilt['historical_hash_comparison']['each_array_byte_match']
    target=root/'conditions'/rows[0]['condition_key']/'demand.npz'
    target.write_bytes(target.read_bytes()+b'changed')
    with pytest.raises(RuntimeError,match='Frozen input/config changed'):
        pilot.locked(root)


def test_rejected_demand_snapshot_has_no_fabricated_motion(tmp_path):
    data={'people':np.array([[2.,2.],[3.,3.]]),'radii':np.full(2,.2),
          'demand':np.array([5.,1.]),'guides':np.array([[1.,1.],[1.,4.],[4.,4.]]),'room':np.array([6.,6.])}
    cfg=tmp_path/'input.yaml'
    cfg.write_text('step: 1\n',encoding='utf-8')
    result=rejected_result(tmp_path/'rejected',{'count':3,'input_sha256':'frozen'},tmp_path,
        'PLAN_SEARCH_EXHAUSTED','PLAN_SEARCH_EXHAUSTED: gap failed',snapshot=data,config_path=cfg)
    saved=pilot.arrays(tmp_path/'rejected/trajectory.npz')
    np.testing.assert_array_equal(saved['crowd_demand'],[5.,1.])
    assert len(saved['positions'])==1 and len(saved['safe_velocity'])==0
    assert result.get('simulation_time') is None
    assert result.get('minimum_net_clearances') is None
    assert reevaluate_result(tmp_path/'rejected')['criteria_consistent']
