"""Paired, fixed-resource boundary construction protocol (two estimators only).

Preparation creates a 16-row development manifest and a 120-row future schedule,
without drawing future inputs. Future freezing and execution are separate,
explicit CLI operations. Shared execution/control/evaluation remain unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import re
import traceback
from time import perf_counter

import numpy as np
from shapely.geometry import Point, Polygon
import yaml
from threadpoolctl import threadpool_limits

from udc.boundary import construct_boundary
from udc.deployment.admissible import admissible_phase
from udc.deployment.planners import (plan_coverage, periodic_gaps, feasibility_bounds,
    infeasibility_certificate, quadrature_mass, coverage_statistics)
from udc.evaluation.comparison import common_motion_metrics
from udc.experiments.nonconvex_rebuild import (write, sha, array_hash, load_protocol, draw_cell,
    rejected_result, _official_monitor)
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, reevaluate_result, _atomic_npz, _atomic_text, STATUSES
from udc.experiments.scene_closure import _people_report, _execution_config
from udc.experiments.target_domain import curve_domain_relation, save_plan, independent_geometry
from udc.navigation import navigation_domain, navigate_and_assign
from udc.navigation.paths import target_domain_check, TargetDomainFailure
from udc.safety import Safety


SPEC = 'configs/step1/boundary_estimator_comparison_v1.yaml'
OUTPUT = 'runs/step1_boundary_estimator_protocol'
ESTIMATORS = ['convex_hull','disk_union_closed']
SMOKE = [['elongated',36],['tight_u',36],['wide_u',36],['elongated',51],
         ['wide_u',35],['elongated',35],['tight_u',35],['tight_u',41]]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_spec(path=SPEC):
    spec = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if (spec['protocol']!='step1_boundary_estimator_comparison_v1' or spec['estimators']!=ESTIMATORS
            or spec['smoke_units']!=SMOKE or spec['scenes']!=['elongated','tight_u','wide_u']
            or spec['guide_counts']!={'elongated':11,'tight_u':13,'wide_u':19}
            or spec['future']['seeds']!=list(range(55,75))
            or spec['smoke_execute_units']!=[['elongated',51],['wide_u',35]]
            or spec['smoke_max_executions']!=4):
        raise ValueError('Changed factors require a new comparison protocol revision')
    expected_boundary={'offset':1.3,'spacing':.1,'wall_margin':.55,'crowd_distance':.75,'closing_radius':.3,'quad_segs':16}
    expected_deployment={'method':'equal_arc','density':'constant_curve_density','phases':[0.,.25,.5,.75],
        'quadrature_samples':2048,'max_iterations':300,'planning_gap_m':1.94,'minimum_separation_m':.75,
        'phase_postprocessing':'static_domain_phase_v1','phase_bins':256,'trigger':'original_static_targets_invalid_only'}
    if spec['boundary']!=expected_boundary or spec['deployment']!=expected_deployment:
        raise ValueError('Changed geometry or deployment policy requires a new protocol revision')
    generator,base=load_protocol(spec['input_generator_spec'])
    if spec['deployment']['phases']!=base['legacy_phases']:
        raise ValueError('Equal Arc phase order changed')
    frozen_control={'offset':1.3,'sample_spacing':.1,'max_gap_limit':2.,'arc_tolerance':.03,
        'position_tolerance':.03,'speed_tolerance':.03,'curve_tolerance':.04,
        'safety_numeric_tolerance':1e-7,'max_replans':3,'stall_window':20,'hold_steps':10,'clearance':.35}
    if (any(base['execution_controller'][k]!=v for k,v in frozen_control.items())
            or base['guides']['max_speed']!=1. or base['simulation']!={'dt':.1,'max_steps':400}):
        raise ValueError('Frozen control/safety/arrival/clock contract changed')
    return spec,generator,base


def audit_future_seeds():
    """Bounded repository audit of seed metadata and existing run path names."""
    reserved=set(range(55,75)); hits=[]; inspected=[]
    files=list(Path('configs').rglob('*.yaml'))+list(Path('experiments').rglob('*.yaml'))
    files+=list(Path('runs').rglob('*manifest*.json'))+list(Path('reports').rglob('*.json'))
    def visit(value,path):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {'seed','seeds','development_seeds','validation_seeds'}:
                    numbers=item if isinstance(item,list) else [item]
                    used=sorted(n for n in numbers if type(n) is int and n in reserved)
                    if used: hits.append({'path':str(path),'key':key,'seeds':used})
                visit(item,path)
        elif isinstance(value,list):
            for item in value: visit(item,path)
    for path in files:
        if (path.as_posix()==SPEC or 'step1_boundary_estimator_protocol' in path.parts):
            continue  # This proposal is a reservation, not evidence of prior use.
        inspected.append(str(path))
        value=yaml.safe_load(path.read_text(encoding='utf-8')) if path.suffix=='.yaml' else read(path)
        visit(value,path)
    pattern=re.compile(r'(?:^|[/\\])seed[_-](5[5-9]|6[0-9]|7[0-4])(?:[/\\.]|$)')
    for path in Path('runs').rglob('*'):
        if pattern.search(str(path)) and 'step1_boundary_estimator_protocol' not in path.parts:
            hits.append({'path':str(path),'key':'existing_run_path'})
    return {'seeds':sorted(reserved),'available_in_scoped_audit':not hits,'hits':hits,
            'scope':'configs/experiments seed metadata; runs manifests and seed paths; reports JSON seed metadata',
            'inspected_metadata_files':inspected,'must_audit_again_at_future_freeze':True}


def input_quality(data):
    people,radii,room=data['people'],data['radii'],data['room']
    finite=bool(np.isfinite(people).all() and np.isfinite(radii).all() and np.isfinite(room).all()
                and np.all(radii>=0) and np.all(room>0))
    distance=np.linalg.norm(people[:,None]-people[None,:],axis=2)
    np.fill_diagonal(distance,np.inf)
    thresholds=radii[:,None]+radii[None,:]
    separated=bool(np.all(distance>=thresholds))
    inside=bool(np.all(people>=radii[:,None]) and np.all(people<=room-radii[:,None]))
    return {'accepted':finite and separated and inside,'finite':finite,'people_disks_inside_room':inside,
            'nonoverlapping_person_disks':separated,'minimum_person_center_distance_m':float(distance.min())}


def input_reference(cell,config_path=None):
    cell=Path(cell).resolve()
    with np.load(cell/'input.npz',allow_pickle=False) as data:
        digest=array_hash(**{key:data[key] for key in ('people','radii','demand','guides','room')})
        quality=input_quality(data)
    record=read(cell/'input_record.json') if (cell/'input_record.json').exists() else None
    if record is not None and (digest!=record['input_sha256'] or sha(cell/'input.npz')!=record['input_file_sha256']):
        raise RuntimeError('Saved input does not match its frozen record')
    return {'cell':str(cell),'input_file_sha256':sha(cell/'input.npz'),'input_sha256':digest,
            'quality':quality,'input_config':str(config_path or cell/'input.yaml')}


def schedule(spec,units):
    return [{'task_id':f'{scene}__seed_{seed}__{estimator}', 'scene':scene,'seed':seed,
             'estimator':estimator,'method':'equal_arc+static_domain_phase_v1',
             'count':spec['guide_counts'][scene]}
            for scene,seed in units for estimator in spec['estimators']]


def prepare(output,spec_path):
    spec,generator,base=load_spec(spec_path)
    root=Path(output).resolve()
    if root.exists(): raise FileExistsError('Preparation requires a new output directory')
    audit=audit_future_seeds()
    if not audit['available_in_scoped_audit']:
        raise RuntimeError('Future seeds have existing usage; do not silently replace them')
    root.mkdir(parents=True)
    references={}
    for scene,seed in spec['smoke_units']:
        cell=Path(spec['smoke_input_root'])/'inputs'/scene/f'seed_{seed}'
        # No generator call here. Reuse the previous reconstruction snapshot.
        reference=input_reference(cell)
        cfg=yaml.safe_load(Path(reference['input_config']).read_text(encoding='utf-8'))
        cfg['controller']['method']='equal_arc'
        target=root/'input_refs'/scene/f'seed_{seed}'/'input.yaml'
        _atomic_text(target,yaml.safe_dump(cfg,sort_keys=False))
        reference.update(input_config=str(target),config_sha256=sha(target),
            origin=spec['smoke_input_origin'],source_config_sha256=sha(cell/'input.yaml'),
            people_sqlite_sha256=sha(cfg['crowd']['trajectory_file']))
        references[f'{scene}__seed_{seed}']=reference
    lock={'spec':spec,'generator':generator,'base':base,'spec_sha256':sha(spec_path),
          'base_spec_sha256':sha(generator['base_spec']), 'generator_spec_sha256':sha(spec['input_generator_spec']),
          'provenance':provenance({},None,'fixed_n',None), 'references':references}
    write(root/'protocol.json',lock)
    write(root/'seed_audit.json',audit)
    write(root/'smoke_manifest.json',{'tasks':schedule(spec,spec['smoke_units']),'task_count':16,
          'input_units':8,'maximum_full_executions':4,'references':references})
    future_units=[[scene,seed] for scene in spec['scenes'] for seed in spec['future']['seeds']]
    write(root/'future_manifest_120.json',{'tasks':schedule(spec,future_units),'task_count':120,
        'input_units':60,'inputs_generated':False,'source_sha256_lock':lock['provenance']['source_sha256'],
        'spec_sha256_lock':lock['spec_sha256'],'preparation_only':True})
    print(json.dumps({'smoke_rows':16,'future_schedule_rows':120,'future_inputs_generated':False}),flush=True)


def equal_arc_target(boundary,count,policy):
    """Original Equal Arc phase order; admission is a separate explicit policy."""
    attempts=[]
    for phase in policy['phases']:
        plan=plan_coverage(boundary,None,None,count,None,'equal_arc',phase,
            policy['quadrature_samples'],policy['max_iterations'],density_override=np.ones(len(boundary.deployment)))
        distances=np.linalg.norm(plan.positions[:,None]-plan.positions[None,:],axis=2)
        np.fill_diagonal(distances,np.inf)
        gaps=periodic_gaps(plan.arc_positions,boundary.length)
        accepted=bool(gaps.max()<=policy['planning_gap_m']+1e-9 and distances.min()>=policy['minimum_separation_m'])
        attempts.append({'legacy_phase':phase,'accepted_geometry':accepted,'min_pair_m':float(distances.min()),
                         'min_arc_gap_m':float(gaps.min()),'max_arc_gap_m':float(gaps.max())})
        if accepted: return plan,attempts
    return None,attempts


def _locked(root):
    lock=read(root/'protocol.json')
    if (sha(SPEC)!=lock['spec_sha256'] or sha(lock['spec']['input_generator_spec'])!=lock['generator_spec_sha256']
            or sha(lock['generator']['base_spec'])!=lock['base_spec_sha256']
            or provenance({},None,'fixed_n',None)['source_sha256']!=lock['provenance']['source_sha256']):
        raise RuntimeError('Frozen source/config changed; create a new protocol attempt')
    return lock


def _reject(destination,row,reference,status,reason):
    result=rejected_result(destination,{**row,'input_sha256':reference['input_sha256']},
        Path(reference['cell']),status,reason)
    _atomic_text(destination/'config.yaml',Path(reference['input_config']).read_text(encoding='utf-8'))
    return result


def run_row(arguments):
    root_string,row,phase,execute=arguments
    root=Path(root_string); lock=_locked(root); spec=lock['spec']
    key=f"{row['scene']}__seed_{row['seed']}"
    references=lock['references'] if phase=='smoke' else read(root/'future_inputs_frozen.json')['references']
    ref=references[key]; cell=Path(ref['cell'])
    destination=root/phase/'results'/row['task_id']; artifacts=root/phase/'plans'/row['task_id']
    if destination.exists() or artifacts.exists(): raise FileExistsError('Never overwrite a protocol row')
    artifacts.mkdir(parents=True)
    started=perf_counter(); timing={}; detail={**row,'input_sha256':ref['input_sha256'],
        'input_origin':ref['origin'],'common_input_legal':ref['quality']['accepted'],
        'execution_authorized_for_row':bool(execute),'common_task':None,'curve_internal':None,
        'boundary_description':None,'phase_triggered':False,'phase_selected_m':None,
        'initial_paths_ok':False,'target_geometry_ok':False,'static_targets_ok':False,
        'stage':'initialization','source_sha256':lock['provenance']['source_sha256']}
    try:
        if sha(cell/'input.npz')!=ref['input_file_sha256'] or sha(ref['input_config'])!=ref['config_sha256']:
            raise RuntimeError('Paired input or shared config changed')
        cfg=yaml.safe_load(Path(ref['input_config']).read_text(encoding='utf-8'))
        if sha(cfg['crowd']['trajectory_file'])!=ref['people_sqlite_sha256']:
            raise RuntimeError('Referenced people database changed')
        with np.load(cell/'input.npz',allow_pickle=False) as snapshot:
            data={key:snapshot[key].copy() for key in snapshot.files}
        if not input_quality(data)['accepted']:
            raise ValueError('INITIALIZATION_INVALID: shared people or room constraints invalid; no estimator called')
        detail['stage']='boundary'
        boundary,description,occupied,reference=construct_boundary(row['estimator'],data['people'],data['radii'],data['room'],**spec['boundary'])
        detail['boundary_description']=description
        timing.update(boundary_construction_s=description['construction_seconds'],boundary_descriptors_s=description['description_seconds'])
        (artifacts/'occupied.wkb').write_bytes(occupied.wkb)
        (artifacts/'physical_reference_disks.wkb').write_bytes(reference.wkb)
        if boundary is None: raise ValueError(description['failure_reason'])
        _atomic_npz(artifacts/'boundary.npz',crowd=boundary.crowd,deployment=boundary.deployment,arc=boundary.arc,length=np.array(boundary.length))
        detail['stage']='resource'
        bounds=feasibility_bounds(boundary.length,1.94,.75)
        certificate=infeasibility_certificate(bounds,row['count'])
        detail['resource']={**bounds,'fixed_count':row['count'],'necessary_condition_met':certificate is None,'count_certificate':certificate}
        domain=navigation_domain(data['room'],.55,Polygon(boundary.crowd),.55)
        detail['domain_sha256']=domain.geometry_sha256
        t=perf_counter(); detail['curve_domain']=curve_domain_relation(boundary,domain)
        timing['curve_domain_diagnostic_s']=perf_counter()-t
        if certificate:
            raise ValueError('CAPACITY_SHORTFALL: fixed count violates '+certificate+'; no resource adjustment')
        detail['stage']='target_geometry'; t=perf_counter()
        original,attempts=equal_arc_target(boundary,row['count'],spec['deployment'])
        timing['equal_arc_planning_s']=perf_counter()-t; detail['equal_arc_attempts']=attempts
        if original is None:
            raise ValueError('PLAN_SEARCH_EXHAUSTED: original Equal Arc phases found no geometric target set; infeasibility not proven')
        detail['target_geometry_ok']=True
        save_plan(artifacts/'original_targets',original,{'attempts':attempts})
        detail['stage']='static_target_domain'
        original_admission=target_domain_check(domain,original.positions,original.arc_positions)
        detail['original_target_domain']=original_admission
        detail['phase_triggered']=not original_admission['accepted']; t=perf_counter()
        candidate,phase_report=admissible_phase(boundary,original,domain,np.ones(len(boundary.deployment)),
            phase_bins=256,gap_limit=1.94,separation=.75,quadrature_samples=2048)
        timing['phase_policy_s']=perf_counter()-t
        write(artifacts/'phase_policy.json',phase_report)
        if candidate is None:
            raise ValueError('PLAN_SEARCH_EXHAUSTED: bounded static-domain phase grid found no witness; infeasibility not proven')
        save_plan(artifacts/'final_targets',candidate,phase_report)
        check=independent_geometry(boundary,original,candidate,domain,lock['base'])
        if not check['accepted']: raise RuntimeError('Independent target geometry check rejected the candidate')
        detail['static_targets_ok']=True; detail['target_geometry']=check
        detail['phase_selected_m']=phase_report['selected_phase_m']
        q,mass,length=quadrature_mass(boundary,np.ones(len(boundary.deployment)),2048)
        detail['curve_internal']={
            'scope':'within_this_original_curve_only_not_a_cross_estimator_ranking',
            'curve_length_m':length,'arc_positions_m':candidate.arc_positions.tolist(),
            'max_gap_m':candidate.max_gap,'min_gap_m':float(periodic_gaps(candidate.arc_positions,length).min()),
            'constant_density_quadrature_cost':coverage_statistics(q,mass,candidate.arc_positions,length,np.inf)['weighted_cost'],
            'selected_phase_m':phase_report['selected_phase_m']}
        detail['stage']='initial_navigation'; t=perf_counter()
        nav=navigate_and_assign(data['guides'],candidate.positions,data['room'],.55,Polygon(boundary.crowd),.55)
        timing['preflight_navigation_s']=perf_counter()-t
        _atomic_npz(artifacts/'assignment.npz',assignment=nav.assignment,lengths=nav.lengths,reachable=nav.reachable)
        write(artifacts/'initial_paths.json',{'paths':[p.tolist() for p in nav.paths], 'domain_sha256':domain.geometry_sha256})
        detail['initial_paths_ok']=True; detail['stage']='execution' if execute else 'planning_only'; t=perf_counter()
        result=experiment(ref['input_config'],destination,plots=False,protocol='fixed_n',fixed_n=row['count'],
            prescribed_boundary=boundary,prescribed_plan=candidate,method_label='equal_arc+static_domain_phase_v1',
            boundary_source=description['boundary_source'],execute=execute)
        timing['runner_wall_s']=perf_counter()-t
        with np.load(destination/'trajectory.npz',allow_pickle=False) as actual:
            paired=all(np.array_equal(actual[a],data[b]) for a,b in
                [('crowd_positions','people'),('crowd_radii','radii'),('crowd_demand','demand'),('room','room')])
            paired=paired and np.array_equal(actual['positions'][0],data['guides']) and np.array_equal(actual['targets'],candidate.positions)
            if not paired: raise RuntimeError('Runtime physical input or target differs from frozen snapshot')
            detail['exact_shared_input_and_target_pairing']=True
            motion=common_motion_metrics(actual['positions'],actual['safe_velocity'],data['people'],data['radii'],data['room'],
                actual['targets'],actual['assignment'],cfg['controller'],Safety(**result['safety_limits']),cfg['simulation']['dt'])
        detail['common_task']=None if motion is None else {**motion,'complete_deployment_success':bool(result['success']),
            'recovery_count':result['replan_count']}
    except Exception as exc:
        status=str(exc).split(':')[0] if isinstance(exc,ValueError) else None
        if status in STATUSES:
            result=_reject(destination,row,ref,status,str(exc))
            detail['failure_evidence']=getattr(exc,'diagnosis',None)
        # Errors stay distinct and retain the same required artifact schema.
        else:
            if not destination.exists():
                result=_reject(destination,row,ref,'EXPERIMENT_ERROR','EXPERIMENT_ERROR: see runner_error.txt')
            else:
                result=read(destination/'metrics.json') if (destination/'metrics.json').exists() else {}
                result.update(termination_status='EXPERIMENT_ERROR',success=False)
                write(destination/'metrics.json',result)
                write(destination/'state.json',{'status':'error','success':False})
            _atomic_text(destination/'runner_error.txt',traceback.format_exc())
    t=perf_counter(); offline=reevaluate_result(destination); timing['offline_reevaluation_s']=perf_counter()-t
    detail.update(termination_status=result['termination_status'],offline_consistent=bool(offline['criteria_consistent']),
        failure_reason=result.get('failure_reason'),timing=timing,total_wall_s=perf_counter()-started)
    write(destination/'protocol_metrics.json',detail)
    return detail


def summarize(rows,expected):
    legal_units={f"{r['scene']}__seed_{r['seed']}" for r in expected if r['common_input_legal']}
    estimators={}
    for name in ESTIMATORS:
        selected=[r for r in rows if r['estimator']==name and r['common_input_legal']]
        successes=sum(bool((r['common_task'] or {}).get('complete_deployment_success')) for r in selected)
        unresolved=sum(r['termination_status']=='NOT_EXECUTED' for r in selected)
        estimators[name]={'primary_denominator':len(legal_units),'observed_complete_successes':successes,
            'unexecuted_unresolved':unresolved,'observed_stage_or_execution_failures':len(selected)-successes-unresolved,
            'complete_deployment_success_fraction':None if unresolved or not legal_units else successes/len(legal_units),
            'preflight_path_passes':sum(r['initial_paths_ok'] for r in selected),
            'outcomes_in_primary_denominator':dict(Counter(r['termination_status'] for r in selected))}
    paired=[]
    for unit in sorted(legal_units):
        pair=[r for r in rows if f"{r['scene']}__seed_{r['seed']}"==unit]
        if len(pair)==2 and all((r['common_task'] or {}).get('complete_deployment_success') for r in pair):
            by={r['estimator']:r['common_task'] for r in pair}
            paired.append({'unit':unit,'convex_hull':by['convex_hull'],'disk_union_closed':by['disk_union_closed'],
                'disk_minus_convex_time_s':by['disk_union_closed']['simulation_time_s']-by['convex_hull']['simulation_time_s'],
                'disk_minus_convex_travel_m':by['disk_union_closed']['actual_trajectory_length_m']-by['convex_hull']['actual_trajectory_length_m']})
    return {'planned_rows':len(expected),'completed_rows':len(rows),'planned_input_units':len(expected)//2,
        'people_legal_common_units':len(legal_units),'people_invalid_common_units':len(expected)//2-len(legal_units),
        'estimators':estimators,'both_successful_executed_pairs':paired,'paired_continuous_unit_count':len(paired),
        'executed_rows':sum(r['common_task'] is not None for r in rows),'offline_inconsistent':sum(not r['offline_consistent'] for r in rows),
        'phase_trigger_rows':[r['task_id'] for r in rows if r['phase_triggered']],
        'outcomes_all_planned_rows':dict(Counter(r['termination_status'] for r in rows)),
        'no_formal_ranking':True,'rows':rows}


def run(output,phase,workers):
    if not 1<=workers<=6: raise ValueError('Worker count must be one to six')
    root=Path(output).resolve(); lock=_locked(root)
    if (root/phase).exists(): raise FileExistsError('Never overwrite a completed or partial attempt')
    manifest=read(root/('smoke_manifest.json' if phase=='smoke' else 'future_manifest_120.json'))
    refs=lock['references'] if phase=='smoke' else read(root/'future_inputs_frozen.json')['references']
    expected=[{**row,'common_input_legal':refs[f"{row['scene']}__seed_{row['seed']}"]['quality']['accepted']} for row in manifest['tasks']]
    _official_monitor()
    for flag in ('UDC_COMPLETE_SEGMENT','UDC_VERTEX_ARRIVAL'):
        if os.environ.get(flag,'1')!='1': raise RuntimeError('Frozen execution switches must remain enabled')
    os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    tasks=[(str(root),row,phase,phase=='future' or [row['scene'],row['seed']] in lock['spec']['smoke_execute_units']) for row in expected]
    write(root/(phase+'_freeze.json'),{'workers':workers,'numerical_threads_per_worker':1,
        'source_sha256':lock['provenance']['source_sha256'],'spec_sha256':lock['spec_sha256'],'tasks':expected})
    started=perf_counter()
    if workers==1:
        rows=[]
        with threadpool_limits(limits=1):
            for task in tasks:
                result=run_row(task); rows.append(result)
                print(json.dumps({'task':result['task_id'],'status':result['termination_status']}),flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            rows=list(pool.map(_worker,tasks))
    summary=summarize(rows,expected); summary['wall_s']=perf_counter()-started
    write(root/(phase+'_summary.json'),summary)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('rows','both_successful_executed_pairs')}),flush=True)


def _worker(task):
    with threadpool_limits(limits=1): return run_row(task)


def freeze_future(output):
    """Explicit later operation; not called by preparation or smoke execution."""
    root=Path(output).resolve(); lock=_locked(root); spec=lock['spec']; audit=audit_future_seeds()
    if not audit['available_in_scoped_audit']: raise RuntimeError('Future seed reservation no longer available')
    if (root/'future_inputs').exists(): raise FileExistsError('Future inputs already frozen; never redraw')
    rows=read(root/'future_manifest_120.json')['tasks']; refs={}
    for scene_name in spec['scenes']:
        scene=next(s for s in lock['base']['scenes'] if s['name']==scene_name)
        for seed in spec['future']['seeds']:
            data,streams=draw_cell(lock['generator'],lock['base'],scene,seed)
            cell=root/'future_inputs'/scene_name/f'seed_{seed}'; cell.mkdir(parents=True)
            _atomic_npz(cell/'input.npz',**data)
            cfg=_execution_config(cell,data['people'],np.array([[0,0],[30,0],[30,30],[0,30]]),spec['guide_counts'][scene_name],seed,lock['base'])
            config=yaml.safe_load(cfg.read_text(encoding='utf-8')); config['controller']['method']='equal_arc'
            config['guides']['initial_positions']=data['guides'].tolist(); _atomic_text(cfg,yaml.safe_dump(config,sort_keys=False))
            ref=input_reference(cell,cfg)
            ref.update(origin='new_future_confirmation_one_draw_frozen_before_results',config_sha256=sha(cfg),
                people_sqlite_sha256=sha(config['crowd']['trajectory_file']))
            refs[f'{scene_name}__seed_{seed}']=ref
            write(cell/'streams.json',streams)
    write(root/'future_inputs_frozen.json',{'references':refs,'input_units':60,'task_rows':len(rows),
        'source_sha256':lock['provenance']['source_sha256'],'spec_sha256':lock['spec_sha256'],'seed_audit':audit})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec',default=SPEC); parser.add_argument('--output',default=OUTPUT)
    parser.add_argument('--workers',type=int,default=1)
    actions=parser.add_mutually_exclusive_group(required=True)
    for name in ('prepare','run-smoke','freeze-future','run-future'): actions.add_argument('--'+name,action='store_true')
    args=parser.parse_args()
    if args.prepare: prepare(args.output,args.spec)
    elif args.run_smoke: run(args.output,'smoke',args.workers)
    elif args.freeze_future: freeze_future(args.output)
    else: run(args.output,'future',args.workers)


if __name__=='__main__': main()
