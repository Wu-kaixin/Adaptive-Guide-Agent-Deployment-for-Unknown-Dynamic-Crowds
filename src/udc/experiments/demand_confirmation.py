"""One-draw paired confirmation, reusing frozen Gaussian/layout/P3/execution code."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from time import perf_counter
import traceback

import numpy as np
from shapely.geometry import Polygon
import yaml
from threadpoolctl import threadpool_limits, threadpool_info

from udc.boundary import construct_boundary
from udc.deployment.planners import feasibility_bounds, infeasibility_certificate, periodic_gaps
from udc.environment import Environment
from udc.experiments.boundary_comparison import input_quality
from udc.experiments.demand_pilot import (arrays, read, physical_hash, frozen_density, plan_original)
from udc.experiments.nonconvex_rebuild import (array_hash, draw_cell, load_protocol, load_boundary,
    rejected_result, sha, write, _official_monitor)
from udc.experiments.phase_attribution import candidate_cache, choose_policy, independent_rejudge
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, reevaluate_result, _atomic_npz, _atomic_text, STATUSES
from udc.experiments.scene_closure import _execution_config
from udc.experiments.target_domain import independent_geometry, save_plan
from udc.navigation import navigation_domain, navigate_and_assign


SPEC='configs/step1/nonconvex_demand_confirmation_v1.yaml'
OUTPUT='runs/step1_nonconvex_demand_confirmation_v1'
SCENES=['elongated','tight_u','wide_u']
METHODS=['equal_arc','projected_mass']


def first_interval(exposed,start=75,length=20,maximum=4999):
    for begin in range(start,maximum-length+2):
        if all((scene,seed) not in exposed for scene in SCENES for seed in range(begin,begin+length)):
            return list(range(begin,begin+length))
    raise RuntimeError('No eligible contiguous interval in the frozen search range')


def audit_seeds(project=Path('.'),exclude=None):
    """Scoped local metadata audit; other computers and absent files are not searched."""
    project=Path(project).resolve(); excluded=Path(exclude).resolve() if exclude else None
    inspected={}; exposed=set(); evidence=[]; other=[]; errors=[]
    family=('nonconvex','boundary_estimator','target_domain','scene_closure','priority_progress','joint_progress')
    def register(scene,seed,path,namespace,reason):
        if type(seed) is not int or not 75<=seed<=4999 or scene not in SCENES: return
        if namespace is not None and namespace!=20261019:
            other.append({'scene':scene,'seed':seed,'path':str(path),'namespace':namespace}); return
        if namespace==20261019 or any(s in str(path).lower() for s in family):
            exposed.add((scene,seed))
            evidence.append({'scene':scene,'seed':seed,'path':str(path),'namespace':namespace,'reason':reason})
        else:
            other.append({'scene':scene,'seed':seed,'path':str(path),'namespace':None,'reason':'unattributed_other_protocol'})
    def visit(value,path,namespace=None,scene=None):
        if isinstance(value,dict):
            namespace=value.get('random_namespace',namespace)
            scene=value.get('scene',value.get('scene_name',scene))
            entropy=value.get('entropy')
            if isinstance(entropy,list) and len(entropy)==3 and entropy[0]==20261019 and entropy[1] in (0,1,2):
                register(SCENES[entropy[1]],entropy[2],path,20261019,'saved_random_stream_entropy')
            seed=value.get('seed')
            register(scene,seed,path,namespace,'saved_scene_seed')
            seeds=value.get('seeds',value.get('development_seeds',[]))
            scenes=value.get('scenes',[scene] if scene else [])
            if isinstance(seeds,list) and isinstance(scenes,list):
                for s in scenes:
                    for n in seeds: register(s,n,path,namespace,'protocol_scene_seed_list')
            for k,v in value.items():
                if isinstance(k,str):
                    match=re.search(r'(elongated|tight_u|wide_u)[/\\_ -]+(?:seed[_ -]*)?(\d+)',k)
                    if match: register(match[1],int(match[2]),path,namespace,'record_key')
                visit(v,path,namespace,scene)
        elif isinstance(value,list):
            for v in value: visit(v,path,namespace,scene)
    files=[]
    for folder in ('configs','experiments','reports','runs'):
        base=project/folder
        if not base.exists(): continue
        for p in base.rglob('*'):
            if excluded and (p==excluded or excluded in p.parents): continue
            if '__pycache__' in p.parts or p.name=='nonconvex_demand_confirmation_v1.yaml': continue
            if p.name=='PROTOCOL.zh.md' and p.parent.name=='step1_nonconvex_demand_confirmation_v1': continue
            if p.is_dir():
                m=re.search(r'(elongated|tight_u|wide_u)[/\\](?:seed[_-])(\d+)',str(p))
                if m and any((p/name).is_file() for name in ('input.npz','input_record.json','source.json')):
                    register(m[1],int(m[2]),p,None,'existing_input_directory')
            elif folder!='runs' and p.suffix in ('.json','.yaml','.yml','.csv','.md'):
                files.append(p)
            elif folder=='runs' and p.suffix=='.json' and (any(t in p.name for t in ('manifest','protocol','input_record','source','reconstruction','inputs_frozen'))):
                files.append(p)
    for p in sorted(set(files)):
        try:
            inspected[str(p.relative_to(project))]=sha(p)
            text=p.read_text(encoding='utf-8-sig')
            if p.suffix in ('.yaml','.yml'): visit(yaml.safe_load(text),p)
            elif p.suffix=='.json': visit(json.loads(text),p)
            elif p.suffix=='.csv':
                for row in csv.DictReader(text.splitlines()):
                    row=dict(row)
                    for key in ('seed','random_namespace'):
                        if str(row.get(key,'')).isdigit(): row[key]=int(row[key])
                    visit(row,p)
            else:
                for scene in SCENES:
                    for match in re.finditer(scene+r'[^\n]{0,70}?seed[_ -]*(\d+)',text):
                        register(scene,int(match[1]),p,None,'report_text_scene_seed')
        except Exception as exc:
            errors.append({'path':str(p),'error':str(exc)})
    if errors: raise RuntimeError('Seed audit could not inspect metadata: '+json.dumps(errors))
    selected=first_interval(exposed)
    return {'scope':'this repository configs/experiments/reports and existing runs input records, manifests, protocols; not other computers',
        'generator_namespace':20261019,'scene_order':SCENES,'selection_rule':'first common unexposed contiguous 20 from 75; ambiguous same-family exposure excluded',
        'selected_seeds':selected,'exposed_scene_seed_pairs':[list(x) for x in sorted(exposed)],
        'exposure_evidence':evidence,'other_protocol_or_unattributed_records':other,'inspected_files':inspected}


def dependencies(spec_path=SPEC):
    spec=yaml.safe_load(Path(spec_path).read_text(encoding='utf-8'))
    pilot=yaml.safe_load(Path(spec['demand_spec']).read_text(encoding='utf-8'))
    phase=yaml.safe_load(Path(spec['phase_spec']).read_text(encoding='utf-8'))
    generator,base=load_protocol(spec['generator_spec'])
    if (spec['scenes']!=SCENES or spec['methods']!=METHODS or spec['demands']!=['ones','hotspot5']
        or pilot['guide_counts']!={'elongated':11,'tight_u':13,'wide_u':19}
        or phase['phase_bins']!=256 or phase['grids']['main']['offset_bins']!=0.
        or pilot['deployment']['max_iterations']!=300 or base['simulation']!={'dt':.1,'max_steps':400}):
        raise ValueError('Changed frozen scientific factors')
    return spec,pilot,phase,generator,base


def prepare(output,spec_path=SPEC):
    root=Path(output).resolve()
    if root.exists(): raise FileExistsError('Use a new confirmation output')
    meta=provenance({},None,'fixed_n',None)
    if meta['dirty']: raise RuntimeError('Commit the protocol/analysis/tests and require a clean working tree before confirmation')
    spec,pilot,phase,generator,base=dependencies(spec_path)
    audit=audit_seeds(exclude=root)
    root.mkdir(parents=True)
    write(root/'seed_audit.json',audit)
    rows=[{'task_id':f'{scene}__seed_{seed}__{demand}__{method}','physical_key':f'{scene}__seed_{seed}',
        'condition_key':f'{scene}__seed_{seed}__{demand}','scene':scene,'seed':seed,'demand':demand,
        'method':method,'method_label':method+'+all_phase_256','count':pilot['guide_counts'][scene]}
        for seed in audit['selected_seeds'] for scene in SCENES for demand in spec['demands'] for method in METHODS]
    write(root/'schedule_240.json',{'rows':rows,'input_generated':False,'physical_units':60,'demand_units':120})
    files={str(Path(p).resolve()):sha(p) for p in (spec_path,spec['demand_spec'],spec['phase_spec'],spec['generator_spec'],
        generator['base_spec'],'requirements.txt','pyproject.toml',spec['evaluation']['fine_evaluator'],
        'reports/step1_nonconvex_demand_confirmation_v1/PROTOCOL.zh.md')}
    # Preserve the two development records and their raw evidence without requiring any rerun.
    historical=read('reports/step1_nonconvex_phase_attribution_v1/raw_checksums.json')
    files.update(historical['protected_prior_references'])
    oldroot=Path('runs/step1_nonconvex_phase_attribution_v1').resolve()
    files.update({str(oldroot/k):v for k,v in historical['new_run_files'].items()})
    for directory in ('reports/step1_nonconvex_demand_pilot_v1','reports/step1_nonconvex_phase_attribution_v1'):
        files.update({str(p.resolve()):sha(p) for p in Path(directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    for p,v in files.items():
        if not Path(p).is_file() or sha(p)!=v: raise RuntimeError('Baseline evidence missing/changed: '+p)
    write(root/'protocol.json',{'spec':spec,'pilot':pilot,'phase':phase,'generator':generator,'base':base,
        'provenance':meta,'protected_files':files,'selected_seeds':audit['selected_seeds'],
        'seed_audit_sha256':sha(root/'seed_audit.json'),'schedule_sha256':sha(root/'schedule_240.json')})
    print(json.dumps({'prepared_rows':240,'selected_seeds':audit['selected_seeds'],'clean_commit':meta['commit']}),flush=True)


def locked(root,inputs=False):
    lock=read(root/'protocol.json')
    meta=provenance({},None,'fixed_n',None)
    if meta['source_sha256']!=lock['provenance']['source_sha256']:
        raise RuntimeError('Scientific source changed after protocol freeze')
    for p,v in lock['protected_files'].items():
        if sha(p)!=v: raise RuntimeError('Protected baseline/config changed: '+p)
    if sha(root/'seed_audit.json')!=lock['seed_audit_sha256'] or sha(root/'schedule_240.json')!=lock['schedule_sha256']:
        raise RuntimeError('Prepared seeds or manifest changed')
    if inputs:
        for p,v in read(root/'inputs_frozen.json')['files'].items():
            if sha(root/p)!=v: raise RuntimeError('Frozen confirmation input changed: '+p)
    return lock


def freeze_inputs(output):
    root=Path(output).resolve(); lock=locked(root)
    if (root/'physical').exists() or (root/'inputs_frozen.json').exists(): raise FileExistsError('Each input is drawn once')
    if provenance({},None,'fixed_n',None)['dirty']: raise RuntimeError('Require clean saved source before generating confirmation inputs')
    fresh=audit_seeds(exclude=root)
    if fresh['selected_seeds']!=lock['selected_seeds']: raise RuntimeError('Seed exposure changed after preparation')
    write(root/'audit_at_input_freeze.json',fresh)
    spec,pilot,base=lock['spec'],lock['pilot'],lock['base']
    sources={}; conditions={}; started=perf_counter()
    for seed in lock['selected_seeds']:
        for scene in SCENES:
            key=f'{scene}__seed_{seed}'; cell=root/'physical'/key; cell.mkdir(parents=True)
            data,streams=draw_cell(lock['generator'],base,next(s for s in base['scenes'] if s['name']==scene),seed)
            _atomic_npz(cell/'input.npz',**data)
            room=np.array([[0,0],[base['room_m'],0],[base['room_m'],base['room_m']],[0,base['room_m']]])
            cfgpath=_execution_config(cell,data['people'],room,pilot['guide_counts'][scene],seed,base)
            cfg=yaml.safe_load(cfgpath.read_text(encoding='utf-8')); cfg['guides']['initial_positions']=data['guides'].tolist()
            _atomic_text(cfgpath,yaml.safe_dump(cfg,sort_keys=False))
            quality=input_quality(data); status='ACCEPTED' if quality['accepted'] else 'INITIALIZATION_INVALID'
            reason='' if quality['accepted'] else 'INITIALIZATION_INVALID: one-draw personnel/room constraints failed'
            b=None; description=None
            if quality['accepted']:
                b,description,occupied,_=construct_boundary('disk_union_closed',data['people'],data['radii'],data['room'],**pilot['boundary'])
                if occupied is not None: (cell/'occupied.wkb').write_bytes(occupied.wkb)
                if b is None: reason=description['failure_reason']; status=reason.split(':')[0]
            if b is not None:
                _atomic_npz(cell/'boundary.npz',crowd=b.crowd,deployment=b.deployment,arc=b.arc,length=np.array(b.length))
            record={'scene':scene,'seed':seed,'count':pilot['guide_counts'][scene],'physical_sha256':physical_hash(data),
                'people_quality':quality,'status':status,'reason':reason,'streams':streams,'boundary_description':description,
                'input_sha256':sha(cell/'input.npz'),'boundary_sha256':sha(cell/'boundary.npz') if b is not None else None,
                'guide_initial_sha256':array_hash(guides=data['guides'])}
            write(cell/'input_record.json',record); sources[key]=record
            for demand_name in spec['demands']:
                cid=key+'__'+demand_name; condition=root/'conditions'/cid; condition.mkdir(parents=True)
                region=pilot['hotspots'][scene] if demand_name=='hotspot5' else None
                mask=(np.linalg.norm(data['people']-region['center'],axis=1)<=region['radius']) if region else np.zeros(len(data['people']),bool)
                demand=np.where(mask,5.,1.)
                ccfg={**cfg,'crowd':dict(cfg['crowd'],demand={'mean':1.,'std':0.,'min':1.,'max':5.})}
                if region: ccfg['crowd']['demand_region']=region
                _atomic_text(condition/'input.yaml',yaml.safe_dump(ccfg,sort_keys=False))
                payload={'demand':demand,'hotspot_mask':mask}; density=None
                if b is not None: payload,density=frozen_density(b,data,demand,mask,pilot,5. if region else None)
                _atomic_npz(condition/'demand.npz',**payload)
                if quality['accepted']:
                    env=Environment(ccfg); obs=env.observe()
                    if not all(np.array_equal(a,v) for a,v in [(obs.positions,data['people']),(obs.radii,data['radii']),
                        (obs.demand,demand),(env.initialize_guides(ccfg['guides']),data['guides'])]):
                        raise RuntimeError('Frozen YAML does not reproduce the drawn observation')
                crec={'physical_key':key,'demand_name':demand_name,'status':status,'region':region,
                    'demand_vector':demand.tolist(),'hotspot_person_indices':np.flatnonzero(mask).tolist(),
                    'density':density,'missing_density_reason':reason if density is None else None,
                    'physical_sha256':physical_hash(data),'observation_sha256':array_hash(people=data['people'],radii=data['radii'],demand=demand),
                    'density_file_sha256':sha(condition/'demand.npz'),'config_sha256':sha(condition/'input.yaml')}
                write(condition/'condition.json',crec); conditions[cid]=crec
    rows=[]
    for row in read(root/'schedule_240.json')['rows']:
        c=conditions[row['condition_key']]
        rows.append({**row,'input_sha256':c['observation_sha256'],'physical_sha256':c['physical_sha256'],
            'input_status':sources[row['physical_key']]['status'],'density_sha256':c['density_file_sha256']})
    write(root/'manifest_240.json',{'rows':rows,'physical_units':60,'demand_units':120,'task_count':240})
    files={p.relative_to(root).as_posix():sha(p) for folder in ('physical','conditions') for p in (root/folder).rglob('*') if p.is_file()}
    files['manifest_240.json']=sha(root/'manifest_240.json')
    write(root/'inputs_frozen.json',{'sources':sources,'conditions':conditions,'files':files,'wall_s':perf_counter()-started,
        'all_inputs_and_windows_saved_before_any_method_result':True,'draws':60,'replacement_draws':0})
    print(json.dumps({'physical_units':60,'demand_units':120,'tasks':240,'input_statuses':dict(Counter(r['status'] for r in sources.values()))}),flush=True)


def load_fine_evaluator(lock):
    # Reuse the independent persisted-coordinate implementation from the previous
    # study unchanged; its file bytes are part of the pre-result protocol lock.
    path=Path(lock['spec']['evaluation']['fine_evaluator'])
    modspec=importlib.util.spec_from_file_location('_udc_frozen_fine_evaluator',path)
    module=importlib.util.module_from_spec(modspec); modspec.loader.exec_module(module)
    return module.independent_integral


def independent_row(root,row):
    directory=root/'results'/row['task_id']; metrics=read(directory/'metrics.json')
    trajectory=arrays(directory/'trajectory.npz')
    if len(trajectory['positions'])<=1:
        replay=reevaluate_result(directory)
        return {'criteria_consistent':replay['criteria_consistent'],'diagnostics_consistent':all(replay['diagnostics_consistent'].values()),
                'success':replay['success'],'motion':None,'executed':False}
    cfg=yaml.safe_load((directory/'config.yaml').read_text(encoding='utf-8'))
    replay=independent_rejudge(directory/'trajectory.npz',cfg,metrics)
    compared={}
    for key in ('actual_max_gap','mean_curve_distance','tracking_rmse','actual_demand_weighted_cost',
                'planned_demand_weighted_cost','actual_hotspot_concentration'):
        a,b=metrics.get(key),replay['diagnostics'].get(key)
        compared[key]=a==b if a is None or b is None else abs(float(a)-float(b))<=1e-9
    replay.update(executed=True,diagnostics_consistent=all(compared.values()),diagnostic_comparison=compared)
    return replay


def run_row(task):
    root_string,row=task; root=Path(root_string); lock=locked(root)
    _official_monitor(); start=perf_counter()
    frozen=read(root/'inputs_frozen.json'); source=frozen['sources'][row['physical_key']]
    physical=root/'physical'/row['physical_key']; condition=root/'conditions'/row['condition_key']
    destination=root/'results'/row['task_id']; plans=root/'plans'/row['task_id']
    if destination.exists() or plans.exists(): raise FileExistsError('Never overwrite/retry a confirmation row')
    plans.mkdir(parents=True)
    data=arrays(physical/'input.npz'); field=arrays(condition/'demand.npz'); data['demand']=field['demand']
    cfgpath=condition/'input.yaml'
    detail={**row,'stage':'input','target_valid':False,'initial_paths_ok':False,'executed':False,'motion':None,
        'planned_cost':None,'fine_cost':None,'actual_cost':None,'recoveries':None,'timing':{},'phase':None,
        'source_commit':lock['provenance']['commit'],'source_sha256':lock['provenance']['source_sha256']}
    def reject(status,reason):
        return rejected_result(destination,row,physical,status,reason,snapshot=data,config_path=cfgpath)
    try:
        with threadpool_limits(limits=1):
            detail['worker']={'pid':os.getpid(),'counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],
                'thread_environment':{k:os.environ.get(k) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS')},
                'numeric_pools':threadpool_info()}
            # Verify only this row's frozen shared files; complete snapshot is checked before each stage.
            for p in (physical/'input.npz',condition/'demand.npz',cfgpath):
                if sha(p)!=frozen['files'][p.relative_to(root).as_posix()]: raise RuntimeError('Frozen shared input changed')
            if source['status']!='ACCEPTED':
                detail['stage']='input' if source['status']=='INITIALIZATION_INVALID' else 'boundary'
                raise ValueError(source['reason'])
            boundary=load_boundary(physical); domain=navigation_domain(data['room'],.55,Polygon(boundary.crowd),.55)
            detail['curve_sha256']=source['boundary_sha256']; detail['domain_sha256']=domain.geometry_sha256
            detail['stage']='resource'
            bounds=feasibility_bounds(boundary.length,1.94,.75)
            certificate=infeasibility_certificate(bounds,row['count']); detail['quantity']={**bounds,'certificate':certificate}
            if certificate: raise ValueError('CAPACITY_SHORTFALL: '+certificate)
            detail['stage']='original_layout'; t=perf_counter()
            original,solver=plan_original(boundary,data,field['rho'],row['count'],row['method'],lock['pilot'])
            detail['timing']['original_layout_s']=perf_counter()-t
            save_plan(plans/'original',original,solver)
            if not solver['geometry_pass']:
                raise ValueError('PLAN_SEARCH_EXHAUSTED: original finite layout phases exhausted; no alternative layout inferred')
            detail['stage']='phase_search'; t=perf_counter()
            identity={'condition_key':row['condition_key'],'input_sha256':row['input_sha256'],
                'physical_sha256':row['physical_sha256'],'curve_sha256':source['boundary_sha256'],
                'layout_sha256':array_hash(positions=original.positions,arcs=original.arc_positions),
                'density_sha256':row['density_sha256'],'domain_sha256':domain.geometry_sha256}
            cache,payload=candidate_cache(boundary,original,domain,field,identity,offset=0.,bins=256)
            detail['timing']['all_phase_s']=perf_counter()-t
            write(plans/'candidates.json',cache)
            # No repeated people/curve or full 256*N*N matrices. Coordinates and
            # all distances are reproducible from original arcs + phase + curve.
            _atomic_npz(plans/'candidates.npz',phases=payload['phases'],arc_positions=payload['arc_positions'],origin_indices=payload['origin_indices'])
            index=choose_policy('P3',True,cache)
            detail['phase']={'cache_key':cache['cache_key'],'candidates':256,'accepted':cache['feasible_count'],
                             'selected_index':index,'relative_phase_m':None}
            if index==-1: raise ValueError('PLAN_SEARCH_EXHAUSTED: all_phase_256 found no feasible target; not an infeasibility proof')
            record=cache['records'][index]; arcs=payload['arc_positions'][index]; points=payload['positions'][index]
            plan=replace(original,arc_positions=arcs,positions=points,max_gap=float(periodic_gaps(arcs,boundary.length).max()),
                method=row['method_label'],iterations=0,exit_reason='ALL_PHASE_256',cost=np.array([record['score']['demand']]))
            check=independent_geometry(boundary,original,plan,domain,lock['base'])
            if not check['accepted']: raise RuntimeError('Selected target failed independent saved-geometry rule')
            detail.update(target_valid=True,planned_cost=record['score'],target_geometry=check)
            detail['phase']['relative_phase_m']=record['relative_phase_m']
            save_plan(plans/'final',plan,{'selected_candidate':record,'identity':identity})
            _atomic_npz(plans/'origin_indices.npz',origin_indices=payload['origin_indices'][index])
            t=perf_counter(); detail['fine_cost']=load_fine_evaluator(lock)(boundary,field,points,8192)
            detail['timing']['fine_evaluation_s']=perf_counter()-t
            detail['stage']='initial_path'; t=perf_counter()
            nav=navigate_and_assign(data['guides'],points,data['room'],.55,Polygon(boundary.crowd),.55)
            detail['timing']['initial_path_s']=perf_counter()-t
            _atomic_npz(plans/'assignment.npz',assignment=nav.assignment,lengths=nav.lengths,reachable=nav.reachable)
            write(plans/'paths.json',{'paths':[p.tolist() for p in nav.paths]})
            detail['initial_paths_ok']=True; detail['stage']='execution'; t=perf_counter()
            result=experiment(cfgpath,destination,plots=False,protocol='fixed_n',fixed_n=row['count'],
                prescribed_boundary=boundary,prescribed_plan=plan,boundary_source='estimated_disk_union',method_label=row['method_label'])
            detail['timing']['runner_s']=perf_counter()-t
            actual=arrays(destination/'trajectory.npz')
            detail['exact_pairing']=all(np.array_equal(a,v) for a,v in [(actual['positions'][0],data['guides']),
                (actual['crowd_positions'],data['people']),(actual['crowd_radii'],data['radii']),
                (actual['crowd_demand'],field['demand']),(actual['crowd_hotspot_mask'],field['hotspot_mask']),
                (actual['room'],data['room']),(actual['targets'],points),(actual['assignment'],nav.assignment),
                (actual['boundary_crowd'],boundary.crowd),(actual['boundary_deployment'],boundary.deployment)])
            if not detail['exact_pairing']: raise RuntimeError('Actual input/targets/assignment differ from frozen shared fields')
    except Exception as exc:
        status=str(exc).split(':')[0] if isinstance(exc,ValueError) else None
        if status in STATUSES:
            result=reject(status,str(exc)); detail['failure_evidence']=getattr(exc,'diagnosis',None)
        else:
            if not destination.exists(): result=reject('EXPERIMENT_ERROR','EXPERIMENT_ERROR: runner_error.txt')
            else:
                result=read(destination/'metrics.json') if (destination/'metrics.json').exists() else {}
                result.update(termination_status='EXPERIMENT_ERROR',success=False)
                write(destination/'metrics.json',result)
            _atomic_text(destination/'runner_error.txt',traceback.format_exc())
            write(destination/'state.json',{'status':'error','success':False})
    t=perf_counter(); replay=independent_row(root,row)
    detail['timing']['independent_rejudge_s']=perf_counter()-t
    write(destination/'independent_rejudge.json',replay)
    detail.update(status=result['termination_status'],success=bool(result['success']),executed=replay['executed'],
        motion=replay['motion'],offline_consistent=replay['criteria_consistent'] and replay['diagnostics_consistent'] and replay['success']==bool(result['success']),
        offline_success=replay['success'],failure_reason=result.get('failure_reason'),
        recoveries=result.get('replan_count') if replay['executed'] else None,wall_s=perf_counter()-start)
    if replay['executed']:
        actual=arrays(destination/'trajectory.npz')
        detail['actual_cost']=load_fine_evaluator(lock)(boundary,field,actual['positions'][-1],2048)
        detail['actual_off_curve']=not bool(replay['criteria']['curve_ok'])
    write(destination/'protocol_metrics.json',detail)
    return detail


def stop_required(result):
    return result['status'] in ('SAFETY_VIOLATION','EXPERIMENT_ERROR') or result.get('offline_consistent') is False


def run(output,stage,workers):
    root=Path(output).resolve(); lock=locked(root,inputs=True)
    if not 1<=workers<=2 or (stage=='first' and workers!=1): raise ValueError('First seed serial, maximum two workers')
    if (root/'run_stop.json').exists(): raise RuntimeError('Confirmation stopped; no continuation under this frozen attempt')
    if (root/(stage+'_freeze.json')).exists(): raise FileExistsError('Stage already attempted')
    if stage=='remaining' and not read(root/'first_gate.json')['passed']: raise RuntimeError('First-seed gate not passed')
    rows=read(root/'manifest_240.json')['rows']; first=lock['selected_seeds'][0]
    rows=[r for r in rows if (r['seed']==first)==(stage=='first')]
    _official_monitor()
    if any(os.environ.get(k,'1')!='1' for k in ('UDC_COMPLETE_SEGMENT','UDC_VERTEX_ARRIVAL')):
        raise RuntimeError('Original execution switches must remain enabled')
    os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    write(root/(stage+'_freeze.json'),{'workers':workers,'numeric_threads':1,'counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],
        'commit':lock['provenance']['commit'],'source_sha256':lock['provenance']['source_sha256'],'tasks':[r['task_id'] for r in rows]})
    started=perf_counter(); results=[]; stopped=None
    if workers==1:
        for row in rows:
            result=run_row((str(root),row)); results.append(result)
            print(json.dumps({'task':result['task_id'],'status':result['status']}),flush=True)
            if stop_required(result): stopped=result['task_id']; break
    else:
        # At most two submitted tasks; a safety violation cannot leave an entire
        # confirmation matrix queued in the process pool.
        iterator=iter(rows)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pending={}
            def submit_one():
                row=next(iterator,None)
                if row: pending[pool.submit(run_row,(str(root),row))]=row
            for _ in range(workers): submit_one()
            while pending:
                completed,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in completed:
                    row=pending.pop(future); result=future.result(); results.append(result)
                    print(json.dumps({'task':result['task_id'],'status':result['status']}),flush=True)
                    if stop_required(result): stopped=result['task_id']
                if not stopped:
                    for _ in completed: submit_one()
    summary={'expected':len(rows),'completed':len(results),'wall_s':perf_counter()-started,
        'outcomes':dict(Counter(r['status'] for r in results)),'stop_task':stopped}
    write(root/(stage+'_summary.json'),summary)
    if stopped:
        write(root/'run_stop.json',{'trigger':stopped,'not_started':[r['task_id'] for r in read(root/'manifest_240.json')['rows']
            if not (root/'results'/r['task_id']/'metrics.json').exists()]})
        raise RuntimeError('Safety/program/independent-evaluation stop; retain evidence')
    if stage=='first':
        write(root/'first_gate.json',{'passed':len(results)==12 and all(not stop_required(r) for r in results),
            'counted_in_240':True,'scientific_failures_kept':True})
    print(json.dumps(summary),flush=True)


def rejudge(output):
    root=Path(output).resolve(); locked(root,inputs=True); checks=[]
    for row in read(root/'manifest_240.json')['rows']:
        directory=root/'results'/row['task_id']
        if not (directory/'metrics.json').exists():
            checks.append({'task_id':row['task_id'],'state':'NOT_STARTED'}); continue
        replay=independent_row(root,row)
        write(directory/'independent_rejudge_final.json',replay)
        checks.append({'task_id':row['task_id'],'criteria_consistent':replay['criteria_consistent'],
            'diagnostics_consistent':replay['diagnostics_consistent'],'executed':replay['executed'],'success':replay['success'],
            'success_consistent':replay['success']==bool(read(directory/'metrics.json')['success'])})
    write(root/'all_independent_checks.json',checks)
    print(json.dumps({'rows':len(checks),'inconsistent':sum(c.get('criteria_consistent') is False or c.get('diagnostics_consistent') is False or c.get('success_consistent') is False for c in checks),
        'not_started':sum(c.get('state')=='NOT_STARTED' for c in checks)}),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default=OUTPUT); p.add_argument('--spec',default=SPEC)
    p.add_argument('--stage',choices=['audit','prepare','freeze','first','remaining','rejudge'],required=True)
    p.add_argument('--workers',type=int,default=1); a=p.parse_args()
    if a.stage=='audit':
        audit=audit_seeds(exclude=Path(a.output)); write(Path('.tmp/demand_confirmation_seed_audit.json'),audit)
        print(json.dumps({'selected_seeds':audit['selected_seeds'],'files':len(audit['inspected_files']),'exposures':len(audit['exposure_evidence'])}))
    elif a.stage=='prepare': prepare(a.output,a.spec)
    elif a.stage=='freeze': freeze_inputs(a.output)
    elif a.stage=='rejudge': rejudge(a.output)
    else:
        try:
            run(a.output,a.stage,a.workers)
        except BaseException as error:
            root=Path(a.output).resolve()
            if (root/'manifest_240.json').exists() and not (root/'run_stop.json').exists():
                _atomic_text(root/'orchestration_error.txt',traceback.format_exc())
                write(root/'run_stop.json',{'stage':a.stage,'type':'EXTERNAL_INTERRUPTION' if isinstance(error,KeyboardInterrupt) else 'EXPERIMENT_ERROR',
                    'not_started':[r['task_id'] for r in read(root/'manifest_240.json')['rows']
                        if not (root/'results'/r['task_id']/'metrics.json').exists()]})
            raise


if __name__=='__main__': main()
