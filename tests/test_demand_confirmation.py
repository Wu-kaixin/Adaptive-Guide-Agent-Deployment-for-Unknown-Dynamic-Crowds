"""Protocol selection, paired denominators, frozen demand and independent failures."""
import json
from pathlib import Path

import numpy as np
import pytest

from udc.analysis.demand_confirmation import (paired_units,pair_values,contrast,stratified_bootstrap,success_pairs,motion_pairs)
from udc.boundary.prescribed import boundary_from_ring
from udc.experiments.demand_confirmation import audit_seeds,first_interval,independent_row,stop_required
from udc.experiments.demand_pilot import frozen_density,physical_hash
from udc.experiments.nonconvex_rebuild import rejected_result


def settings():
    return {'bootstrap_repetitions':1000,'bootstrap_rng_seed':2026102001,'confidence':.95,
            'minimum_valid_pairs_per_scene_for_interval':2,'maximum_uncalculable_bootstrap_share':.01}


def sample_rows():
    rows=[]
    for i,scene in enumerate(('elongated','tight_u','wide_u')):
        for seed in (20,21,22):
            for demand in ('ones','hotspot5'):
                for method in ('equal_arc','projected_mass'):
                    rows.append({'physical_key':f'{scene}__seed_{seed}','scene':scene,'seed':seed,'demand':demand,
                        'method':method,'status':'SUCCESS','success':True,'target_valid':True,
                        'planned_cost':{'demand':.3-(i+1)*.005*(seed-19) if method=='projected_mass' else .3},
                        'motion':{'simulation_time_s':20.,'actual_trajectory_length_m':150.},'recoveries':0})
    return rows


def test_seed_audit_excludes_actual_same_generator_and_keeps_other_protocol_distinct(tmp_path):
    p=tmp_path/'runs/step1_nonconvex_old'; p.mkdir(parents=True)
    (p/'manifest.json').write_text(json.dumps({'random_namespace':20261019,'scene':'elongated','seed':75}))
    q=tmp_path/'reports/step1_nonconvex_old'; q.mkdir(parents=True)
    (q/'rows.csv').write_text('scene,seed,random_namespace\ntight_u,76,20261019\nwide_u,77,12345\n')
    a=audit_seeds(tmp_path)
    assert a['selected_seeds']==list(range(77,97))
    assert ['elongated',75] in a['exposed_scene_seed_pairs']
    assert ['tight_u',76] in a['exposed_scene_seed_pairs']
    assert ['wide_u',77] not in a['exposed_scene_seed_pairs']
    assert a['other_protocol_or_unattributed_records']
    assert 'not other computers' in a['scope']


def test_interval_uses_any_exposed_scene_and_never_replaces_by_outcome():
    assert first_interval({('wide_u',80)})==list(range(81,101))
    assert first_interval(set())==list(range(75,95))
    with pytest.raises(RuntimeError): first_interval({('wide_u',75)},maximum=94)


def test_primary_is_equal_scene_weighted_conditional_targets_not_row_mean():
    rows=sample_rows()
    # One missing target excludes this B pair only, while A and the unit remain.
    next(r for r in rows if r['scene']=='tight_u' and r['seed']==22 and r['demand']=='hotspot5' and r['method']=='projected_mass')['target_valid']=False
    units=paired_units(rows); result=contrast(units,'hotspot5')
    assert len(units)==9 and result['n_pairs']==8
    expected=np.mean([-.01,-.015,-.03])
    assert result['equal_scene_mean_difference']==pytest.approx(expected)
    assert result['equal_scene_mean_difference']!=pytest.approx(np.mean([p['difference'] for p in result['pairs']]))
    assert contrast(units,'ones')['n_pairs']==9


def test_whole_unit_stratified_bootstrap_is_reproducible_and_retains_missing_units():
    rows=sample_rows()
    next(r for r in rows if r['scene']=='elongated' and r['seed']==22 and r['demand']=='hotspot5' and r['method']=='projected_mass')['target_valid']=False
    units=paired_units(rows); config=settings()
    a=stratified_bootstrap(units,config); b=stratified_bootstrap(units,config)
    assert a==b and a['physical_units_resampled'] and a['A_B_methods_states_share_each_sampled_index']
    # The chance of drawing only the missing unit in a 3-unit layer exceeds 1%.
    assert a['uncalculable']>10 and a['interval'] is None
    assert 'too many' in a['reason']
    assert stratified_bootstrap(units,config,complete=False)['interval'] is None


def test_interval_unavailable_for_empty_or_too_small_stratum():
    rows=sample_rows()
    for r in rows:
        if r['scene']=='wide_u' and r['demand']=='hotspot5': r['target_valid']=False
    estimate=contrast(paired_units(rows),'hotspot5')
    assert estimate['equal_scene_mean_difference'] is None
    assert stratified_bootstrap(paired_units(rows),settings())['interval'] is None


def test_execution_failure_does_not_remove_qualified_planning_pair_or_denominator():
    rows=sample_rows()
    failed=next(r for r in rows if r['scene']=='wide_u' and r['seed']==22 and r['demand']=='hotspot5' and r['method']=='projected_mass')
    failed.update(status='TIMEOUT',success=False)
    units=paired_units(rows)
    assert contrast(units,'hotspot5')['n_pairs']==9
    assert success_pairs(units,'hotspot5')['all']=={'n_physical_units':9,'both_success':8,'equal_only':1,'projected_only':0,'both_fail':0}
    assert motion_pairs(units,'hotspot5')['n_both_success']==8


def test_demand_fields_share_physical_input_and_preserve_hotspot_before_targets():
    b=boundary_from_ring(np.array([[0.,0.],[6.,0.],[6.,6.],[0.,6.]]))
    data={'people':np.array([[1.,2.],[4.,4.]]),'radii':np.array([.2,.2]),'guides':np.array([[1.,1.],[2.,1.],[3.,1.]]),
          'room':np.array([10.,10.])}
    spec={'density':{'gaussian_bandwidth_m':1.2,'quadrature_samples':2048}}
    mask=np.array([True,False]); before=physical_hash(data)
    a,ar=frozen_density(b,data,np.ones(2),np.zeros(2,bool),spec)
    z,zr=frozen_density(b,data,np.array([5.,1.]),mask,spec,5.)
    assert physical_hash(data)==before and not np.array_equal(a['rho'],z['rho'])
    assert not ar['excess_applicable'] and zr['excess_applicable'] and z['excess_window'].any()
    assert ar['array_sha256']!=zr['array_sha256']
    assert np.ptp(a['rho'])>0  # person demand 1 is not a constant curve field


def test_stage_rejection_has_independent_artifacts_and_null_motion(tmp_path):
    row={'task_id':'rejected','count':3,'input_sha256':'frozen'}
    data={'people':np.array([[2.,2.],[3.,3.]]),'radii':np.array([.2,.2]),'demand':np.array([5.,1.]),
          'guides':np.array([[1.,1.],[1.,4.],[4.,4.]]),'room':np.array([6.,6.])}
    cfg=tmp_path/'input.yaml'; cfg.write_text('step: 1\n')
    rejected_result(tmp_path/'results/rejected',row,tmp_path,'BOUNDARY_INVALID','BOUNDARY_INVALID: evidence',snapshot=data,config_path=cfg)
    replay=independent_row(tmp_path,row)
    assert replay['criteria_consistent'] and replay['diagnostics_consistent']
    assert replay['motion'] is None and not replay['executed']


def test_safety_program_or_rejudge_error_stops_but_scientific_failure_does_not():
    for status in ('TIMEOUT','REPLAN_EXHAUSTED','BOUNDARY_INVALID','PLAN_SEARCH_EXHAUSTED','INITIALIZATION_INVALID'):
        assert not stop_required({'status':status,'offline_consistent':True})
    for status in ('SAFETY_VIOLATION','EXPERIMENT_ERROR'):
        assert stop_required({'status':status,'offline_consistent':True})
    assert stop_required({'status':'SUCCESS','offline_consistent':False})
