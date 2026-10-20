"""Phase opportunity, cyclic identities, frozen windows and independent evidence."""
from dataclasses import replace

import numpy as np
import pytest
from shapely.geometry import Polygon

from udc.boundary.prescribed import boundary_from_ring
from udc.deployment.planners import Plan, _interpolate, periodic_gaps, quadrature_mass
from udc.deployment.admissible import admissible_phase
from udc.experiments.phase_attribution import (candidate_cache, choose_policy, digest,
    phase_grid, score_sites, shifted_layout)
from udc.navigation import navigation_domain


@pytest.fixture
def layout():
    b=boundary_from_ring(np.array([[2.,2.],[3.5,2.],[3.5,3.5],[2.,3.5]]))
    sites=np.array([0.,1.,2.5,4.])
    plan=Plan(_interpolate(b,sites),sites,np.array([1.]),2.,'saved_layout',0,'SAVED',1.)
    domain=navigation_domain([10,10],.55,Polygon([[6,6],[7,6],[7,7],[6,7]]),.55)
    q,mass,_=quadrature_mass(b,np.array([1.,.4,.2,.7]),2048)
    field={'rho':np.array([1.,.4,.2,.7]),'mass':mass,'quadrature':q,
           'excess_rho':np.array([.7,.1,0.,.2]),'excess_mass':mass*.2,
           'excess_window':np.r_[np.ones(700,bool),np.zeros(1348,bool)]}
    return b,plan,domain,field


def test_main_grid_is_legacy_and_half_grid_is_separate():
    length=20.735250954732287
    main=phase_grid(length); half=phase_grid(length,offset=.5)
    expected=np.array([(i if i<=128 else i-256)*length/256 for i in range(256)])
    np.testing.assert_array_equal(main,expected)
    assert len(main)==len(half)==256 and 0 in main and 0 not in half
    assert len(set(np.round(main/length,12))&set(np.round(half/length,12)))==0
    np.testing.assert_allclose(np.sort(half),(np.arange(256)-127.5)*length/256,atol=1e-13)


def test_full_precision_gaps_seam_and_raw_index_mapping_under_cyclic_permutation(layout):
    b,plan,_,_=layout
    permuted=replace(plan,positions=np.roll(plan.positions,2,axis=0),arc_positions=np.roll(plan.arc_positions,2))
    for phase in phase_grid(b.length):
        arcs,points,origins,expected=shifted_layout(b,plan,phase)
        other,other_points,other_origins,_=shifted_layout(b,permuted,phase)
        np.testing.assert_array_equal(arcs,other)
        np.testing.assert_array_equal(points,other_points)
        np.testing.assert_allclose(periodic_gaps(arcs,b.length),expected,atol=1e-14)
        np.testing.assert_allclose(arcs,(plan.arc_positions[origins]+phase)%b.length,atol=1e-14)
        np.testing.assert_allclose(other,(permuted.arc_positions[other_origins]+phase)%b.length,atol=1e-14)
        assert periodic_gaps(arcs,b.length).max()==pytest.approx(2.)
        assert periodic_gaps(arcs,b.length)[-1]==pytest.approx(expected[-1])


def test_policies_separate_repair_cost_selection_and_no_op():
    cache={'records':[{'index':0,'accepted':False,'relative_phase_m':0.,'score':{'demand':.1}},
        {'index':1,'accepted':True,'relative_phase_m':.1,'score':{'demand':4.}},
        {'index':2,'accepted':True,'relative_phase_m':-.1,'score':{'demand':3.}},
        {'index':3,'accepted':True,'relative_phase_m':1.,'score':{'demand':1.}}]}
    assert choose_policy('P0',False,cache) is None
    assert choose_policy('P1',False,cache)==2  # signed tie; cost not consulted
    assert choose_policy('P2',False,cache)==3
    assert choose_policy('P3',True,cache)==3
    assert choose_policy('P1',True,cache) is None
    assert choose_policy('P2',True,cache) is None
    cache['records'][1]['score']['demand']=.0001
    assert choose_policy('P1',False,cache)==2
    for r in cache['records']: r['accepted']=False
    assert choose_policy('P3',True,cache)==-1


def test_candidate_cache_contains_all_pairs_and_matches_original_phase_selection(layout):
    b,plan,domain,field=layout
    identity={'input':'frozen','curve':'c','layout':'l','density':'rho','domain':domain.geometry_sha256}
    cache,payload=candidate_cache(b,plan,domain,field,identity,gap_limit=2.1)
    assert cache['candidate_count']==256 and 0<cache['feasible_count']<256
    assert payload['all_pair_distances'].shape==(256,4,4)
    for index in (0,1,128,255):
        d=payload['all_pair_distances'][index]
        assert np.diag(d).sum()==0
        np.testing.assert_array_equal(d,d.T)
        assert cache['records'][index]['cyclic_gap_error_m']<1e-12
        assert cache['records'][index]['curve_error_m']<1e-12
        assert cache['records'][index]['pair_ok']==bool(np.min(d+np.eye(4)*1e9)>=.75)
    changed,_=candidate_cache(b,plan,domain,field,{**identity,'density':'other'},gap_limit=2.1)
    assert cache['cache_key']!=changed['cache_key']
    half,_=candidate_cache(b,plan,domain,field,identity,offset=.5,gap_limit=2.1)
    assert half['cache_key']!=cache['cache_key']
    # The old function returns the original legal layout, matching conditional P2.
    old,details=admissible_phase(b,plan,domain,field['rho'],phase_bins=256,gap_limit=2.1,
                               separation=.75,quadrature_samples=2048)
    assert choose_policy('P2',True,cache) is None
    np.testing.assert_array_equal(old.positions,plan.positions)
    assert details['selected_phase_m']==0.


def test_refined_integration_keeps_original_window_and_does_not_select_new_targets(layout):
    b,plan,_,field=layout
    field['rho']=np.ones(4)
    field['excess_rho']=np.array([.8,.1,0.,.1])
    # Deliberately freeze a window unrelated to the rho threshold: it must survive.
    field['excess_window']=np.r_[np.ones(700,bool),np.zeros(1348,bool)]
    saved=plan.arc_positions.copy()
    results=[score_sites(b,field,plan.arc_positions,n) for n in (2048,4096,8192)]
    for n,result in zip((2048,4096,8192),results):
        q=(np.arange(n)+.5)*b.length/n
        outside=~field['excess_window'][(q/b.length*2048).astype(int)]
        delta=(q[:,None]-saved[None,:]+b.length/2)%b.length-b.length/2
        exact=float(np.min(delta**2,axis=1)[outside].mean())
        assert result['background_arc']==pytest.approx(exact,abs=1e-12)
    np.testing.assert_array_equal(saved,plan.arc_positions)
    assert abs(results[-1]['demand']-results[-2]['demand'])<1e-6
