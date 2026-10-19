"""Protocol contracts, real planning interfaces, and shared physical metrics."""
import copy
from pathlib import Path

import numpy as np
import pytest
import yaml

from udc.boundary import construct_boundary, estimate_boundary
from udc.evaluation.comparison import common_motion_metrics
from udc.experiments import boundary_comparison as protocol
from udc.experiments.nonconvex_rebuild import draw_cell, sha
from udc.experiments.scene_closure import _execution_config
from udc.safety import Safety


def make_case(tmp_path,monkeypatch,scene_name,seed):
    spec,generator,base=protocol.load_spec()
    scene=next(s for s in base['scenes'] if s['name']==scene_name)
    data,_=draw_cell(generator,base,scene,seed)
    cell=tmp_path/'input'; cell.mkdir()
    np.savez(cell/'input.npz',**data)
    cfg=_execution_config(cell,data['people'],np.array([[0,0],[30,0],[30,30],[0,30]]),
        spec['guide_counts'][scene_name],seed,base)
    config=yaml.safe_load(cfg.read_text()); config['controller']['method']='equal_arc'
    config['guides']['initial_positions']=data['guides'].tolist()
    cfg.write_text(yaml.safe_dump(config),encoding='utf-8')
    ref=protocol.input_reference(cell,cfg)
    ref.update(config_sha256=sha(cfg),people_sqlite_sha256=sha(config['crowd']['trajectory_file']),origin='test_protocol_reconstruction')
    lock={'spec':spec,'base':base,'references':{f'{scene_name}__seed_{seed}':ref},'provenance':{'source_sha256':'fixture'}}
    monkeypatch.setattr(protocol,'_locked',lambda root:lock)
    rows=protocol.schedule(spec,[[scene_name,seed]])
    return rows,ref


def test_convex_adapter_contains_person_radii_and_keeps_existing_output():
    positions=np.array([[10.,10.],[10.6,10.],[10.3,10.5]])
    radii=np.array([.2,.4,.3])
    boundary,info,occupied,_=construct_boundary('convex_hull',positions,radii,[30,30])
    direct=estimate_boundary(positions,1.3,.1,[30,30],.55,.75,radii)
    for name in ('crowd','deployment','arc'):
        np.testing.assert_array_equal(getattr(boundary,name),getattr(direct,name))
    assert occupied.bounds[2]==pytest.approx(11.) # x=10.6 plus its own 0.4 m radius.
    assert info['disk_quad_segs_effective']==32 and info['controller_covers_acceptance_disks_16']
    assert info['uncovered_disk_area_m2']<1e-12


def test_invalid_common_input_rejects_both_before_estimator(tmp_path,monkeypatch):
    rows,ref=make_case(tmp_path,monkeypatch,'tight_u',35)
    assert not ref['quality']['accepted']
    monkeypatch.setattr(protocol,'construct_boundary',lambda *a,**k:pytest.fail('estimator saw invalid shared input'))
    results=[protocol.run_row((str(tmp_path),row,'smoke',False)) for row in rows]
    assert len({r['input_sha256'] for r in results})==1
    for result in results:
        assert result['termination_status']=='INITIALIZATION_INVALID'
        assert result['common_task'] is None and result['boundary_description'] is None
        assert result['offline_consistent']


@pytest.mark.parametrize('seed',[35,51])
def test_real_estimator_acceptance_and_phase_policies_share_input(tmp_path,monkeypatch,seed):
    rows,_=make_case(tmp_path,monkeypatch,'elongated',seed)
    results=[protocol.run_row((str(tmp_path),row,'smoke',False)) for row in rows]
    convex,disk=results
    assert convex['termination_status']=='NOT_EXECUTED'
    assert convex['common_task'] is None and convex['initial_paths_ok']
    assert len({r['input_sha256'] for r in results})==1 and convex['count']==disk['count']==11
    if seed==35:
        assert disk['termination_status']=='BOUNDARY_INVALID'
        assert disk['boundary_description']['component_count']>1
    else:
        assert disk['termination_status']=='NOT_EXECUTED' and disk['phase_triggered']
        assert not convex['phase_triggered']
        assert disk['phase_selected_m']==pytest.approx(-.080997074041923)
        assert disk['target_geometry']['accepted']
        assert convex['domain_sha256']!=disk['domain_sha256']
        assert all(r['exact_shared_input_and_target_pairing'] for r in results)
    assert all(r['common_task'] is None and r['offline_consistent'] for r in results)


def test_curve_failure_remains_in_common_denominator_and_unexecuted_is_missing():
    motion={'complete_deployment_success':True,'simulation_time_s':2.,'actual_trajectory_length_m':3.}
    rows=[]
    for seed in (1,2,3):
        for estimator in protocol.ESTIMATORS:
            status=('SUCCESS' if seed==1 else 'NOT_EXECUTED' if seed==2 and estimator=='convex_hull'
                    else 'BOUNDARY_INVALID' if seed==2 else 'INITIALIZATION_INVALID')
            rows.append({'task_id':f'{seed}_{estimator}','scene':'elongated','seed':seed,'estimator':estimator,
                'common_input_legal':seed!=3,'common_task':motion if status=='SUCCESS' else None,
                'termination_status':status,'initial_paths_ok':status in ('SUCCESS','NOT_EXECUTED'),
                'phase_triggered':False,'offline_consistent':True})
    summary=protocol.summarize(rows,rows)
    assert summary['people_legal_common_units']==2 and summary['paired_continuous_unit_count']==1
    assert summary['estimators']['convex_hull']['complete_deployment_success_fraction'] is None
    disk=summary['estimators']['disk_union_closed']
    assert disk['primary_denominator']==2 and disk['complete_deployment_success_fraction']==.5
    assert disk['outcomes_in_primary_denominator']['BOUNDARY_INVALID']==1


def test_common_metrics_measure_entire_actual_intervals_without_boundary_truth():
    limits=Safety(.75,.75,.55,1.)
    positions=np.array([[[1.,5.]],[[9.,5.]]]); velocities=np.array([[[1.,0.]]])
    cfg={'hold_steps':1,'position_tolerance':.03,'speed_tolerance':.03}
    args=(positions,velocities,np.array([[5.,5.]]),np.array([.2]),np.array([10.,10.]),
        np.array([[9.,5.]]),np.array([0]),cfg,limits,8.)
    result=common_motion_metrics(*args)
    assert not result['interval_safety_ok'] and result['unsafe_interval_count']==1
    assert result['actual_trajectory_length_m']==8.
    assert common_motion_metrics(positions[:1],velocities[:0],*args[2:]) is None


def test_future_schedule_has_fixed_resources_and_no_generated_inputs():
    spec,_,_=protocol.load_spec()
    future=protocol.schedule(spec,[[scene,seed] for scene in spec['scenes'] for seed in spec['future']['seeds']])
    assert len(future)==120 and {r['seed'] for r in future}==set(range(55,75))
    for scene in spec['scenes']:
        assert {r['count'] for r in future if r['scene']==scene}=={spec['guide_counts'][scene]}
    changed=copy.deepcopy(spec); changed['deployment']['phase_bins']=512
    assert spec['deployment']['phase_bins']==256
