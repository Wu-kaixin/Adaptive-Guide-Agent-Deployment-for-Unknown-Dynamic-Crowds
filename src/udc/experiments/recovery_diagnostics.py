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
    if full: verify(value['protected']); verify(value['references'])
    return value


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
    boundary=load_boundary(Path(row['physical'])); plan=load_plan(Path(row['original_plan'])/'final')
    source=arrays(old/'trajectory.npz'); cfg=yaml.safe_load(Path(row['config']).read_text(encoding='utf-8'))
    expected_files={p:lockvalue['references'][p] for p in (row['config'],row['initial_assignment'],str(old/'trajectory.npz'))}
    verify(expected_files); _official_monitor()
    class Observed(original_controller):
        def prepare_prescribed(self,*args,**kwargs):
            answer=super().prepare_prescribed(*args,**kwargs); holder['controller']=self
            if not np.array_equal(self.assignment,expected): raise RuntimeError('Initial assignment differs from frozen target experiment')
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
    actual=arrays(destination/'trajectory.npz'); length=min(len(source['positions']),len(actual['positions']))
    first=None
    for k in range(length):
        if not np.array_equal(source['positions'][k],actual['positions'][k]): first=k; break
    controller=holder['controller']
    trace=assignment_trace_check(expected,actual['assignment'],actual['targets'],result['recovery_attempts'])
    prefix={}
    for name in ('positions','nominal_velocity','safe_velocity','waypoint_index'):
        if name in source and name in actual:
            n=min(len(source[name]),len(actual[name])); prefix[name]={'common_samples':n,'exact':np.array_equal(source[name][:n],actual[name][:n])}
    summary={**{k:row[k] for k in ('task_id','scene','seed','count','method','original_status','original_physical_success')},
        'attempt':attempt,'status':result['termination_status'],'success':result['success'],'physical_success':replay['success'],
        'criteria_consistent':replay['criteria_consistent'],'assignment_trace':trace,'motion':replay['motion'],
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


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',default=OUTPUT)
    p.add_argument('--stage',required=True,choices=['freeze','baseline']); a=p.parse_args(); root=Path(a.output).resolve()
    if a.stage=='freeze': freeze(a.output)
    else:
        value=lock(root,full=True); row=next(r for r in read(root/'manifest_360.json') if r['task_id']==value['spec']['focal'])
        execute(root,row,'A_baseline')


if __name__=='__main__': main()
