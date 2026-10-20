"""New-input confirmation estimand and sample/denominator contracts."""
import numpy as np
import pytest

from udc.experiments.demand_confirmation import first_interval
from udc.analysis.resource_confirmation import inference, motion_subsets
from udc.analysis.resource_response import units, interactions, contrast


def sample():
    rows=[]
    for i,scene in enumerate(('elongated','tight_u','wide_u')):
        for seed in range(3):
            for n in (0,2,4):
                for method in ('equal_arc','projected_mass'):
                    rows.append({'scene':scene,'seed':seed,'physical_key':f'{scene}_{seed}','increment':n,
                        'count':11+n,'method':method,'target_valid':True,'success':True,
                        'planned_cost':{'demand':.3-.01*n-(.01+.002*n*(i+1) if method=='projected_mass' else 0)},
                        'motion':{'simulation_time_s':20+n,'actual_trajectory_length_m':100+n},'recoveries':0})
    return rows


def settings():
    return {'bootstrap_rng_seed':2026102003,'bootstrap_repetitions':1000,
        'minimum_valid_pairs_per_scene_for_interval':2,'maximum_uncalculable_bootstrap_share':.01}


def test_new_seed_interval_starts_at_95_and_excludes_any_scene_exposure():
    assert first_interval(set(),95)==list(range(95,115))
    assert first_interval({('tight_u',99),('wide_u',119)},95)==list(range(120,140))
    with pytest.raises(RuntimeError): first_interval({('wide_u',4990)},4980)


def test_primary_uses_four_targets_and_keeps_execution_failure_without_requiring_N4():
    rows=sample()
    for r in rows:
        if r['scene']=='elongated' and r['seed']==0:
            if r['increment']==4: r['target_valid']=False
            if r['increment']==2 and r['method']=='projected_mass': r['success']=False
    us=units(rows)
    assert len(interactions(us,2))==9
    assert len(interactions(us,2,common=True))==8
    result=inference(us,settings())
    assert result['estimates']['interaction_N2']['valid_pairs_by_scene']==dict.fromkeys(('elongated','tight_u','wide_u'),3)
    assert result['estimates']['interaction_N2']['interval']==pytest.approx([-.008,-.008])


def test_primary_equal_scene_weight_not_pooled_survivor_mean():
    rows=sample()
    for r in rows:
        if r['scene']=='elongated' and r['seed']==0: r['target_valid']=False
    result=contrast(interactions(units(rows),2))
    assert result['equal_scene_mean_difference']==pytest.approx(-.008)
    assert result['all']['mean_difference']!=pytest.approx(-.008)


def test_bootstrap_missing_whole_units_and_empty_stratum_withholds_interval():
    rows=sample()
    for r in rows:
        if r['seed']==2: r['target_valid']=False
    a=inference(units(rows),settings()); b=inference(units(rows),settings())
    assert a==b
    assert len({v['uncalculable'] for v in a['estimates'].values()})==1
    assert a['estimates']['interaction_N2']['interval'] is None
    for r in rows:
        if r['scene']=='wide_u': r['target_valid']=False
    assert inference(units(rows),settings())['estimates']['interaction_N2']['interval'] is None


def test_motion_subsets_distinguish_own_N_from_common_six_and_keep_full_planning_unit():
    rows=sample()
    next(r for r in rows if r['scene']=='wide_u' and r['seed']==0 and r['increment']==4 and r['method']=='projected_mass')['success']=False
    us=units(rows); result=motion_subsets(us)
    def selected(n,label):
        return next(r for r in result if r['scene']=='wide_u' and r['increment']==n and r['method']=='equal_arc' and r['subset']==label)
    assert selected(0,'own_N_both_success')['n']==3
    assert selected(0,'all_six_common_success')['n']==2
    assert selected(4,'own_N_both_success')['n']==2
    assert len(interactions(us,2))==9
    assert selected(0,'all_six_common_success')['per_guide_m']==pytest.approx(100/11)
