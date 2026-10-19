"""Read saved confirmation artifacts only; no generator, planner or replay.

The additional physical audit implements analytic interval minima directly
from saved coordinates. Existing independent acceptance files remain intact.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import shapely
from shapely.geometry import Point, Polygon
from udc.experiments.provenance import provenance


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stats(values):
    values=np.asarray([v for v in values if v is not None],dtype=float)
    return None if not len(values) else dict(n=len(values), mean=float(values.mean()),
        median=float(np.median(values)), minimum=float(values.min()), maximum=float(values.max()))


def intervals(positions, people, room):
    start=positions[:-1]; delta=np.diff(positions,axis=0)
    rel=start[:,:,None,:]-people[None,None,:,:]
    denom=np.sum(delta*delta,axis=-1)[:,:,None]
    t=np.clip(np.divide(-np.sum(rel*delta[:,:,None,:],axis=-1),denom,
        out=np.zeros(rel.shape[:-1]),where=denom>0),0,1)
    human=np.linalg.norm(rel+t[:,:,:,None]*delta[:,:,None,:],axis=-1).min(axis=(1,2))
    i,j=np.triu_indices(positions.shape[1],1)
    rel=start[:,i]-start[:,j]; velocity=delta[:,i]-delta[:,j]
    denom=np.sum(velocity*velocity,axis=-1)
    t=np.clip(np.divide(-np.sum(rel*velocity,axis=-1),denom,
        out=np.zeros(denom.shape),where=denom>0),0,1)
    guide=np.linalg.norm(rel+t[:,:,None]*velocity,axis=-1).min(axis=1)
    vertex_wall=np.minimum(positions,room-positions).min(axis=(1,2))
    wall=np.minimum(vertex_wall[:-1],vertex_wall[1:])
    return {'crowd':human,'guides':guide,'walls':wall}


STAGES=['common_input_rejection','boundary_or_curve_invalid','quantity_necessary_condition',
    'target_geometry_search','static_domain_or_phase_search','initial_assignment_or_path',
    'execution_recovery_coordination_or_timeout','terminal_arrival_curve_or_coverage',
    'safety_violation','program_error','external_interruption','not_executed','success']


def stage(row):
    status=row['termination_status']
    if status=='SUCCESS': return 'success'
    if status=='INITIALIZATION_INVALID': return 'common_input_rejection'
    if status in ('BOUNDARY_INVALID','OFFSET_INVALID'): return 'boundary_or_curve_invalid'
    if status=='CAPACITY_SHORTFALL': return 'quantity_necessary_condition'
    if status=='EXPERIMENT_ERROR': return 'program_error'
    if status=='NOT_EXECUTED': return 'not_executed'
    if status=='SAFETY_VIOLATION': return 'safety_violation'
    if row['stage']=='static_target_domain': return 'static_domain_or_phase_search'
    if row['stage']=='target_geometry': return 'target_geometry_search'
    if row['stage']=='initial_navigation': return 'initial_assignment_or_path'
    if status=='COVERAGE_NOT_MET': return 'terminal_arrival_curve_or_coverage'
    return 'execution_recovery_coordination_or_timeout'


def joint(rows):
    cells=[]
    for scene,seed in sorted({(r['scene'],r['seed']) for r in rows}):
        pair={r['estimator']:r for r in rows if (r['scene'],r['seed'])==(scene,seed)}
        assert len(pair)==2
        if not pair['convex_hull']['common_input_legal']: state='common_input_rejection'
        elif any(r['termination_status'] in ('NOT_EXECUTED','EXPERIMENT_ERROR','EXTERNAL_INTERRUPTION') for r in pair.values()): state='unresolved'
        else:
            c,d=[bool((pair[k]['common_task'] or {}).get('complete_deployment_success')) for k in ('convex_hull','disk_union_closed')]
            state='both_success' if c and d else 'convex_only' if c else 'disk_only' if d else 'both_failed'
        cells.append({'scene':scene,'seed':seed,'joint_status':state,
            **{k:r['termination_status'] for k,r in pair.items()}})
    counts={k:sum(c['joint_status']==k for c in cells) for k in
        ('both_success','convex_only','disk_only','both_failed','unresolved','common_input_rejection')}
    m=len(cells)-counts['common_input_rejection']
    return dict(planned_units=len(cells),legal_units=m,joint_counts=counts,
        disk_minus_convex_success_fraction=None if counts['unresolved'] or not m else (counts['disk_only']-counts['convex_only'])/m,units=cells)


def analyze(root,out):
    started=perf_counter()
    summary=read(root/'future_summary.json'); rows=summary['rows']
    assert len(rows)==120 and len({r['task_id'] for r in rows})==120
    assert summary['completed_rows']==120
    frozen=read(root/'future_freeze_verified_before_results.json')
    assert all(sha(root/p)==digest for p,digest in frozen['files'].items())
    lock=read(root/'protocol.json')
    assert provenance({},None,'fixed_n',None)['source_sha256']==lock['provenance']['source_sha256']
    historical=read(Path('reports/step1_boundary_estimator_protocol/raw_checksums.json'))
    assert all(sha(path)==digest for path,digest in historical.items())
    references=read(root/'future_inputs_frozen.json')['references']
    write(out/'fresh_seed_audit_before_freeze.json',read(root/'future_inputs_frozen.json')['seed_audit'])
    write(out/'input_map_60.json',frozen['input_map'])
    write(out/'execution_preflight.json',read(root/'full_comparison_execution_preflight.json'))
    write(out/'live_worker_environment.json',read(root/'full_comparison_live_worker_environment.json'))
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as cpu:
        cpu_name=winreg.QueryValueEx(cpu,'ProcessorNameString')[0]
    write(out/'execution_lock.json',{'actual_execution_commit':'3220233acf75dbd2a3a9cc28b8f91fab9f2e1255',
        'historical_algorithm_baseline':lock['spec']['baseline_commit'],
        'scientific_source_sha256':lock['provenance']['source_sha256'],
        'locked_file_sha256':{k:lock[k] for k in ('spec_sha256','generator_spec_sha256','base_spec_sha256')},
        'freeze_file_sha256':{p:sha(root/p) for p in ('future_inputs_frozen.json','future_manifest_120.json','future_freeze.json','future_freeze_verified_before_results.json')},
        'cpu_name_registry':cpu_name,'GEOS':shapely.geos_version_string,
        'scope_authorization':'Full 120 rows explicitly authorized; historical run_in_this_round=false unchanged',
        'new_input_origin':'prospectively frozen new inputs, not reconstruction of historical originals',
        'reservation_manifest_metadata':'inputs_generated=false describes preparation time; actual completion is in future_inputs_frozen.json and future_summary.json'})
    audit_rows=[]; evidence=[]; ledger=[]; coverage=[]; execution_events=[]
    for row in rows:
        directory=root/'future/results'/row['task_id']
        artifacts=root/'future/plans'/row['task_id']
        online=read(directory/'metrics.json'); offline=read(directory/'reevaluated_metrics.json')
        assert row['offline_consistent'] and offline['criteria_consistent']
        ref=references[f"{row['scene']}__seed_{row['seed']}"]
        motion=row['common_task']
        audit={'task_id':row['task_id'],'executed':motion is not None,
            'online_status':row['termination_status'],'offline_criteria':offline['criteria'],
            'criteria_consistent':offline['criteria_consistent'],
            'diagnostics_consistent':offline['diagnostics_consistent'],
            'original_online_sha256':sha(directory/'metrics.json'),
            'original_offline_sha256':sha(directory/'reevaluated_metrics.json'),
            'trajectory_sha256':sha(directory/'trajectory.npz'),
            'physical_motion':None}
        if motion is not None:
            assert online['commit']=='3220233acf75dbd2a3a9cc28b8f91fab9f2e1255'
            assert not online['dirty']
            assert online['source_sha256']==lock['provenance']['source_sha256']
            assert online['diagnostic_priority_stall_counterfactual'] is False
            with np.load(directory/'trajectory.npz',allow_pickle=False) as actual,np.load(Path(ref['cell'])/'input.npz') as frozen_data:
                assert all(np.array_equal(actual[a],frozen_data[b]) for a,b in
                    [('crowd_positions','people'),('crowd_radii','radii'),('crowd_demand','demand'),('room','room')])
                assert np.array_equal(actual['positions'][0],frozen_data['guides'])
                positions=actual['positions']; values=intervals(positions,actual['crowd_positions'],actual['room'])
                residual={k:values[k]-online['safety_limits'][{'crowd':'crowd_distance','guides':'guide_distance','walls':'wall_distance'}[k]] for k in values}
                minima={k:float(v.min()) for k,v in values.items()}
                net={k:float(v.min()) for k,v in residual.items()}
                assert all(abs(minima[k]-motion['minimum_center_distances_m'][k])<1e-12 for k in minima)
                unsafe=np.logical_or.reduce([v < -1e-7 for v in residual.values()])
                travel=float(np.linalg.norm(np.diff(positions,axis=0),axis=-1).sum())
                assert abs(travel-motion['actual_trajectory_length_m'])<1e-12
                assert not unsafe.any()
                assert abs(float(actual['time'][-1]-actual['time'][0])-motion['simulation_time_s'])<1e-10
                audit['physical_motion']={'executed_intervals':len(positions)-1,
                    'minimum_center_distances_m':minima,'unclipped_minimum_residuals_m':net,
                    'minimum_interval_index':{k:int(v.argmin()) for k,v in values.items()},
                    'unsafe_interval_indices':np.flatnonzero(unsafe).tolist(),
                    'actual_trajectory_length_m':travel,'simulation_time_s':motion['simulation_time_s'],
                    'maximum_actual_interval_speed_m_s':float((np.linalg.norm(np.diff(positions,axis=0),axis=-1)/.1).max()),
                    'exact_paired_physical_input':True,'synchronous_guide_minima':True}
                assert audit['physical_motion']['maximum_actual_interval_speed_m_s']<=1.+1e-7
            assert offline['success']==online['success']
            execution_events.append({'task_id':row['task_id'],'scene':row['scene'],'seed':row['seed'],
                'estimator':row['estimator'],'recovery_count':online['replan_count'],
                'recovery_attempt_count':len(online['recovery_attempts']),
                'recovery_triggers':dict(Counter(e.get('trigger_reason') for e in online['recovery_attempts'])),
                'deadlock_events':online['deadlock_events'],
                'coordination_activated':online['coordination'].get('activated',False),
                'coordination_steps':online['coordination'].get('steps',0),
                'coordination_exit_reason':online['coordination'].get('exit_reason'),
                'counterfactual':online['diagnostic_priority_stall_counterfactual']})
        write(directory/'confirmation_interval_audit.json',audit)
        audit_rows.append(audit)
        d=row['boundary_description']
        if d:
            occupied=shapely.from_wkb((artifacts/'occupied.wkb').read_bytes())
            reference=shapely.from_wkb((artifacts/'physical_reference_disks.wkb').read_bytes())
            assert bool(occupied.covers(reference))==d['covers_observed_disks_32_strict']
            assert float(reference.difference(occupied).area)==d['uncovered_disk_area_m2']
            exterior=Polygon(occupied.exterior) if occupied.geom_type=='Polygon' else None
            actual_outer=None
            if d['curve_valid']:
                with np.load(artifacts/'boundary.npz') as boundary_data:
                    actual_outer=Polygon(boundary_data['crowd'])
                assert actual_outer.equals(exterior)
            with np.load(Path(ref['cell'])/'input.npz') as data:
                per_person=[]
                if exterior is not None:
                    for index,(point,radius) in enumerate(zip(data['people'],data['radii'])):
                        disk=Point(point).buffer(float(radius),quad_segs=16)
                        outside=disk.difference(exterior)
                        if not exterior.covers(disk):
                            vertices=[list(p) for p in disk.exterior.coords if not exterior.covers(Point(p))]
                            per_person.append({'person_index':index,'position':point.tolist(),'radius':float(radius),
                                'strict_covers':False,'de9im':exterior.relate(disk),
                                'difference_type':outside.geom_type,'difference_area_m2':float(outside.area),
                                'difference_length_m':float(outside.length),'difference_wkt':outside.wkt,
                                'noncovered_disk_vertices':vertices,
                                'maximum_noncovered_vertex_distance_m':max((exterior.distance(Point(p)) for p in vertices),default=None)})
            record={'task_id':row['task_id'],'strict_full_occupied_covers_reference32':d['covers_observed_disks_32_strict'],
                'full_occupied_reference32_difference_area_m2':d['uncovered_disk_area_m2'],
                'controller_outer_ring_installed':bool(d['curve_valid']),
                'extracted_outer_covers_all_acceptance_disks16':None if exterior is None else not per_person,
                'extracted_outer_ring_sha256':None if exterior is None else hashlib.sha256(exterior.wkb).hexdigest(),
                'controller_outer_ring_sha256':None if actual_outer is None else hashlib.sha256(actual_outer.wkb).hexdigest(),
                'controller_outer_representation':'actual boundary.npz crowd array' if actual_outer is not None else 'not installed; extracted exterior used for refusal diagnosis only',
                'full_occupied_sha256':d['occupied_wkb_sha256'],'reference32_sha256':d['reference_wkb_sha256'],
                'acceptance16_noncovered_people':per_person}
            write(artifacts/'confirmation_coverage_audit.json',record); coverage.append(record)
        if row['termination_status']!='SUCCESS':
            evidence.append({'task_id':row['task_id'],'failure_layer':stage(row),'status':row['termination_status'],
                'first_recorded_stage':row['stage'],'reason':row['failure_reason'],
                'input_quality':ref['quality'],'boundary_description':d,
                'coverage_audit':None if not d else record,'common_motion':motion,
                'state':read(directory/'state.json'),'result_path':str(directory),
                'no_confirmation_rerun_or_algorithm_change':True})
        ledger.append({'task_id':row['task_id'],'scene':row['scene'],'seed':row['seed'],'estimator':row['estimator'],
            'input_sha256':row['input_sha256'],'common_input_legal':row['common_input_legal'],
            'fixed_N':row['count'],'stage_outcome':stage(row),'status':row['termination_status'],
            'reason':row['failure_reason'],'executed':motion is not None,
            'simulation_time_s':None if motion is None else motion['simulation_time_s'],
            'actual_trajectory_length_m':None if motion is None else motion['actual_trajectory_length_m'],
            'physical_safety_ok':None if motion is None else motion['interval_safety_ok'],
            'independent_acceptance_pass':None if motion is None else offline['success'],
            'source_sha256':row['source_sha256']})
    write(out/'status_ledger_120.json',ledger)
    with (out/'status_ledger_120.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(ledger[0]));writer.writeheader();writer.writerows(ledger)
    write(out/'failure_evidence.json',evidence)
    write(out/'coverage_predicates.json',coverage)
    write(out/'execution_events.json',execution_events)
    write(out/'independent_audit.json',{'tasks':len(audit_rows),'executed':sum(a['executed'] for a in audit_rows),
        'all_criteria_consistent':all(a['criteria_consistent'] for a in audit_rows),
        'all_saved_diagnostics_consistent':all(all(a['diagnostics_consistent'].values()) for a in audit_rows),
        'total_executed_intervals':sum(a['physical_motion']['executed_intervals'] for a in audit_rows if a['executed']),
        'maximum_actual_interval_speed_m_s':max(a['physical_motion']['maximum_actual_interval_speed_m_s'] for a in audit_rows if a['executed']),
        'unsafe_executed_rows':sum(bool(a['physical_motion']['unsafe_interval_indices']) for a in audit_rows if a['executed']),
        'minimum_unclipped_residuals_m':{k:min(a['physical_motion']['unclipped_minimum_residuals_m'][k] for a in audit_rows if a['executed']) for k in ('crowd','guides','walls')},
        'rows':audit_rows})
    analysis={'joint':joint(rows),'by_scene':{},'stage_counts':{},'paired_continuous':{},'geometry':{},
        'counts_all_planned_rows':summary['outcomes_all_planned_rows'],'execution_wall_s':summary['wall_s']}
    for scope in ('all','elongated','tight_u','wide_u'):
        selected=rows if scope=='all' else [r for r in rows if r['scene']==scope]
        by_est={}
        for estimator in ('convex_hull','disk_union_closed'):
            est=[r for r in selected if r['estimator']==estimator]
            by_est[estimator]={k:sum(stage(r)==k for r in est) for k in STAGES}
            valid=[r for r in est if r['boundary_description']]
            curves=[r for r in valid if r['boundary_description']['curve_valid']]
            analysis['geometry'][f'{scope}/{estimator}']={'common_legal_denominator':sum(r['common_input_legal'] for r in est),
                'constructed_occupied_count':len(valid),'valid_curve_count':len(curves),
                'components':dict(Counter(r['boundary_description']['component_count'] for r in valid)),
                'holes':dict(Counter(r['boundary_description']['occupied_holes'] for r in valid)),
                'occupied_perimeter_includes_holes_m':stats([r['boundary_description']['occupied_perimeter_m'] for r in valid]),
                'occupied_area_m2':stats([r['boundary_description']['occupied_area_m2'] for r in valid]),
                'concavity_area_m2':stats([r['boundary_description']['concavity_area_m2'] for r in valid]),
                'extra_envelope_area_m2':stats([r['boundary_description']['extra_envelope_area_m2'] for r in valid]),
                'filled_hole_area_m2':stats([r['boundary_description']['controller_filled_hole_area_m2'] for r in valid]),
                'strict_covers32_count':sum(r['boundary_description']['covers_observed_disks_32_strict'] for r in valid),
                'zero_difference_area32_count':sum(r['boundary_description']['uncovered_disk_area_m2']==0 for r in valid),
                'curve_length_m':stats([r['boundary_description']['curve_length_m'] for r in curves]),
                'necessary_count_distribution':dict(Counter(r['resource']['necessary_min_count'] for r in curves)),
                'quantity_necessary_condition_passes':sum(r['resource']['necessary_condition_met'] for r in curves),
                'positive_illegal_arc_rows':sum(r['curve_domain']['strict_illegal_length_m']>0 for r in curves),
                'illegal_arc_length_m':stats([r['curve_domain']['strict_illegal_length_m'] for r in curves]),
                'maximum_penetration_lower_m':max((r['curve_domain']['max_penetration_lower_m'] for r in curves),default=None),
                'maximum_penetration_upper_m':max((r['curve_domain']['max_penetration_upper_m'] for r in curves),default=None),
                'phase_trigger_count':sum(r['phase_triggered'] for r in est),
                'initial_path_passes':sum(r['initial_paths_ok'] for r in est),
                'executed_successes':sum(bool((r['common_task'] or {}).get('complete_deployment_success')) for r in est)}
        analysis['stage_counts'][scope]=by_est
        if scope!='all': analysis['by_scene'][scope]=joint(selected)
        diffs=[]
        for scene,seed in sorted({(r['scene'],r['seed']) for r in selected}):
            pair={r['estimator']:r for r in selected if (r['scene'],r['seed'])==(scene,seed)}
            if not all((r['common_task'] or {}).get('complete_deployment_success') for r in pair.values()): continue
            c,d=[pair[k] for k in ('convex_hull','disk_union_closed')]
            diff={'scene':scene,'seed':seed}
            for k in ('simulation_time_s','actual_trajectory_length_m','recovery_count'):
                diff[k+'_disk_minus_convex']=d['common_task'][k]-c['common_task'][k]
                diff[k+'_convex']=c['common_task'][k];diff[k+'_disk']=d['common_task'][k]
            diff['total_task_wall_s_disk_minus_convex']=d['total_wall_s']-c['total_wall_s']
            for k in sorted(set(d['timing']) & set(c['timing'])):
                diff[k+'_disk_minus_convex']=d['timing'][k]-c['timing'][k]
            diffs.append(diff)
        analysis['paired_continuous'][scope]={'both_executed_success_pairs':len(diffs),
            'descriptive_statistics':{k:stats([p[k] for p in diffs]) for k in (diffs[0] if diffs else []) if k not in ('scene','seed')},
            'pairs':diffs,'inference':'descriptive paired results only; no independent-row CI, significance or population-100-percent claim'}
        curve_diffs=[]
        for scene,seed in sorted({(r['scene'],r['seed']) for r in selected}):
            pair={r['estimator']:r for r in selected if (r['scene'],r['seed'])==(scene,seed)}
            if all(r['boundary_description'] and r['boundary_description']['curve_valid'] for r in pair.values()):
                curve_diffs.append(pair['disk_union_closed']['boundary_description']['curve_length_m']-pair['convex_hull']['boundary_description']['curve_length_m'])
        analysis['paired_continuous'][scope]['both_valid_curve_length_difference_m']=stats(curve_diffs)
    write(out/'analysis.json',analysis)
    write(out/'verification.json',{'scientific_source_unchanged':True,'all_frozen_input_files_unchanged':True,
        'historical_protocol_and_smoke_files_unchanged':True,'historical_files_checked':len(historical),
        'unique_planned_task_count':len(rows),'unique_input_count':len(references),
        'actual_executed_rows':sum(r['common_task'] is not None for r in rows),
        'not_executed_rows':sum(r['termination_status']=='NOT_EXECUTED' for r in rows),
        'program_errors':sum(r['termination_status']=='EXPERIMENT_ERROR' for r in rows),
        'all_execution_commit':'3220233acf75dbd2a3a9cc28b8f91fab9f2e1255',
        'counterfactual_off_in_execution_metrics':True,'source_sha256':lock['provenance']['source_sha256'],
        'input_file_map_sha256':frozen['file_map_sha256'],'read_only_analysis_seconds':perf_counter()-started})
    write(out/'raw_checksums.json',{str(p):sha(p) for p in sorted(root.rglob('*')) if p.is_file()})
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['svg.hashsalt']='step1_boundary_estimator_comparison_v1'
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    for scene,color in zip(('elongated','tight_u','wide_u'),('C0','C1','C2')):
        pairs=analysis['paired_continuous'][scene]['pairs']
        for ax,key,label in zip(axes,('simulation_time_s','actual_trajectory_length_m'),('Simulation time (s)','Actual total Guide travel (m)')):
            ax.scatter([p[key+'_convex'] for p in pairs],[p[key+'_disk'] for p in pairs],s=24,color=color,label=f'{scene} (n={len(pairs)})',alpha=.75)
            ax.set_xlabel('Convex hull: '+label);ax.set_ylabel('Disk closing: '+label)
    for ax in axes:
        low=min(ax.get_xlim()[0],ax.get_ylim()[0]); high=max(ax.get_xlim()[1],ax.get_ylim()[1])
        ax.plot([low,high],[low,high],':',color='gray');ax.set_xlim(low,high);ax.set_ylim(low,high)
    axes[0].legend(fontsize=8);fig.suptitle('Paired successful executions only (50 units); descriptive, no ranking')
    svg=out/'paired_execution.svg'
    fig.savefig(svg,metadata={'Date':None});plt.close(fig)
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    print(json.dumps({'joint':{k:v for k,v in analysis['joint'].items() if k!='units'},
        'by_scene':{k:{n:v for n,v in d.items() if n!='units'} for k,d in analysis['by_scene'].items()}},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('runs/step1_boundary_estimator_protocol'))
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args();args.output.mkdir(exist_ok=True,parents=True)
    analyze(args.root,args.output)
