"""New-input paired routing comparisons; all policies share allocations."""
import argparse
import itertools
import json
from pathlib import Path
import shutil
import traceback

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from udc.experiments.static_multigroup import (ROOT, write_json, digest, scientific_sources, environment_record,
    _load_inputs, _blank_execution, _save_task)
from udc.experiments.recovery_study import run, replay, STOP
from udc.experiments.static_mechanism import continuous_summary
from udc.multigroup.observation import measure
from udc.multigroup.scenarios import make_truth, initial_guides
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.allocation import build_catalog, allocate, selected_targets
from udc.multigroup.evaluation import evaluate, reference_groups
from udc.multigroup.safe_routing import execute


def prepare(protocol, study):
    design = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    base = yaml.safe_load((ROOT / design['base_protocol']).read_text(encoding='utf-8'))
    expected = 20 if design['evidence']=='prospective_confirmation' else 3
    if design['seeds']['count'] != expected:
        raise ValueError('Freeze 3 development or 20 confirmation seeds per stratum')
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study/'protocol.yaml')
    units,tasks=[],[]
    for k,shape,seed in itertools.product(design['group_counts'],design['shapes'],range(design['seeds']['start'],design['seeds']['start']+expected)):
        truth=make_truth(k,shape,seed,design['namespace'],base['group_size_multipliers'],base['group_hotspot_multipliers'])
        obs=measure(truth,design['error_bound_m'],np.random.SeedSequence([design['namespace'],seed,k]),'bounded_disk')
        name=f'K{k}_{shape}_s{seed}'
        folder=study/'inputs'/name;folder.mkdir(parents=True)
        np.savez_compressed(folder/'truth.npz',positions=truth.positions,radii=truth.radii,demand=truth.demand,
                            group_ids=truth.group_ids,guides=initial_guides(k*max(base['resources'][r] for r in design['resources']),base['room_m']))
        np.savez_compressed(folder/'observation.npz',positions=obs.observation.positions,radii=obs.observation.radii,demand=obs.observation.demand)
        unit={'unit_id':name,'group_count':k,'shape':shape,'seed':seed,'error_bound_m':design['error_bound_m']}
        for field in ('truth','observation'):
            path=folder/(field+'.npz');unit[field]=path.relative_to(study).as_posix();unit[field+'_sha256']=digest(path)
        units.append(unit)
        for resource,allocator,policy in itertools.product(design['resources'],design['allocators'],design['policies']):
            tasks.append(unit|{'resource':resource,'budget':base['resources'][resource]*k,'allocator':allocator,'policy':policy,
                'task_id':f'{name}_{resource}_{allocator}_{policy}'})
    sources=scientific_sources()
    for relative in sources:
        target=study/'source_snapshot'/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,target)
    manifest={'protocol':design['protocol'],'evidence':design['evidence'],'spec':base,'design':design,'units':units,'tasks':tasks,
              'sources':sources,'environment':environment_record(),'protocol_sha256':digest(study/'protocol.yaml'),
              'new_independent_samples':len(units),'prepared_before_method_evaluation':True,
              'allocation_shared_by_all_routing_policies':True}
    write_json(study/'manifest.json',manifest);write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})
    return {'units':len(units),'tasks':len(tasks),'manifest_sha256':digest(study/'manifest.json')}


def worker(argument):
    study,unit,manifest=argument;study=Path(study);base=manifest['spec'];config=base['model']|manifest['design']['routing']
    tasks=[t for t in manifest['tasks'] if t['unit_id']==unit['unit_id']]
    truth,guides,obs=_load_inputs(study,unit);rows=[];setup=None;plans={}
    with threadpool_limits(limits=1):
        try:
            groups,domain=construct_groups(obs,base['room_m'],config)
            catalogs=build_catalog(groups,obs,domain,max(t['budget'] for t in tasks),'projected_mass',config)
            reference=reference_groups(truth,base['room_m'],config)
        except Exception as error:setup=error
        for task in tasks:
            result=_blank_execution(guides[:task['budget']],'NOT_EXECUTED');targets,ids=np.empty((0,2)),np.empty(0,int);metrics=dict(task);planning={}
            try:
                if setup is not None:raise setup
                key=(task['resource'],task['allocator'])
                if key not in plans:
                    allocation=allocate(catalogs,groups,task['budget'],task['allocator'])
                    q,g=selected_targets(allocation,config);plans[key]=(allocation,q,g)
                allocation,targets,ids=plans[key]
                planning={'counts':allocation.counts,'objective':allocation.objective,'targets':targets,'target_groups':ids,
                          'same_targets_for_all_routing_policies':True}
                metrics['continuous_planned_service']=continuous_summary(reference,truth,targets,config)
                result=execute(guides[:task['budget']],targets,domain,base['room_m'],config,obs,task['policy'])
                metrics.update(evaluate(result,targets,ids,groups,obs,truth,base['room_m'],config))
                if not metrics['motion']['initial_safety_ok'] or metrics['motion']['interval_safety_ok'] is False:metrics['status']='SAFETY_VIOLATION'
                elif result.status!='ARRIVED':metrics['status']=result.status
                else:metrics['status']='FULL_SUCCESS' if metrics['full_task_success'] else 'PARTIAL_SERVICE'
                metrics['continuous_actual_service']=continuous_summary(reference,truth,result.positions[-1][result.assignment>=0],config) if len(result.velocities) else None
            except ScientificFailure as error:metrics.update(status=error.status,failure_message=str(error),failure_evidence=error.evidence)
            except Exception:metrics.update(status='EXPERIMENT_ERROR',traceback=traceback.format_exc())
            _save_task(study/'results'/task['task_id'],task,config,result,targets,ids,planning,metrics);rows.append(metrics)
            if metrics['status'] in STOP:break
    return rows


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run','replay']);p.add_argument('--protocol');p.add_argument('--study',required=True);p.add_argument('--workers',type=int,default=32)
    args=p.parse_args();s=Path(args.study).resolve()
    value=prepare(args.protocol,s) if args.action=='prepare' else run(s,args.workers,worker) if args.action=='run' else replay(s,args.workers)
    print(json.dumps(value,ensure_ascii=False,indent=2),flush=True)
