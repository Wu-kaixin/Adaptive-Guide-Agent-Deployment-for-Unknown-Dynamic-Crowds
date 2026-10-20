"""Fixed-target recovery experiments and observational replay; no replanning."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
from time import perf_counter
import traceback

import numpy as np
import yaml
from threadpoolctl import threadpool_limits, threadpool_info

from udc.experiments import runner
from udc.controller import abcg
from udc.navigation import paths as navmod
from udc.experiments.demand_pilot import read, arrays
from udc.experiments.nonconvex_rebuild import write, sha, load_boundary, load_plan, _official_monitor
from udc.experiments.runner import _atomic_npz
from udc.experiments.provenance import provenance
from udc.experiments.demand_confirmation import assignment_trace_check
from udc.experiments.phase_attribution import independent_rejudge

SPEC='configs/step1/resource_recovery_diagnostics_v1.yaml'
OUTPUT='runs/step1_resource_recovery_diagnostics_v1'


def digest(a): return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def verify(files):
    for name,value in files.items():
        if sha(name)!=value: raise RuntimeError('Frozen file changed: '+name)


def freeze(output):
    root=Path(output).resolve()
    if root.exists(): raise FileExistsError('Never overwrite an attempt')
    meta=provenance({},None,'fixed_n',None)
    if meta['dirty']: raise RuntimeError('Commit diagnostics protocol before freezing')
    spec=yaml.safe_load(Path(SPEC).read_text(encoding='utf-8'))
    old=Path(spec['resource_run']).resolve(); old_report=Path(spec['resource_report']).resolve()
    resource=read(old/'protocol.json'); frozen=read(old/'inputs_frozen.json')
    raw=read(old_report/'raw_checksums.json')
    protected={str(old/k):v for k,v in raw['run_files'].items()}
    protected.update(resource['historical_files'])
    protected.update({str(p):sha(p) for p in old_report.rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    verify(protected)
    entries=[]; refs={}
    for row in read(old/'manifest_360.json')['rows']:
        result=old/'results'/row['task_id']; detail=read(result/'protocol_metrics.json')
        if not detail['target_valid']:
            entries.append({**row,'original_status':detail['status'],'target_valid':False,'reference_result':str(result)})
            continue
        init=frozen['starts'][row['initial_key']]
        original=(Path(resource['spec']['source_run']).resolve()/'results'/row['old_task_id'] if row['mode']=='reference' else result)
        plans=(Path(resource['spec']['source_run']).resolve()/'plans'/row['old_task_id'] if row['mode']=='reference' else old/'plans'/row['task_id'])
        entry={**row,'target_valid':True,'original_status':detail['status'],'original_physical_success':detail['offline_success'],
            'original_result':str(original),'original_plan':str(plans),'physical':init['physical'],
            'config':str(original/'config.yaml'),'initial_assignment':str(plans/'assignment.npz')}
        for p in (original/'config.yaml',original/'metrics.json',original/'trajectory.npz',
                  Path(init['physical'])/'boundary.npz',plans/'final/targets.npz',plans/'final/plan.json',plans/'assignment.npz'):
            refs[str(p)]=sha(p)
        entries.append(entry)
    if sum(r['target_valid'] for r in entries)!=276 or len(entries)!=360: raise RuntimeError('Unexpected original accounting')
    if not all(t in {r['task_id'] for r in entries if r['target_valid']} for t in spec['small_regression']): raise RuntimeError('Small identities missing')
    root.mkdir(parents=True)
    write(root/'manifest_360.json',entries)
    write(root/'protocol.json',{'spec':spec,'provenance':meta,'protected':protected,'references':refs,
        'manifest_sha256':sha(root/'manifest_360.json'),'config_sha256':sha(SPEC),'original_resource':resource['provenance'],
        'initially_unchanged_science':{str(p):sha(p) for folder in ('controller','navigation','safety','evaluation')
            for p in Path('src/udc',folder).rglob('*.py')},
        'baseline_runner_sha256':sha('src/udc/experiments/runner.py')})
    print({'frozen':len(entries),'targets':276,'readonly_rejections':84,'protected':len(protected)})


def lock(root,full=False):
    value=read(root/'protocol.json')
    if sha(root/'manifest_360.json')!=value['manifest_sha256'] or sha(SPEC)!=value['config_sha256']:
        raise RuntimeError('Frozen identities/config changed')
    if full:
        transition=read(root/'source_transition.json') if (root/'source_transition.json').exists() else None
        for name,digest_value in value['protected'].items():
            if transition is not None and str(Path(name).resolve())==transition['path']:
                if digest_value!=transition['original_raw_sha256'] or sha(name)!=transition['candidate_raw_sha256']:
                    raise RuntimeError('Declared source transition changed')
                blob=subprocess.check_output(['git','show',transition['original_git_ref']+':'+transition['repository_path']])
                variants=[blob,blob.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')]
                if not any(hashlib.sha256(b).hexdigest()==digest_value for b in variants):
                    raise RuntimeError('Historical source Git object does not match original raw evidence')
            else: verify({name:digest_value})
        verify(value['references'])
    return value


def declare_repair(output):
    root=Path(output).resolve(); value=lock(root)
    if (root/'source_transition.json').exists(): raise FileExistsError('Only one declared scientific patch')
    if not read(root/'B_navigation_only'/value['spec']['focal']/'summary.json')['physical_success']:
        raise RuntimeError('No supporting navigation counterfactual')
    name='src/udc/navigation/paths.py'; absolute=str(Path(name).resolve()); old=value['protected'][absolute]
    blob=subprocess.check_output(['git','show','125fd653:'+name])
    if not any(hashlib.sha256(b).hexdigest()==old for b in (blob,blob.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))):
        raise RuntimeError('Original source evidence mismatch')
    write(root/'source_transition.json',{'path':absolute,'repository_path':name,'original_git_ref':'125fd653',
        'original_raw_sha256':old,'original_git_blob_sha256':hashlib.sha256(blob).hexdigest(),
        'candidate_raw_sha256':sha(name),'reason':'intentional single shared navigation repair after isolated B evidence; original code verified through Git, old artifacts remain live-file locked',
        'no_original_lock_modified':True,'no_small_episode_started_before_this_record':True})
    lock(root,full=True)


def state(controller,positions):
    return {'positions':np.asarray(positions).tolist(),'assignment':controller.assignment.tolist(),
        'paths':{str(i):v.tolist() for i,v in controller.paths.items()},'waypoints':dict(controller.waypoint),
        'replans':controller.replans,'priority_mode':controller.priority_mode,'priority_order':list(controller.priority_order),
        'priority_index':controller.priority_index,'control_step':controller._control_step,
        'priority_reference':controller._priority_reference,'priority_samples':list(controller._priority_samples),
        'priority_token':controller._priority_token,'navigation':controller._navigation_hashes(),
        'domain_sha256':controller._domain().geometry_sha256,'vertex_pending':list(controller._vertex_pending)}


def execute(root,row,attempt,policy=None):
    """All replay paths start at t=0. Optional policy is a declared development intervention."""
    destination=root/attempt/row['task_id']
    if destination.exists(): raise FileExistsError('No repeated target attempt')
    lockvalue=lock(root); events=[]; holder={}; scope={'name':'initial'}
    original_controller=runner.ABCGController; original_graph=navmod._visibility_graph
    original_nav=abcg.navigate_and_assign
    expected=arrays(row['initial_assignment'])['assignment']; old=Path(row['original_result'])
    expected_paths=read(Path(row['original_plan'])/'paths.json')['paths']
    boundary=load_boundary(Path(row['physical'])); plan=load_plan(Path(row['original_plan'])/'final')
    source=arrays(old/'trajectory.npz'); cfg=yaml.safe_load(Path(row['config']).read_text(encoding='utf-8'))
    expected_files={p:lockvalue['references'][p] for p in (row['config'],row['initial_assignment'],str(old/'trajectory.npz'))}
    verify(expected_files); _official_monitor()
    class Observed(original_controller):
        def prepare_prescribed(self,*args,**kwargs):
            answer=super().prepare_prescribed(*args,**kwargs); holder['controller']=self
            if not np.array_equal(self.assignment,expected): raise RuntimeError('Initial assignment differs from frozen target experiment')
            if not all(np.array_equal(self.paths[int(i)],path) for i,path in zip(np.flatnonzero(expected>=0),expected_paths)):
                raise RuntimeError('Initial paths differ from frozen target experiment')
            holder['initial_assignment']=self.assignment.copy()
            return answer
        def recover(self,observation,positions,trigger_reason='explicit_recovery_request'):
            frame=inspect.currentframe().f_back
            loc=frame.f_locals
            events.append({'kind':'trigger','state':state(self,positions),'runner':{
                'step':loc.get('step'),'hold':loc.get('hold'),'progress':list(loc.get('progress',[])),
                'time_s':float((loc.get('step',-1)+1)*cfg['simulation']['dt'])}})
            try:
                answer=super().recover(observation,positions,trigger_reason)
                events.append({'kind':'recover_return','answer':answer,'state':state(self,positions)})
                return answer
            except Exception as error:
                events.append({'kind':'recover_raise','error':str(error),'cause':str(error.__cause__),
                    'state':state(self,positions)})
                raise
        def _recover_strategy(self,observation,positions,strategy):
            scope['name']=strategy
            events.append({'kind':'strategy_enter','strategy':strategy,'state':state(self,positions)})
            try:
                answer=(policy(self,observation,positions,strategy,super()._recover_strategy) if policy is not None
                        else super()._recover_strategy(observation,positions,strategy))
                events.append({'kind':'strategy_return','strategy':strategy,'state':state(self,positions)})
                return answer
            except Exception as error:
                events.append({'kind':'strategy_raise','strategy':strategy,'error':str(error),
                    'diagnosis':getattr(error,'diagnosis',None),'state':state(self,positions)})
                raise
    def observe_graph(free,obstacle,extra_nodes=()):
        graph=original_graph(free,obstacle,extra_nodes)
        events.append({'kind':'graph_build','scope':scope['name'],'free_wkb_sha256':hashlib.sha256(free.wkb).hexdigest(),
            'obstacle_wkb_sha256':None if obstacle is None else hashlib.sha256(obstacle.wkb).hexdigest(),
            'nodes':len(graph[0]),'edges':sum(map(len,graph[1]))//2,'extra_nodes':len(extra_nodes)})
        return graph
    def observe_nav(*args,**kwargs):
        events.append({'kind':'navigation_call','scope':scope['name'],'start_sha256':digest(args[0]),
            'target_sha256':digest(args[1]),'room':np.asarray(args[2]).tolist(),'wall_margin':args[3],
            'crowd_wkb_sha256':hashlib.sha256(args[4].wkb).hexdigest(),'crowd_margin':args[5],
            'kwargs':sorted(kwargs)})
        return original_nav(*args,**kwargs)
    runner.ABCGController=Observed; navmod._visibility_graph=observe_graph; abcg.navigate_and_assign=observe_nav
    start=perf_counter()
    try:
        with threadpool_limits(limits=1):
            result=runner.experiment(row['config'],destination,plots=False,protocol='fixed_n',fixed_n=row['count'],
                prescribed_boundary=boundary,prescribed_plan=plan,boundary_source='estimated_disk_union',method_label=row['method_label'])
            pools=threadpool_info()
    finally:
        runner.ABCGController=original_controller; navmod._visibility_graph=original_graph; abcg.navigate_and_assign=original_nav
    replay=independent_rejudge(destination/'trajectory.npz',cfg,result)
    comparisons={}
    for key in ('actual_max_gap','mean_curve_distance','tracking_rmse','actual_demand_weighted_cost',
                'planned_demand_weighted_cost','actual_hotspot_concentration'):
        a,b=result.get(key),replay['diagnostics'].get(key)
        comparisons[key]=a==b if a is None or b is None else abs(a-b)<=1e-9
    replay['diagnostics_consistent']=all(comparisons.values())
    actual=arrays(destination/'trajectory.npz'); length=min(len(source['positions']),len(actual['positions']))
    first=None
    for k in range(length):
        if not np.array_equal(source['positions'][k],actual['positions'][k]): first=k; break
    controller=holder['controller']
    trace=assignment_trace_check(expected,actual['assignment'],actual['targets'],result['recovery_attempts'])
    prefix={}
    for name in ('positions','nominal_velocity','safe_velocity','waypoint_index'):
        if name in source and name in actual:
            n=min(len(source[name]),len(actual[name])); first_sample=next((i for i in range(n) if not np.array_equal(source[name][i],actual[name][i])),None)
            prefix[name]={'common_samples':n,'exact':first_sample is None,'first_difference_sample':first_sample}
    summary={**{k:row[k] for k in ('task_id','scene','seed','count','method','original_status','original_physical_success')},
        'attempt':attempt,'status':result['termination_status'],'success':result['success'],'physical_success':replay['success'],
        'criteria_consistent':replay['criteria_consistent'],'assignment_trace':trace,'motion':replay['motion'],
        'diagnostics_consistent':replay['diagnostics_consistent'],
        'first_position_difference_frame':first,'prefix':prefix,'source':provenance({},None,'fixed_n',row['count']),
        'numeric_pools':pools,'counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],
        'wall_s':perf_counter()-start,'recoveries':result['replan_count'],'targets_exact':np.array_equal(actual['targets'],source['targets']),
        'input_exact':all(np.array_equal(actual[k],source[k]) for k in ('crowd_positions','crowd_radii','crowd_demand','room','crowd_hotspot_mask'))
            and np.array_equal(actual['positions'][0],source['positions'][0])}
    write(destination/'observation_log.json',events); write(destination/'independent_rejudge.json',replay)
    write(destination/'summary.json',summary)
    if attempt=='A_baseline':
        write(destination/'terminal_control_state.json',state(controller,actual['positions'][-1]))
    print(json.dumps({'task':row['task_id'],'attempt':attempt,'status':summary['status'],'time':replay['motion']['simulation_time_s'],
        'first_difference':first}),flush=True)
    return summary


def boundary_contact_counterfactual(output):
    root=Path(output).resolve(); value=lock(root,full=True)
    verify(value['initially_unchanged_science'])
    row=next(r for r in read(root/'manifest_360.json') if r['task_id']==value['spec']['focal'])
    if (root/'B_navigation_only').exists(): raise FileExistsError('Single bounded B attempt')
    write(root/'B_hypothesis.json',{'hypothesis':'a numerical boundary start is omitted because a 1.776e-15 projection vector is treated as a reliable direction',
        'change':'only when original boundary node fails and contact is within original 1e-7, try the existing outward normal with original 1e-8 node offset',
        'guards':['free.covers node','physical_free.covers full prefix','original complete segment check'],
        'unchanged':['targets','initial assignment','recovery control flow','budget','clock','progress','safety filter'],
        'no_C_or_D':'B has direct omitted-path evidence; first test only the navigation correction'})
    old=navmod._boundary_node; interventions=[]
    def contact(point,domain):
        node=old(point,domain)
        if node is not None: return node
        point=np.asarray(point,dtype=float); boundary=domain.conservative_obstacle.boundary
        nearest=np.asarray(boundary.interpolate(boundary.project(navmod.Point(point))).coords[0])
        distance=float(np.linalg.norm(nearest-point))
        if distance>navmod.ENDPOINT_TOLERANCE_M: return None
        direction=navmod._outward_free_direction(nearest,domain)
        if direction is None: return None
        candidate=nearest+navmod.BOUNDARY_NODE_NUDGE_M*direction
        accepted=(domain.free.covers(navmod.Point(candidate))
            and domain.physical_free.covers(navmod.LineString([point,candidate]))
            and navmod.segment_in_free(point,candidate,domain))
        interventions.append({'true_position':point.tolist(),'projection':nearest.tolist(),'projection_distance':distance,
            'node':candidate.tolist(),'accepted':bool(accepted),'domain_sha256':domain.geometry_sha256})
        return candidate if accepted else None
    navmod._boundary_node=contact
    try:
        result=execute(root,row,'B_navigation_only')
        from udc.analysis.recovery_diagnostics import matrix
        matrix(output,'B_counterfactual')
    finally: navmod._boundary_node=old
    write(root/'B_interventions.json',interventions)
    return result


def safe_execute(task):
    rootstr,row,attempt=task; root=Path(rootstr)
    if attempt=='full_candidate':
        frozen=read(root/'candidate_freeze.json')
        if provenance({},None,'fixed_n',None)['source_sha256']!=frozen['source']['source_sha256']:
            raise RuntimeError('Candidate scientific code changed during full regression')
    try: return execute(root,row,attempt)
    except Exception as error:
        dest=root/attempt/row['task_id']; dest.mkdir(parents=True,exist_ok=True)
        runner._atomic_text(dest/'orchestration_error.txt',traceback.format_exc())
        result={'task_id':row['task_id'],'attempt':attempt,'status':'EXPERIMENT_ERROR','success':False,
            'physical_success':None,'error':repr(error),'original_status':row['original_status'],
            'original_physical_success':row['original_physical_success']}
        write(dest/'orchestration_error.json',result); return result


def stops(result):
    return (result['status'] in ('EXPERIMENT_ERROR','SAFETY_VIOLATION')
        or result.get('criteria_consistent') is False or result.get('diagnostics_consistent') is False
        or (result.get('motion') or {}).get('interval_safety_ok') is False
        or not result.get('input_exact',True) or not result.get('targets_exact',True)
        or not result.get('assignment_trace',{}).get('accepted',True))


def freeze_candidate(output):
    root=Path(output).resolve(); value=lock(root,full=True)
    if (root/'candidate_freeze.json').exists(): raise FileExistsError('Single candidate freeze')
    meta=provenance({},None,'fixed_n',None)
    if meta['dirty']: raise RuntimeError('Commit candidate and require a clean tree before full regression')
    gate=read(root/'small_gate.json')
    if not gate['passed']: raise RuntimeError('Small regression did not pass; no full matrix')
    expected=[r for r in read(root/'manifest_360.json') if r['target_valid']]
    write(root/'candidate_freeze.json',{'source':meta,'task_ids':[r['task_id'] for r in expected],
        'target_count':276,'readonly_rejections':84,'manifest_sha256':value['manifest_sha256'],
        'fixed_targets_no_replanning':True,'small_gate_sha256':sha(root/'small_gate.json'),
        'candidate_files':{str(p):sha(p) for directory in ('controller','navigation','safety','evaluation')
            for p in Path('src/udc',directory).rglob('*.py')}})


def regress(output,stage,workers):
    root=Path(output).resolve(); value=lock(root,full=True)
    if not 1<=workers<=2 or (stage=='small' and workers!=1): raise ValueError('Small serial, full maximum two workers')
    attempt='small_candidate' if stage=='small' else 'full_candidate'
    if (root/attempt).exists() or (root/(stage+'_start.json')).exists(): raise FileExistsError('Never retry a regression matrix')
    rows=read(root/'manifest_360.json')
    if stage=='small': rows=[next(r for r in rows if r['task_id']==key) for key in value['spec']['small_regression']]
    else:
        frozen=read(root/'candidate_freeze.json'); rows=[r for r in rows if r['target_valid']]
        if [r['task_id'] for r in rows]!=frozen['task_ids']: raise RuntimeError('Full identities changed')
        verify(frozen['candidate_files'])
        if provenance({},None,'fixed_n',None)['source_sha256']!=frozen['source']['source_sha256']: raise RuntimeError('Candidate changed')
    _official_monitor()
    if any(os.environ.get(k,'1')!='1' for k in ('UDC_COMPLETE_SEGMENT','UDC_VERTEX_ARRIVAL')): raise RuntimeError('Original segment/vertex behavior required')
    os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    write(root/(stage+'_start.json'),{'source':provenance({},None,'fixed_n',None),'tasks':[r['task_id'] for r in rows],
        'workers':workers,'numerical_threads':1,'counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL']})
    started=perf_counter(); results=[]; stop=None
    if workers==1:
        for row in rows:
            result=safe_execute((str(root),row,attempt)); results.append(result)
            if stops(result): stop=result['task_id']; break
    else:
        iterator=iter(rows)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pending={}
            def submit_one():
                row=next(iterator,None)
                if row is not None: pending[pool.submit(safe_execute,(str(root),row,attempt))]=row
            for _ in range(workers): submit_one()
            while pending:
                finished,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in finished:
                    pending.pop(future); result=future.result(); results.append(result)
                    if stops(result): stop=result['task_id']
                if stop is None:
                    for _ in finished: submit_one()
    summary={'expected':len(rows),'completed':len(results),'outcomes':dict(Counter(r['status'] for r in results)),
        'wall_s':perf_counter()-started,'stop':stop,'source':provenance({},None,'fixed_n',None)}
    write(root/(stage+'_summary.json'),summary)
    if stage=='small':
        write(root/'small_gate.json',{'passed':len(results)==9 and all(not stops(r) and r['physical_success'] for r in results),
            'required':9,'complete_fixed_target_regression_only_if_pass':True})
    lock(root,full=True)
    if stop: raise RuntimeError('Safety/program/audit stop; preserve all evidence')
    print(json.dumps(summary),flush=True)


def rejudge_full(output):
    root=Path(output).resolve(); value=lock(root,full=True); frozen=read(root/'candidate_freeze.json')
    if provenance({},None,'fixed_n',None)['source_sha256']!=frozen['source']['source_sha256']:
        raise RuntimeError('Use the frozen candidate source for final rejudgement')
    checks=[]
    for row in read(root/'manifest_360.json'):
        if not row['target_valid']:
            checks.append({'task_id':row['task_id'],'status':'READONLY_COMMON_REJECTION','original_status':row['original_status'],
                'executed':False,'motion':None,'reference_files_hash_verified':True})
            continue
        directory=root/'full_candidate'/row['task_id']
        if not (directory/'metrics.json').exists():
            checks.append({'task_id':row['task_id'],'status':'NOT_STARTED'}); continue
        m=read(directory/'metrics.json'); cfg=yaml.safe_load((directory/'config.yaml').read_text(encoding='utf-8'))
        replay=independent_rejudge(directory/'trajectory.npz',cfg,m)
        comparable={}
        for k in ('actual_max_gap','mean_curve_distance','tracking_rmse','actual_demand_weighted_cost',
                  'planned_demand_weighted_cost','actual_hotspot_concentration'):
            a,b=m.get(k),replay['diagnostics'].get(k)
            comparable[k]=a==b if a is None or b is None else abs(a-b)<=1e-9
        replay['diagnostics_consistent']=all(comparable.values()); replay['success_consistent']=replay['success']==m['success']
        write(directory/'independent_rejudge_final.json',replay)
        checks.append({'task_id':row['task_id'],'status':m['termination_status'],'executed':True,
            'success':replay['success'],'criteria_consistent':replay['criteria_consistent'],
            'diagnostics_consistent':replay['diagnostics_consistent'],'success_consistent':replay['success_consistent'],
            'minimum_net_clearances_m':replay['motion']['minimum_net_clearances_m'],
            'unsafe_interval_count':replay['motion']['unsafe_interval_count']})
    write(root/'all_independent_checks_360.json',checks); lock(root,full=True)
    print({'rows':len(checks),'executed':sum(c.get('executed',False) for c in checks),
        'not_started':sum(c['status']=='NOT_STARTED' for c in checks),
        'inconsistent':sum(any(c.get(k) is False for k in ('criteria_consistent','diagnostics_consistent','success_consistent')) for c in checks)})


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',default=OUTPUT)
    p.add_argument('--stage',required=True,choices=['freeze','baseline','counterfactual_b','declare_repair','small','freeze_candidate','full','rejudge'])
    p.add_argument('--workers',type=int,default=1); a=p.parse_args(); root=Path(a.output).resolve()
    if a.stage=='freeze': freeze(a.output)
    elif a.stage=='counterfactual_b': boundary_contact_counterfactual(a.output)
    elif a.stage=='declare_repair': declare_repair(a.output)
    elif a.stage=='freeze_candidate': freeze_candidate(a.output)
    elif a.stage in ('small','full'): regress(a.output,a.stage,a.workers)
    elif a.stage=='rejudge': rejudge_full(a.output)
    else:
        value=lock(root,full=True); row=next(r for r in read(root/'manifest_360.json') if r['task_id']==value['spec']['focal'])
        execute(root,row,'A_baseline')


if __name__=='__main__': main()
