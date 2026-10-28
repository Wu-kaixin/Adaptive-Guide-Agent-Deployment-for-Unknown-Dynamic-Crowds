"""Fifty frozen reobservations, continuous method-specific actual robot states."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from datetime import datetime
import itertools
import json
from pathlib import Path
import shutil
import traceback

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import measure,BoundedMeasurement
from udc.multigroup.scenarios import make_truth,initial_guides
from udc.multigroup.geometry import construct_groups,ScientificFailure
from udc.multigroup.allocation import build_catalog,allocate
from udc.multigroup.phase_retention import phase_pool,choose_groups
from udc.multigroup.reference_support import build_support
from udc.multigroup.stable_update import METHODS,decide,actual_targets
from udc.multigroup.safe_routing import execute
from udc.multigroup.evaluation import evaluate,reference_groups
from udc.experiments.static_mechanism import continuous_summary
from udc.experiments.static_multigroup import (ROOT,digest,write_json,scientific_sources,environment_record,verify,
    _load_inputs,_blank_execution,_save_task)
from udc.experiments.worker_resources import initialize_worker
from udc.experiments.frozen_resume import atomic_json
from udc.experiments.episode_artifacts import EpisodeWriter

STOP={'EXPERIMENT_ERROR','SAFETY_VIOLATION','MODEL_BOUND_VIOLATION'}


def prepare(protocol,study):
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    base=yaml.safe_load((ROOT/design['base_protocol']).read_text(encoding='utf-8'))
    expected=20 if design['evidence']=='prospective_confirmation' else 3
    if design['seeds']['count']!=expected or design['steps']!=50 or design['methods']!=list(METHODS):raise ValueError('Frozen 50-step design required')
    if design['base_actual_error_m']+design['perturbation_amplitude_m']>design['declared_error_m']:raise ValueError('Error bound exceeded by generator')
    study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    units,tasks=[],[]
    for k,shape,seed in itertools.product(design['group_counts'],design['shapes'],range(design['seeds']['start'],design['seeds']['start']+expected)):
        truth=make_truth(k,shape,seed,design['namespace'],base['group_size_multipliers'],base['group_hotspot_multipliers'])
        shape_id=('circle','ellipse','concave','irregular').index(shape)
        obs=measure(truth,design['base_actual_error_m'],np.random.SeedSequence([design['namespace'],seed,k,shape_id]),'bounded_disk')
        name=f'K{k}_{shape}_s{seed}';folder=study/'inputs'/name;folder.mkdir(parents=True)
        np.savez_compressed(folder/'truth.npz',positions=truth.positions,radii=truth.radii,demand=truth.demand,
                            group_ids=truth.group_ids,guides=initial_guides(k*max(base['resources'][r] for r in design['resources']),base['room_m']))
        np.savez_compressed(folder/'observation.npz',positions=obs.observation.positions,radii=obs.observation.radii,demand=obs.observation.demand)
        unit={'unit_id':name,'group_count':k,'shape':shape,'seed':seed,'error_bound_m':design['declared_error_m'],'series':{}}
        for field in ('truth','observation'):
            path=folder/(field+'.npz');unit[field]=path.relative_to(study).as_posix();unit[field+'_sha256']=digest(path)
        radial=np.zeros_like(truth.positions)
        for label in np.unique(truth.group_ids):
            rows=truth.group_ids==label;vectors=truth.positions[rows]-truth.positions[rows].mean(axis=0)
            radial[rows]=vectors/np.maximum(np.linalg.norm(vectors,axis=1)[:,None],1e-12)*(-1 if label%2 else 1)
        for pattern_id,pattern in enumerate(design['patterns']):
            rng=np.random.default_rng(np.random.SeedSequence([design['namespace'],seed,k,shape_id,pattern_id,617]))
            positions=[obs.observation.positions.copy()]
            angle=rng.uniform(0,2*np.pi);direction=np.array([np.cos(angle),np.sin(angle)])
            for step in range(1,design['steps']+1):
                if pattern=='independent_jitter':
                    angles=rng.uniform(0,2*np.pi,len(truth.positions));magnitude=np.sqrt(rng.uniform(0,1,len(truth.positions)))
                    delta=design['perturbation_amplitude_m']*magnitude[:,None]*np.c_[np.cos(angles),np.sin(angles)]
                elif pattern=='slow_common_bias':delta=design['perturbation_amplitude_m']*step/design['steps']*direction
                elif pattern=='oscillatory_shape_bias':delta=design['perturbation_amplitude_m']*np.sin(2*np.pi*step/10)*radial
                else:raise ValueError('Unknown bounded observation sequence')
                positions.append(obs.observation.positions+delta)
            path=folder/(pattern+'.npz');np.savez_compressed(path,positions=np.asarray(positions))
            unit['series'][pattern]={'path':path.relative_to(study).as_posix(),'sha256':digest(path)}
        units.append(unit)
        for resource in design['resources']:
            tasks.append({'task_id':name+'_nominal_'+resource,'unit_id':name,'kind':'nominal','resource':resource})
            for pattern,method in itertools.product(design['patterns'],design['methods']):
                tasks.append({'task_id':f'{name}_{resource}_{pattern}_{method}','unit_id':name,'kind':'episode','resource':resource,'pattern':pattern,'method':method,'steps':50})
    work_items=units
    if design.get('split_pattern_workers',False):
        work_items=[u|{'unit_id':u['unit_id']+'__pattern_'+pattern,'physical_id':u['unit_id'],'only_pattern':pattern}
                    for u in units for pattern in design['patterns']]
        tasks=[]
        for unit in work_items:
            for resource in design['resources']:
                tasks.append({'task_id':unit['unit_id']+'_nominal_'+resource,'unit_id':unit['unit_id'],'physical_id':unit['physical_id'],'kind':'nominal','resource':resource})
                for method in design['methods']:
                    tasks.append({'task_id':f'{unit["unit_id"]}_{resource}_{unit["only_pattern"]}_{method}','unit_id':unit['unit_id'],'physical_id':unit['physical_id'],
                        'kind':'episode','resource':resource,'pattern':unit['only_pattern'],'method':method,'steps':50})
    sources=scientific_sources()
    for relative in sources:
        target=study/'source_snapshot'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,target)
    manifest={'protocol':design['protocol'],'evidence':design['evidence'],'design':design,'spec':base,'units':units,'tasks':tasks,
        'sources':sources,'environment':environment_record(),'protocol_sha256':digest(study/'protocol.yaml'),'work_items':work_items,
        'prepared_before_method_evaluation':True,'scheduled_step_records':sum(1 if t['kind']=='nominal' else 50 for t in tasks),
        'independent_physical_blocks':len(units),'checkpoint_boundary':'every 5 completed step records per episode'}
    write_json(study/'manifest.json',manifest);write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})
    return {'physical_blocks':len(units),'tasks':len(tasks),'step_records':manifest['scheduled_step_records'],'manifest_sha256':digest(study/'manifest.json')}


def _perform(guides,q,ids,groups,domain,obs,truth,reference,base,config,task,planning):
    result=_blank_execution(guides,'NOT_EXECUTED');metrics=dict(task)
    try:
        result=execute(guides,q,domain,base['room_m'],config,obs,'adaptive_reverse')
        metrics.update(evaluate(result,q,ids,groups,obs,truth,base['room_m'],config))
        metrics['continuous_actual_service']=continuous_summary(reference,truth,result.positions[-1][result.assignment>=0],config) if len(result.velocities) else None
        if not metrics['motion']['initial_safety_ok'] or metrics['motion']['interval_safety_ok'] is False:metrics['status']='SAFETY_VIOLATION'
        elif result.status!='ARRIVED':metrics['status']=result.status
        else:metrics['status']='FULL_SUCCESS' if metrics['full_task_success'] else 'PARTIAL_SERVICE'
        if metrics.get('deployment_completed') and len(q):
            active=result.assignment>=0
            tracking=float(np.linalg.norm(result.positions[-1][active]-q[result.assignment[active]],axis=1).max())
            metrics['tracking_error_m']=tracking
            if tracking>config['tracking_bound_m']:metrics.update(status='EXPERIMENT_ERROR',failure_message='Tracking contract violated')
        if planning.get('accepted_certificate'):
            baseline=continuous_summary(reference,truth,planning['baseline_targets'],config)
            metrics['continuous_baseline_planned_service']=baseline
            losses=[a['uncovered']-b['uncovered'] for a,b in zip(metrics['continuous_actual_service']['groups'],baseline['groups'])] if metrics.get('deployment_completed') else None
            metrics['actual_service_losses_against_own_baseline']=losses
            if losses is not None and max(losses)>config['true_service_delta']:metrics['status']='MODEL_BOUND_VIOLATION'
    except ScientificFailure as error:metrics.update(status=error.status,failure_message=str(error),failure_evidence=error.evidence)
    except Exception:metrics.update(status='EXPERIMENT_ERROR',traceback=traceback.format_exc())
    return result,metrics


def worker(argument):
    study,unit,manifest=argument;study=Path(study);base=manifest['spec'];design=manifest['design'];config=base['model']|design['model']
    truth,guides,nominal=_load_inputs(study,unit);rows=[];episode_summaries=[];stopped=False
    with threadpool_limits(limits=1):
        maximum=max(base['resources'][r] for r in design['resources'])*unit['group_count']
        initial_error=None;reference=();partition=()
        try:
            reference=reference_groups(truth,base['room_m'],config)
            groups,domain=construct_groups(nominal,base['room_m'],config);partition=tuple(g.rows for g in groups)
            catalogs=build_catalog(groups,nominal,domain,maximum,'projected_mass',config)
        except Exception as error:initial_error=error
        starts={}
        for resource in design['resources']:
            budget=base['resources'][resource]*unit['group_count'];q,ids=np.empty((0,2)),np.empty(0,int)
            task={'task_id':unit['unit_id']+'_nominal_'+resource,'kind':'nominal','unit_id':unit['unit_id'],'resource':resource}
            result=_blank_execution(guides[:budget],'NOT_EXECUTED');planning={}
            try:
                if initial_error is not None:raise initial_error
                allocation=allocate(catalogs,groups,budget,'minimax')
                pools=[phase_pool(g,nominal,domain,int(n),config) for g,n in zip(groups,allocation.counts)]
                q,ids,_,_=choose_groups(pools,'continuous_euclidean_greedy')
                planning={'counts':allocation.counts,'baseline_targets':q,'accepted_certificate':None}
                result,metrics=_perform(guides[:budget],q,ids,groups,domain,nominal,truth,reference,base,config,task,planning)
            except ScientificFailure as error:metrics=task|{'status':error.status,'failure_message':str(error),'failure_evidence':error.evidence,'deployment_completed':False}
            except Exception:metrics=task|{'status':'EXPERIMENT_ERROR','traceback':traceback.format_exc(),'deployment_completed':False}
            _save_task(study/'results'/task['task_id'],task,config,result,q,ids,planning,metrics);rows.append(metrics)
            starts[resource]=(result,ids,metrics,budget)
            stopped|=metrics['status'] in STOP
        for pattern in ([unit['only_pattern']] if 'only_pattern' in unit else design['patterns']):
            series=unit['series'][pattern]
            if digest(study/series['path'])!=series['sha256']:raise ValueError('Frozen observation sequence changed')
            with np.load(study/series['path'],allow_pickle=False) as data:observations=data['positions'].copy()
            writers,states,summaries={},{},{}
            for resource,method in itertools.product(design['resources'],design['methods']):
                initial,ids,metrics,budget=starts[resource];key=(resource,method)
                task={'task_id':f'{unit["unit_id"]}_{resource}_{pattern}_{method}','unit_id':unit['unit_id'],'group_count':unit['group_count'],
                      'shape':unit['shape'],'seed':unit['seed'],'kind':'episode','resource':resource,'method':method,'pattern':pattern,'budget':budget}
                writers[key]=EpisodeWriter(study/'results'/task['task_id'],task,config,budget,5)
                points,labels=actual_targets(initial,ids) if metrics.get('deployment_completed') else (np.empty((0,2)),np.empty(0,int))
                states[key]={'positions':initial.positions[-1].copy(),'points':points,'ids':labels,'alive':bool(metrics.get('deployment_completed')) and not stopped,
                             'elapsed_s':0.,
                             'robot_groups':np.where(initial.assignment>=0,ids[np.maximum(initial.assignment,0)] if len(ids) else -1,-1)}
                summaries[key]={'task_id':task['task_id'],'sequence_completed':True,'actual_total_travel_m':0.,'count_switch_events':0,
                    'counts_l1_total':0,'delivered_intergroup_transfers':0,'accepted_frames':0,'fallback_frames':0,'nontrivial_hold_frames':0,
                    'deployment_event_times_s':[],'event_worst_uncovered':[],'event_weighted_uncovered':[],'statuses':Counter()}
            for step in range(1,51):
                obs=BoundedMeasurement(Observation(observations[step],truth.radii,truth.demand),design['declared_error_m'],'frozen_bounded_reobservation')
                setup=None
                phase_cache={}
                try:
                    current_groups,current_domain=construct_groups(obs,base['room_m'],config)
                    if tuple(g.rows for g in current_groups)!=partition:raise ScientificFailure('PARTITION_CHANGED','Static group identity changed')
                    current_catalogs=build_catalog(current_groups,obs,current_domain,maximum,'projected_mass',config)
                    support=build_support(obs,current_groups,config)
                    from shapely.geometry import LineString
                    for g,region in zip(reference,support.regions):
                        if not LineString(np.vstack([g.boundary.deployment,g.boundary.deployment[0]])).difference(region).is_empty:
                            raise RuntimeError('MODEL_BOUND_VIOLATION: original reference outside support')
                except Exception as error:setup=error
                for key,writer in writers.items():
                    state,summary=states[key],summaries[key];resource,method=key;task=writer.task|{'step':step};q,ids=np.empty((0,2)),np.empty(0,int);planning={}
                    initial=state['positions'].copy();result=_blank_execution(initial,'NOT_EXECUTED')
                    if not state['alive'] or stopped:
                        metrics=task|{'status':'NOT_EXECUTED_PRIOR_DEPLOYMENT_FAILURE','deployment_completed':False}
                    else:
                        try:
                            if setup is not None:raise setup
                            q,ids,planning=decide(obs,current_groups,current_domain,current_catalogs,writer.guides,state['points'],state['ids'],config,support,method,phase_cache)
                            old_counts=np.bincount(state['ids'],minlength=len(current_groups));new_counts=np.bincount(ids,minlength=len(current_groups))
                            result,metrics=_perform(initial,q,ids,current_groups,current_domain,obs,truth,reference,base,config,task,planning)
                            metrics.update(decision=planning['decision'],counts=tuple(int(n) for n in new_counts),baseline_counts=planning['baseline_counts'],
                                counts_l1=int(np.abs(new_counts-old_counts).sum()),accepted_certificate=bool(planning['accepted_certificate']),
                                maximum_person_error_m=float(np.linalg.norm(obs.observation.positions-truth.positions,axis=1).max()))
                            if metrics['maximum_person_error_m']>design['declared_error_m']:metrics['status']='EXPERIMENT_ERROR'
                            if metrics.get('deployment_completed'):
                                robot_groups=np.full(writer.guides,-1,int);active=result.assignment>=0;robot_groups[active]=ids[result.assignment[active]]
                                transfers=int(np.count_nonzero((state['robot_groups']>=0)&(robot_groups>=0)&(state['robot_groups']!=robot_groups)))
                                metrics['delivered_intergroup_transfers']=transfers;summary['delivered_intergroup_transfers']+=transfers
                                summary['count_switch_events']+=metrics['counts_l1']>0;summary['counts_l1_total']+=metrics['counts_l1']
                                state['points'],state['ids']=actual_targets(result,ids);state['robot_groups']=robot_groups
                                summary['accepted_frames']+=metrics['accepted_certificate'];summary['fallback_frames']+=planning['decision']=='uncertified_fallback'
                                summary['nontrivial_hold_frames']+=planning['decision']=='hold_counts_and_phase' and not np.array_equal(q,planning['baseline_targets'])
                            state['positions']=result.positions[-1].copy()
                            state['alive']=bool(metrics.get('deployment_completed'))
                        except ScientificFailure as error:metrics=task|{'status':error.status,'failure_message':str(error),'failure_evidence':error.evidence,'deployment_completed':False};state['alive']=False
                        except Exception:
                            metrics=task|{'status':'MODEL_BOUND_VIOLATION' if setup is not None and 'MODEL_BOUND_VIOLATION' in str(setup) else 'EXPERIMENT_ERROR',
                                'traceback':traceback.format_exc(),'deployment_completed':False};state['alive']=False
                    summary['statuses'][metrics['status']]+=1
                    summary['sequence_completed']&=bool(metrics.get('deployment_completed'))
                    summary['actual_total_travel_m']+=metrics.get('motion',{}).get('travel_m') or 0.
                    state['elapsed_s']+=metrics.get('motion',{}).get('simulation_time_s') or 0.
                    metrics['elapsed_deployment_event_time_s']=state['elapsed_s'] if 'motion' in metrics else None
                    summary['deployment_event_times_s'].append(metrics['elapsed_deployment_event_time_s'])
                    actual_service=metrics.get('continuous_actual_service') if metrics.get('deployment_completed') else None
                    summary['event_worst_uncovered'].append(actual_service['worst_group_uncovered'] if actual_service else None)
                    summary['event_weighted_uncovered'].append(actual_service['weighted_uncovered'] if actual_service else None)
                    stopped|=metrics['status'] in STOP
                    saved_state={'step':step,'execution_status':result.status,'paths':result.paths,'evidence':result.evidence,'planning':planning}
                    writer.append(metrics,result,q,ids,saved_state)
                if step%5==0:
                    durable=sum(w.total for w in writers.values())
                    atomic_json(study/'unit_progress'/ (unit['unit_id']+'.json'),{'pattern':pattern,'step':step,'durable_pattern_records':durable,'stopped':stopped})
            for key,writer in writers.items():
                summary=summaries[key];summary['statuses']=dict(summary['statuses']);writer.close(summary);episode_summaries.append(summary)
    unit_summary={'unit_id':unit['unit_id'],'stopped':stopped,'nominal_statuses':dict(Counter(r['status'] for r in rows)),
                  'episodes':episode_summaries,'recorded_step_records':len(rows)+sum(w['statuses'].get(s,0) for w in episode_summaries for s in w['statuses'])}
    write_json(study/'units'/(unit['unit_id']+'.json'),unit_summary)
    return unit_summary


def run(study,workers):
    manifest=verify(study)
    if not 1<=workers<=32:raise ValueError('Worker budget exceeded')
    if any((study/name).exists() for name in ('results','summary.json','status.json')):raise FileExistsError('No automatic retry')
    (study/'unit_progress').mkdir()
    (study/'orchestration_errors').mkdir()
    work_items=manifest.get('work_items',manifest['units'])
    pending,results,stopped={},[],False;iterator=iter(work_items)
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize_worker) as pool:
        def submit():
            unit=next(iterator,None)
            if unit:pending[pool.submit(worker,(str(study),unit,manifest))]=unit['unit_id']
        for _ in range(workers):submit()
        while pending:
            done,_=wait(pending,timeout=10,return_when=FIRST_COMPLETED)
            for future in done:
                name=pending.pop(future)
                try:
                    fresh=future.result();results.append(fresh);stopped|=fresh['stopped']
                except Exception:
                    diagnostic={'unit':name,'traceback':traceback.format_exc(),'scientific_retry':False}
                    atomic_json(study/'orchestration_errors'/(name+'.json'),diagnostic)
                    atomic_json(study/'orchestration_error.json',diagnostic);stopped=True
            if not stopped:
                for _ in done:submit()
            atomic_json(study/'status.json',{'updated_at':datetime.now().astimezone().isoformat(),'completed_units':len(results),
                'scheduled_units':len(work_items),'in_flight':len(pending),'stopped':stopped,
                'recorded_step_records_in_complete_units':sum(r['recorded_step_records'] for r in results)})
    durable_rows=[]
    for receipt in (study/'results').rglob('receipt.json') if (study/'results').exists() else ():
        hashes=json.loads(receipt.read_text(encoding='utf-8'))
        if any(digest(receipt.parent/name)!=sha for name,sha in hashes.items()):raise ValueError('Durable artifact changed')
        saved=json.loads((receipt.parent/'metrics.json').read_text(encoding='utf-8'))
        durable_rows.extend(saved if isinstance(saved,list) else [saved])
    summary={'stopped':stopped,'completed_units':len(results),'scheduled_units':len(work_items),
             'recorded_step_records':len(durable_rows),'scheduled_step_records':manifest['scheduled_step_records'],
             'not_recorded_steps':manifest['scheduled_step_records']-len(durable_rows),
             'statuses':dict(Counter(r['status'] for r in durable_rows)),
             'orchestration_error_units':len(list((study/'orchestration_errors').glob('*.json'))),
             'sequence_completed':sum(e['sequence_completed'] for r in results for e in r['episodes']),
             'episodes_recorded':sum(len(r['episodes']) for r in results),'independent_physical_blocks':len(manifest['units'])}
    write_json(study/'summary.json',summary);return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run']);p.add_argument('--protocol');p.add_argument('--study',required=True);p.add_argument('--workers',type=int,default=32)
    args=p.parse_args();study=Path(args.study).resolve()
    print(json.dumps(prepare(args.protocol,study) if args.action=='prepare' else run(study,args.workers),ensure_ascii=False,indent=2),flush=True)
