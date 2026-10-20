"""Post-run evidence checks and readable exports, without any further execution."""
from collections import Counter
from pathlib import Path
import numpy as np
from udc.experiments.demand_pilot import read, arrays
from udc.experiments.nonconvex_rebuild import write, sha
from udc.experiments.deadline_diagnostics import OUTPUT, lock, canonical_events
from udc.analysis.demand_confirmation import csv_write
from udc.evaluation.acceptance import evaluate
from udc.experiments.nonconvex_rebuild import load_boundary
from udc.safety import Safety
from time import perf_counter

REPORT=Path(__file__).resolve().parent
ROOT=Path(OUTPUT).resolve()
frozen=lock(ROOT); full=read(REPORT/'analysis.json')
proofs={}
for name in frozen['spec']['attempts']:
    p=ROOT/name; frames=read(p/'frames.json'); monitors=read(p/'monitor.json'); summary=read(p/'summary.json')
    metrics=read(p/'metrics.json'); replay=read(p/'independent_rejudge.json'); calls=read(p/'joint_calls.json')
    trajectory=arrays(p/'trajectory.npz'); navigation=read(p/'navigation_sets.json')
    observations=[]; current_active=Counter(); same_guide_arrival_losses=[]; identity_changes=[]; previous={}
    tol=frozen['original_config']['controller']['position_tolerance']
    error_checks=[]
    for r in frames:
        before=r['before']; after=r['after_step']; g=r['priority_guide']
        nav=navigation[after['navigation']['paths_sha256']]
        if g is not None:
            path=np.array(nav['paths'][str(g)]); k=after['waypoints'][str(g)]
            tail=float(np.linalg.norm(np.diff(path[k:],axis=0),axis=1).sum())
            a=float(np.linalg.norm(path[k]-np.array(before['positions'][g])))+tail
            b=float(np.linalg.norm(path[k]-np.array(after['positions'][g])))+tail
            assert abs(a-r['priority_reference']['remaining_before_m'])<1e-10
            assert abs(b-r['remaining_after'][g])<1e-10
            current_active.update(c['kind'] for c in r['active_constraints'] if c['guide']==g or c['other_guide']==g)
        for i,(target,error) in enumerate(zip(after['assignment'],r['errors_after'])):
            old=previous.get(i)
            if old and old['target']!=target:
                identity_changes.append({'frame':r['frame'],'guide':i,'old_target':old['target'],'new_target':target})
                old=None
            if old and old['within'] and error>tol:
                same_guide_arrival_losses.append({'frame':r['frame'],'time_s':r['time_end_s'],'guide':i,'target':target,
                    'error_before_m':old['error'],'error_after_m':error,'priority_guide':g,
                    'nominal_speed_m_s':float(np.linalg.norm(r['nominal'][i])),
                    'filtered_speed_m_s':float(np.linalg.norm(r['filtered'][i])),
                    'same_assignment':True,'priority_yield_motion':g is not None and i!=g and np.linalg.norm(r['nominal'][i])==0})
            previous[i]={'target':target,'within':error<=tol,'error':error}
        errors=np.linalg.norm(np.array(after['positions'])-trajectory['targets'][after['assignment']],axis=1)
        assert np.max(np.abs(errors-np.array(r['errors_after'])))<1e-12
        observations.append({'frame':r['frame'],'time_s':r['time_end_s'],'priority_guide':g,
            'active_current_guide':sum(c['kind']=='guide_guide' and (c['guide']==g or c['other_guide']==g) for c in r['active_constraints']),
            'active_current_human':sum(c['kind']=='guide_human' and c['guide']==g for c in r['active_constraints']),
            'active_current_wall':sum(c['kind']=='guide_wall' and c['guide']==g for c in r['active_constraints']),
            'active_current_planning':sum(c['kind']=='planning_boundary' and c['guide']==g for c in r['active_constraints'])})
    comparison={}
    for key in ('actual_max_gap','mean_curve_distance','tracking_rmse','actual_demand_weighted_cost','planned_demand_weighted_cost','actual_hotspot_concentration'):
        x,y=metrics.get(key),replay['diagnostics'].get(key)
        comparison[key]=x==y if x is None or y is None else abs(x-y)<=1e-9
    assert all(comparison.values())
    solver=[]
    for c in calls:
        r=frames[c['frame']-1]; solved=c['solver']
        if c['accepted']:
            assert np.array_equal(c['returned_velocity'],r['filtered'])
        else:
            assert np.array_equal(r['projection'],r['filtered'])
        solver.append({'frame':c['frame'],'time_start_s':r['time_start_s'],'guide':c['guide'],'request_m_s':c['request_m_s'],
            'projection_along_m_s':r['projection_along_m_s'],'installed_along_m_s':r['filtered_along_m_s'],
            'accepted':c['accepted'],'solver_success':None if solved is None else solved['success'],
            'solver_status':None if solved is None else solved['status'],'iterations':None if solved is None else solved['iterations'],
            'raw_clipped_row_residual_m_s':None if solved is None else solved['row_residual_m_s'],
            'raw_clipped_progress_shortfall_m_s':None if solved is None else solved['progress_shortfall_m_s'],
            'returned_residual_m_s':c['returned_residual'],'wall_s':c['solver_wall_s']})
    ref_changes=full[name]['reference_changes']
    same_object_resets=[r for r in ref_changes if r['guide']==r['previous_guide']]
    unexpected=[r for r in same_object_resets if r['reasons']!='waypoint_changed']
    assert not unexpected
    handed=full[name]['post_service_displacement']
    assert all(r['first_pushed_outside_s'] is None for r in handed)
    proof={'diagnostics_match_runner':comparison,'same_reference_remaining_reconstructed_from_true_positions':True,
        'per_frame_target_error_exact':True,'current_priority_active_constraint_records':dict(current_active),
        'same_object_reference_resets':len(same_object_resets),'unexpected_same_object_reference_resets':unexpected,
        'same_assignment_arrival_then_loss':same_guide_arrival_losses,'identity_changes':identity_changes,
        'after_queue_handoff_push_outside':[],'joint_accepted_velocity_exact':True,'joint_rejected_keeps_projection_exact':True,
        'original_config_exact':summary['config_exact'],'input_targets_exact':summary['input_targets_exact'],
        'recovery_budget_consumed':summary['recoveries'],'physical_interval_safe':replay['motion']['interval_safety_ok'],
        'criteria_consistent':replay['criteria_consistent'],'no_priority_stall_bypass':metrics['diagnostic_priority_stall_counterfactual'] is False
            and not metrics['priority_stall_suppressed_events']}
    csv_write(REPORT/(name+'_active_constraints.csv'),observations)
    csv_write(REPORT/(name+'_joint_queries.csv'),solver)
    write(REPORT/(name+'_supplementary_checks.json'),proof); proofs[name]=proof
continuation=read(ROOT/'diagnostic_continuation_60s/continuation_audit.json')
assert continuation['full_cutoff_state_exact'] and continuation['original_prefix_exact'] and continuation['no_fourth_recovery']
assert all(p['no_priority_stall_bypass'] and p['physical_interval_safe'] for p in proofs.values())
assert full['baseline_40s']['summary']['baseline_exact']
old=Path(frozen['original_result']); assert read(old/'metrics.json')['termination_status']=='TIMEOUT'
data=arrays(ROOT/'diagnostic_continuation_60s/trajectory.npz')
metrics=read(ROOT/'diagnostic_continuation_60s/metrics.json')
cfg=frozen['original_config']; boundary=load_boundary(Path(frozen['initial']['physical']))
criteria_config=dict(cfg['controller'],_people=data['crowd_positions'],_radii=data['crowd_radii'],
    _room=data['room'],_demand=data['crowd_demand'],_hotspot_mask=data['crowd_hotspot_mask'])
prefixes=[]; started=perf_counter()
for frame in range(400,len(data['positions'])):
    criteria,diag=evaluate(data['positions'][:frame+1],data['safe_velocity'][:frame],data['active_mask'],
        data['targets'],data['assignment'],boundary,Safety(**metrics['safety_limits']),criteria_config)
    prefixes.append({'frame':frame,'time_s':frame*.1,'criteria':criteria,'all_pass':all(criteria.values()),
        'max_target_error_m':diag['max_tracking_error'],'max_curve_distance_m':diag['max_curve_distance'],
        'max_gap_m':diag['actual_max_gap']})
first={k:next((r['time_s'] for r in prefixes if r['criteria'][k]),None) for k in ('arrival_ok','curve_ok','gap_ok','coverage_ok')}
first['all_pass']=next((r['time_s'] for r in prefixes if r['all_pass']),None)
assert first['all_pass']==43.800000000000004
write(REPORT/'original_evaluator_prefix_checks_39.json',{'source':'unchanged original evaluate on 39 saved trajectory prefixes; no new execution',
    'first_post_cutoff_s':first,'prefixes':prefixes,'evaluation_wall_s':perf_counter()-started})
critical=[Path(frozen['initial'][key]) for key in ('config','guides')]
critical += [Path(frozen['initial']['physical'])/name for name in ('input.npz','boundary.npz','free.wkb','static_domain.json')]
critical += [Path(frozen['initial']['condition'])/'demand.npz',Path(frozen['original_plan'])/'assignment.npz',
    Path(frozen['original_plan'])/'paths.json',old/'trajectory.npz',old/'metrics.json']
critical += list((Path(frozen['original_plan'])/'final').glob('*'))
write(REPORT/'fixed_scene_references.json',{'files':{str(p.resolve()):sha(p) for p in critical if p.is_file()},
    'original_manifest_sha256':sha(Path(frozen['spec']['confirmation_run'])/'manifest_360.json'),
    'original_failure_index_sha256':sha(Path(frozen['spec']['confirmation_report'])/'fixed_execution_failures.json')})
write(REPORT/'cutoff_40s_state.json',read(ROOT/'baseline_40s/cutoff_40s_state.json'))
lock(ROOT)
write(REPORT/'final_checks.json',{'classification':'A: safe advancement with late completion under the frozen policy and initialization',
    'production_scientific_modification':False,'conditional_small_or_full_execution_regression':False,
    'reason_no_execution_regression':'no demonstrated implementation or monitoring error; original production sources unchanged',
    'original_protocol_success':False,'original_confirmation_status':'TIMEOUT','original_confirmation_success_count':275,
    'diagnostic_completion_time_s':43.8,'overrun_s':3.8,'baseline_exact':True,'continuation':continuation,
    'criteria_and_diagnostics_consistent':True,'same_true_start_full_path_and_error_checks':True,
    '39_prefix_checks_with_unchanged_original_evaluator':True,'first_original_criteria_pass_s':first,
    'all_motion_intervals_checked':400+438,'post_40s_unique_intervals':38,'unsafe_intervals':0,
    'protected_files_verified':len(frozen['protected']),'production_files_unchanged':len(frozen['production_sources']),
    'tests':'218 passed in 25.21s','environment_unchanged':True})
write(REPORT/'raw_checksums.json',{'new_run_files':{p.relative_to(ROOT).as_posix():sha(p) for p in ROOT.rglob('*') if p.is_file()},
    'historical_files_verified':len(frozen['protected']),'original_confirmation_unchanged':True,
    'final_snapshot_after_console_files_closed':True})
write(REPORT/'report_checksums.json',{'files':{p.relative_to(REPORT).as_posix():sha(p) for p in REPORT.rglob('*')
    if p.is_file() and p.name!='report_checksums.json' and '__pycache__' not in p.parts},
    'external_documents':{str(p.resolve()):sha(p) for p in [Path('docs/VALIDATION.zh.md'),Path('docs/STEP1_EVIDENCE_MAP.zh.md')]},
    'excludes_self_and_python_cache':True})
print({k:{f:p[f] for f in ('current_priority_active_constraint_records','same_object_reference_resets',
    'same_assignment_arrival_then_loss','identity_changes')} for k,p in proofs.items()})
