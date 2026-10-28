"""Reconstruct observation-only decisions and every saved consecutive interval."""
from collections import Counter,defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

import numpy as np
from shapely.geometry import LineString
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.geometry import construct_groups,ScientificFailure
from udc.multigroup.allocation import build_catalog,allocate
from udc.multigroup.phase_retention import phase_pool,choose_groups
from udc.multigroup.reference_support import build_support
from udc.multigroup.stable_update import decide,actual_targets
from udc.multigroup.execution import Execution
from udc.multigroup.evaluation import evaluate,reference_groups
from udc.experiments.static_mechanism import continuous_summary
from udc.experiments.static_multigroup import verify,digest,write_json,_load_inputs
from udc.experiments.worker_resources import initialize_worker


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def sequential_total(values):
    """Mirror the runner's repeated += exactly, including Python 3.12."""
    total=0.
    for value in values:total+=value
    return total


def normalize(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,np.generic):return value.item()
    if isinstance(value,(tuple,list)):return [normalize(v) for v in value]
    if isinstance(value,dict):return {k:normalize(v) for k,v in value.items()}
    return value


def load_task(folder):
    receipt=read(folder/'receipt.json')
    if any(digest(folder/name)!=sha for name,sha in receipt.items()):raise ValueError('Saved nominal artifacts changed')
    state=read(folder/'state.json')
    with np.load(folder/'trajectory.npz',allow_pickle=False) as data:
        result=Execution(data['positions'].copy(),data['velocities'].copy(),data['assignment'].copy(),state['execution_status'],(),state['evidence'])
        q,ids=data['targets'].copy(),data['target_groups'].copy()
    return result,q,ids,read(folder/'metrics.json')


def load_episode(folder):
    completion=read(folder/'completion.json')
    if digest(folder/'config.json')!=completion['config_sha256']:raise ValueError('Episode config changed')
    rows=[]
    for relative,sha in sorted(completion['chunk_receipt_sha256'].items()):
        receipt_path=folder/relative
        if digest(receipt_path)!=sha:raise ValueError('Episode receipt changed')
        chunk=receipt_path.parent;receipt=read(receipt_path)
        if any(digest(chunk/name)!=checksum for name,checksum in receipt.items()):raise ValueError('Episode chunk changed')
        metrics,states=read(chunk/'metrics.json'),read(chunk/'state.json')
        with np.load(chunk/'trajectory.npz',allow_pickle=False) as data:
            for i,(m,state) in enumerate(zip(metrics,states)):
                a,b=data['position_offsets'][i:i+2];c,d=data['velocity_offsets'][i:i+2];n=int(data['target_counts'][i])
                execution=Execution(data['positions'][a:b].copy(),data['velocities'][c:d].copy(),data['assignment'][i].copy(),state['execution_status'],(),state['evidence'])
                rows.append((m,state,execution,data['targets'][i,:n].copy(),data['target_groups'][i,:n].copy()))
    if len(rows)!=50:raise ValueError('Incomplete 50-frame episode')
    summary=completion['summary']
    recomputed={'sequence_completed':all(r[0].get('deployment_completed',False) for r in rows),
        'actual_total_travel_m':sequential_total((r[0].get('motion',{}).get('travel_m') or 0.) for r in rows),
        'statuses':dict(Counter(r[0]['status'] for r in rows)),
        'count_switch_events':sum(bool(r[0].get('counts_l1')) for r in rows if r[0].get('deployment_completed')),
        'counts_l1_total':sum(r[0].get('counts_l1',0) for r in rows if r[0].get('deployment_completed')),
        'delivered_intergroup_transfers':sum(r[0].get('delivered_intergroup_transfers',0) for r in rows),
        'accepted_frames':sum(r[0].get('accepted_certificate',False) for r in rows if r[0].get('deployment_completed')),
        'fallback_frames':sum(r[0].get('decision')=='uncertified_fallback' for r in rows if r[0].get('deployment_completed')),
        'nontrivial_hold_frames':sum(r[1]['planning'].get('decision')=='hold_counts_and_phase' and not np.array_equal(r[3],r[1]['planning']['baseline_targets'])
                                      for r in rows if r[0].get('deployment_completed'))}
    if 'event_worst_uncovered' in summary:
        elapsed,times,worst,weighted=0.,[],[],[]
        for m,_,_,_,_ in rows:
            elapsed+=m.get('motion',{}).get('simulation_time_s') or 0.
            time=elapsed if 'motion' in m else None
            if time!=m['elapsed_deployment_event_time_s']:raise ValueError('Episode time bookkeeping mismatch')
            times.append(time);service=m.get('continuous_actual_service') if m.get('deployment_completed') else None
            worst.append(service['worst_group_uncovered'] if service else None)
            weighted.append(service['weighted_uncovered'] if service else None)
        recomputed.update(deployment_event_times_s=times,event_worst_uncovered=worst,event_weighted_uncovered=weighted)
    if any(summary[key]!=value for key,value in recomputed.items()):raise ValueError('Episode summary does not match complete ledger')
    return rows,completion['summary']


def worker(argument):
    study,unit,manifest=argument;study=Path(study);base=manifest['spec'];design=manifest['design'];config=base['model']|design['model']
    truth,guides,nominal=_load_inputs(study,unit);reference=reference_groups(truth,base['room_m'],config)
    checked,mismatches,certificate_frames,episodes=0,[],0,[];starts={}
    with threadpool_limits(limits=1):
        groups,_=construct_groups(nominal,base['room_m'],config)
        maximum=max(base['resources'][r] for r in design['resources'])*unit['group_count']
        nominal_catalog=build_catalog(groups,nominal,construct_groups(nominal,base['room_m'],config)[1],maximum,'projected_mass',config)
        for resource in design['resources']:
            name=unit['unit_id']+'_nominal_'+resource
            result,q,ids,saved=load_task(study/'results'/name)
            if 'motion' in saved:
                allocation=allocate(nominal_catalog,groups,base['resources'][resource]*unit['group_count'],'minimax')
                pools=[phase_pool(g,nominal,construct_groups(nominal,base['room_m'],config)[1],int(n),config) for g,n in zip(groups,allocation.counts)]
                cq,cg,_,_=choose_groups(pools,'continuous_euclidean_greedy')
                if not np.array_equal(q,cq) or not np.array_equal(ids,cg):mismatches.append(name+':DECISION')
                fresh=evaluate(result,q,ids,groups,nominal,truth,base['room_m'],config)
                if any(fresh[k]!=saved[k] for k in fresh):mismatches.append(name)
            starts[resource]=(result,ids,saved)
            checked+=1
        for pattern in ([unit['only_pattern']] if 'only_pattern' in unit else design['patterns']):
            info=unit['series'][pattern]
            if digest(study/info['path'])!=info['sha256']:raise ValueError('Frozen series changed')
            with np.load(study/info['path'],allow_pickle=False) as data:positions=data['positions'].copy()
            loaded,states={},{}
            for resource in design['resources']:
                result,ids,saved=starts[resource]
                q,g=actual_targets(result,ids) if saved.get('deployment_completed') else (np.empty((0,2)),np.empty(0,int))
                for method in design['methods']:
                    task_id=f'{unit["unit_id"]}_{resource}_{pattern}_{method}'
                    records,summary=load_episode(study/'results'/task_id);loaded[resource,method]=records
                    episodes.append({'physical_id':unit.get('physical_id',unit['unit_id']),'group_count':unit['group_count'],'shape':unit['shape'],'seed':unit['seed'],
                                     'resource':resource,'pattern':pattern,'method':method,**summary})
                    states[resource,method]={'positions':result.positions[-1].copy(),'points':q.copy(),'ids':g.copy()}
            for frame in range(1,51):
                obs=BoundedMeasurement(Observation(positions[frame],truth.radii,truth.demand),design['declared_error_m'],'frozen_bounded_reobservation')
                current_groups,domain=construct_groups(obs,base['room_m'],config)
                catalogs=build_catalog(current_groups,obs,domain,maximum,'projected_mass',config)
                support=build_support(obs,current_groups,config)
                phase_cache={}
                for g,region in zip(reference,support.regions):
                    if not LineString(np.vstack([g.boundary.deployment,g.boundary.deployment[0]])).difference(region).is_empty:
                        mismatches.append(unit['unit_id']+f':{pattern}:{frame}:SUPPORT')
                for key,records in loaded.items():
                    saved,state,execution,q,ids=records[frame-1];current=states[key];name=saved['task_id']+f':{frame}'
                    if not np.array_equal(execution.positions[0],current['positions']):mismatches.append(name+':STATE_RESET')
                    if 'motion' in saved:
                        cq,cg,planning=decide(obs,current_groups,domain,catalogs,len(current['positions']),current['points'],current['ids'],config,support,key[1],phase_cache)
                        if not np.array_equal(q,cq) or not np.array_equal(ids,cg) or normalize(planning)!=state['planning']:
                            mismatches.append(name+':DECISION')
                        fresh=evaluate(execution,q,ids,current_groups,obs,truth,base['room_m'],config)
                        actual=continuous_summary(reference,truth,execution.positions[-1][execution.assignment>=0],config) if len(execution.velocities) else None
                        if any(fresh[k]!=saved[k] for k in fresh) or actual!=saved['continuous_actual_service']:mismatches.append(name+':EVALUATION')
                        if planning['accepted_certificate'] and saved.get('deployment_completed'):
                            certificate_frames+=1
                            baseline=continuous_summary(reference,truth,planning['baseline_targets'],config)
                            losses=[a['uncovered']-b['uncovered'] for a,b in zip(actual['groups'],baseline['groups'])]
                            if losses!=saved['actual_service_losses_against_own_baseline'] or max(losses)>config['true_service_delta']:mismatches.append(name+':MODEL_BOUND')
                        current['positions']=execution.positions[-1].copy()
                        if saved.get('deployment_completed'):current['points'],current['ids']=actual_targets(execution,ids)
                    checked+=1
    return {'unit_id':unit['unit_id'],'checked':checked,'mismatches':mismatches,'certificate_frames':certificate_frames,'episodes':episodes}


def bootstrap(values,seed,repetitions=10000):
    block_count=len(values)
    groups=defaultdict(list)
    for (k,shape,_),value in values.items():groups[k,shape].append(value)
    if len(groups)!=4:return {'estimate':None,'ci_95':None,'reason':'Missing prespecified strata'}
    rng=np.random.default_rng(seed);draws=np.zeros(repetitions)
    for values in groups.values():
        numbers=np.asarray(values,float);draws+=rng.choice(numbers,(repetitions,len(numbers)),replace=True).mean(axis=1)/4
    return {'estimate':float(np.mean([np.mean(v) for v in groups.values()])),'ci_95':np.quantile(draws,[.025,.975]).tolist(),'blocks':block_count}


def audit(study,workers,output=None):
    study=Path(study).resolve();manifest=verify(study)
    output=Path(output).resolve() if output is not None else study
    if output!=study:output.mkdir(parents=True,exist_ok=False)
    if read(study/'summary.json')['stopped']:raise ValueError('Scientific stop; inspect preserved artifacts without rerunning')
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize_worker) as pool:
        results=[]
        for result in pool.map(worker,[(str(study),u,manifest) for u in manifest.get('work_items',manifest['units'])]):
            results.append(result);print(json.dumps({'replayed_units':len(results),'scheduled':len(manifest.get('work_items',manifest['units']))}),flush=True)
    mismatches=[v for r in results for v in r['mismatches']];episodes=[e for r in results for e in r['episodes']]
    result={'manifest_sha256':digest(study/'manifest.json'),'checked_step_records':sum(r['checked'] for r in results),
            'scheduled_step_records':manifest['scheduled_step_records'],'mismatches':mismatches,
            'all_match':not mismatches and sum(r['checked'] for r in results)==manifest['scheduled_step_records'],
            'certificate_frames':sum(r['certificate_frames'] for r in results),'independent_physical_blocks':len(manifest['units']),
            'evidence':manifest['evidence'],'by_method':{method:{'episodes':sum(e['method']==method for e in episodes),
                'completed_episodes':sum(e['method']==method and e['sequence_completed'] for e in episodes),
                'accepted_frames':sum(e['accepted_frames'] for e in episodes if e['method']==method),
                'fallback_frames':sum(e['fallback_frames'] for e in episodes if e['method']==method),
                'count_switch_events':sum(e['count_switch_events'] for e in episodes if e['method']==method),
                'nontrivial_hold_frames':sum(e['nontrivial_hold_frames'] for e in episodes if e['method']==method)} for method in manifest['design']['methods']}}
    stat=manifest['design']['statistics']
    if isinstance(stat,dict):
        pairs=defaultdict(dict)
        for e in episodes:
            if e['resource']==stat['primary_resource'] and e['pattern']==stat['primary_pattern']:
                pairs[e['group_count'],e['shape'],e['seed']][e['method']]=e
        differences={k:p['counts_phase_guard']['actual_total_travel_m']-p['recompute_all']['actual_total_travel_m'] for k,p in pairs.items()
                     if p['counts_phase_guard']['sequence_completed'] and p['recompute_all']['sequence_completed']}
        result['primary_joint_completed_pairs']=len(differences);result['primary_scheduled_pairs']=len(pairs)
        result['primary_conditional_travel_difference']=bootstrap(differences,stat['bootstrap_seed']) if differences else None
        bound=50*max(manifest['spec']['resources'].values())*max(manifest['design']['group_counts'])*manifest['spec']['model']['max_speed_m_s']*manifest['spec']['model']['deadline_s']
        lower={k:differences.get(k,-bound) for k in pairs};upper={k:differences.get(k,bound) for k in pairs}
        result['full_ledger_travel_identification_range_m']=[bootstrap(v,0,1)['estimate'] for v in (lower,upper)]
    write_json(output/'offline_audit.json',result);write_json(output/'episode_summaries.json',episodes)
    if not result['all_match'] or result['checked_step_records']!=result['scheduled_step_records']:raise ValueError('OFFLINE_AUDIT_MISMATCH')
    return result
