"""Read-only controller observation and one isolated deadline continuation.

Production modules remain byte-identical. The continuation compiles the original
runner's AST with precisely one loop upper bound changed; every other branch and
the saved production configuration are retained. Neither run resumes positions.
"""
from __future__ import annotations
import argparse
import ast
import copy
from dataclasses import asdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from time import perf_counter

import numpy as np
import yaml
from threadpoolctl import threadpool_limits, threadpool_info

from udc.controller import abcg
from udc.safety import coordinate
from udc.safety.filter import binding_constraints
from udc.navigation import segment_in_free
from udc.experiments import runner
from udc.experiments.demand_pilot import read, arrays
from udc.experiments.nonconvex_rebuild import write, sha, load_boundary, load_plan, _official_monitor
from udc.experiments.provenance import provenance
from udc.experiments.recovery_diagnostics import state, verify
from udc.experiments.phase_attribution import independent_rejudge
from udc.experiments.demand_confirmation import assignment_trace_check

SPEC='configs/step1/execution_deadline_diagnostics_v1.yaml'
OUTPUT='runs/step1_execution_deadline_diagnostics_v1'
REPORT='reports/step1_execution_deadline_diagnostics_v1'


def transform_deadline(source,limit_steps=600):
    """Fail closed unless the one expected original simulation loop is found."""
    if type(limit_steps) is not int or not 400<limit_steps<=600: raise ValueError('Bounded diagnostic only: 401..600 steps')
    tree=ast.parse(textwrap.dedent(source)); original=copy.deepcopy(tree)
    matches=[n for n in ast.walk(tree) if isinstance(n,ast.For) and isinstance(n.target,ast.Name) and n.target.id=='step']
    if len(matches)!=1: raise ValueError('Expected exactly one original step loop')
    loop=matches[0]
    expected=ast.parse("range(0 if execute else sim['max_steps'], sim['max_steps'])",mode='eval').body
    if ast.dump(loop.iter)!=ast.dump(expected): raise ValueError('Original loop changed; diagnostic refuses adaptation')
    before=copy.deepcopy(loop.iter.args[1]); loop.iter.args[1]=ast.Constant(limit_steps)
    changed=ast.dump(tree); loop.iter.args[1]=before
    if ast.dump(tree)!=ast.dump(original): raise RuntimeError('A second AST change was introduced')
    loop.iter.args[1]=ast.Constant(limit_steps); ast.fix_missing_locations(tree)
    return tree,{'changed_nodes':1,'change':"step loop upper bound sim['max_steps'] -> "+str(limit_steps),
        'original_AST_sha256':hashlib.sha256(ast.dump(original).encode()).hexdigest(),
        'diagnostic_AST_sha256':hashlib.sha256(changed.encode()).hexdigest(),
        'restoring_only_bound_reproduces_original_AST':True,'original_source_sha256':hashlib.sha256(source.encode()).hexdigest()}


def deadline_entry(limit_steps=600):
    tree,proof=transform_deadline(inspect.getsource(runner.experiment),limit_steps)
    namespace=dict(runner.__dict__)
    exec(compile(tree,'<diagnostic_continuation_60s:original_runner>', 'exec'),namespace)
    isolated=namespace['experiment']
    def invoke(*args,**kwargs):
        if not kwargs.get('execute',True) or kwargs.get('resume',False) or kwargs.get('restart_interrupted',False):
            raise ValueError('Diagnostic continuation starts at t=0 with execution enabled')
        cfg=read_yaml(args[0])
        if cfg['simulation']['max_steps']!=400 or cfg['simulation']['dt']!=.1 or cfg['controller']['max_replans']!=3:
            raise ValueError('Diagnostic requires unchanged original 400-step/3-recovery configuration')
        if kwargs.get('prescribed_plan') is None or kwargs.get('prescribed_boundary') is None:
            raise ValueError('Diagnostic requires the frozen target and curve')
        return isolated(*args,**kwargs)
    return invoke,proof


def production_sources():
    result={}
    for name in subprocess.check_output(['git','ls-tree','-r','--name-only','0433bb19','src/udc'],text=True).splitlines():
        if not name.endswith('.py'): continue
        original=subprocess.check_output(['git','show','0433bb19:'+name]).replace(b'\r\n',b'\n')
        if Path(name).read_bytes().replace(b'\r\n',b'\n')!=original: raise RuntimeError('Original execution source differs: '+name)
        result[str(Path(name).resolve())]=sha(name)
    return result


def freeze(output):
    root=Path(output).resolve()
    if root.exists(): raise FileExistsError('Use a new diagnostics directory')
    meta=provenance({},None,'fixed_n',None)
    if meta['dirty']: raise RuntimeError('Commit diagnostic protocol, source and tests before replay')
    spec=yaml.safe_load(Path(SPEC).read_text(encoding='utf-8')); old=Path(spec['confirmation_run']).resolve()
    report=Path(spec['confirmation_report']).resolve(); previous=read(old/'protocol.json')
    row=next(r for r in read(old/'manifest_360.json')['rows'] if r['task_id']==spec['focal'])
    detail=read(old/'results'/row['task_id']/'protocol_metrics.json'); initial=read(old/'inputs_frozen.json')['starts'][row['initial_key']]
    if detail['status']!='TIMEOUT' or not detail['target_valid'] or not detail['initial_paths_ok']: raise RuntimeError('Focal identity changed')
    protected={str(old/k):v for k,v in read(report/'raw_checksums.json')['run_files'].items()}
    protected.update(previous['historical_files'])
    protected.update({str(p.resolve()):sha(p) for p in report.rglob('*') if p.is_file() and '__pycache__' not in p.parts})
    verify(protected); science=production_sources(); _,proof=deadline_entry(600)
    cfg=read_yaml(initial['config'])
    if cfg['simulation']['max_steps']!=400 or cfg['simulation']['dt']!=.1 or cfg['controller']['max_replans']!=3:
        raise RuntimeError('Original time, step or recovery budget differs')
    root.mkdir(parents=True)
    write(root/'protocol.json',{'spec':spec,'source':meta,'production_sources':science,'protected':protected,
        'config_file_sha256':sha(SPEC),'protocol_text_sha256':sha(REPORT+'/PROTOCOL.zh.md'),
        'row':row,'initial':initial,'original_result':str(old/'results'/row['task_id']),
        'original_plan':str(old/'plans'/row['task_id']),'old_source':previous['provenance'],
        'original_config':cfg,'deadline_transform':proof})
    write(root/'environment.json',{'executable':sys.executable,'python':sys.version,'udc_path':str(Path(abcg.__file__).resolve()),
        'source':meta,'no_dependency_upgrade':True})
    print({'focal':row['task_id'],'protected_files':len(protected),'production_sources':len(science),'source':meta},flush=True)


def read_yaml(path): return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


def lock(root):
    value=read(root/'protocol.json')
    if provenance({},None,'fixed_n',None)['source_sha256']!=value['source']['source_sha256']: raise RuntimeError('Diagnostic source changed after freeze')
    verify(value['protected']); verify(value['production_sources'])
    if sha(SPEC)!=value['config_file_sha256'] or sha(REPORT+'/PROTOCOL.zh.md')!=value['protocol_text_sha256']:
        raise RuntimeError('Diagnostic protocol changed')
    return value


def canonical_events(value):
    if isinstance(value,dict):
        return {k:canonical_events(v) for k,v in value.items() if 'seconds' not in k and k not in ('wall_s','solve_time')}
    if isinstance(value,list): return [canonical_events(v) for v in value]
    return value


def execute(output,attempt):
    root=Path(output).resolve(); frozen=lock(root)
    if attempt not in frozen['spec']['attempts']: raise ValueError('Unregistered attempt')
    destination=root/attempt
    if destination.exists(): raise FileExistsError('No replay or continuation retry')
    if attempt=='diagnostic_continuation_60s' and not read(root/'baseline_40s/summary.json')['baseline_exact']:
        raise RuntimeError('Original failure not exactly reproduced')
    _official_monitor(); os.environ.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1')
    original={k:getattr(runner,k) for k in ('ABCGController',)}
    original_safe=abcg.safe_velocity; original_joint=abcg.joint_progress_velocity; original_minimize=coordinate.minimize
    frames=[]; monitors=[]; events=[]; calls=[]; navigation={}; holder={}; phase={}
    old=Path(frozen['original_result']); source=arrays(old/'trajectory.npz'); cfg=frozen['original_config']
    plans=Path(frozen['original_plan']); expected=arrays(plans/'assignment.npz')['assignment']
    paths=read(plans/'paths.json')['paths']; boundary=load_boundary(Path(frozen['initial']['physical'])); plan=load_plan(plans/'final')
    def snapshot(controller,positions):
        result=state(controller,positions); nav=result['navigation']['paths_sha256']
        navigation.setdefault(nav,{'assignment':controller.assignment.tolist(),'paths':{str(k):v.tolist() for k,v in controller.paths.items()}})
        return result
    class Observed(original['ABCGController']):
        def prepare_prescribed(self,*args,**kwargs):
            answer=super().prepare_prescribed(*args,**kwargs); holder['controller']=self
            if not np.array_equal(self.assignment,expected) or not all(np.array_equal(self.paths[int(i)],p) for i,p in zip(np.flatnonzero(expected>=0),paths)):
                raise RuntimeError('Initial assignment or complete installed paths differ')
            events.append({'kind':'initial','state':snapshot(self,args[1])})
            return answer
        def step(self,observation,positions,dt):
            phase.clear(); phase['step']=self._control_step+1
            before=snapshot(self,positions); result=super().step(observation,positions,dt)
            nominal,filtered=result; after=positions+float(dt)*filtered.velocity
            current=(None if self._priority_reference is None else int(self._priority_reference['guide']))
            domain=self._domain(); remain=self.remaining_path_lengths(after)
            record={'frame':self._control_step,'time_start_s':(self._control_step-1)*float(dt),'time_end_s':self._control_step*float(dt),
                'before':before,'after_step':snapshot(self,after),'priority_guide':current,
                'errors_before':self.errors(positions).tolist(),'errors_after':self.errors(after).tolist(),
                'remaining_after':remain.tolist(),'nominal':nominal.tolist(),'filtered':filtered.velocity.tolist(),
                'projection':phase['projection'],'safety_result':{k:v for k,v in asdict(filtered).items() if k!='velocity'},
                'active_constraints':binding_constraints(positions,filtered.velocity,observation.positions,self.room,dt,self.safety,domain.conservative_obstacle),
                'command_segments_legal':[bool(segment_in_free(positions[i],after[i],domain)) for i in range(len(positions))]}
            if current is not None:
                index=self.waypoint[current]; aim=self.paths[current][index]; delta=aim-positions[current]; distance=float(np.linalg.norm(delta))
                tangent=delta/max(distance,1e-30)
                record.update(priority_reference=copy.deepcopy(self._priority_reference),aim=aim.tolist(),aim_distance_before_m=distance,
                    aim_segment_legal=bool(segment_in_free(positions[current],aim,domain)),
                    nominal_along_m_s=float(tangent@nominal[current]),filtered_along_m_s=float(tangent@filtered.velocity[current]),
                    projection_along_m_s=float(tangent@np.asarray(phase['projection'])[current]),
                    same_reference_progress_m=float(self._priority_reference['remaining_before_m']-remain[current]))
            frames.append(record); return result
        def observe_priority_progress(self,next_positions,max_error,window,position_tolerance):
            caller=inspect.currentframe().f_back.f_locals
            before={'token':copy.deepcopy(self._priority_token),'samples':list(self._priority_samples)}
            result=super().observe_priority_progress(next_positions,max_error,window,position_tolerance)
            record={'frame':self._control_step,'priority_mode':self.priority_mode,'reference':copy.deepcopy(self._priority_reference),
                'before':before,'after':{'token':copy.deepcopy(self._priority_token),'samples':list(self._priority_samples)},
                'stalled':bool(result),'window':window,'max_error':max_error,'tolerance':position_tolerance,
                'hold':caller.get('hold'),'ordinary_progress':list(caller.get('progress',[]))}
            monitors.append(record)
            if self._control_step==400:
                holder['cutoff_state']={'controller':snapshot(self,next_positions),'monitor':copy.deepcopy(record),
                    'runner':{'step':caller.get('step'),'hold':caller.get('hold'),'progress':list(caller.get('progress',[]))}}
            return result
        def recover(self,observation,positions,trigger_reason='explicit_recovery_request'):
            caller=inspect.currentframe().f_back.f_locals
            events.append({'kind':'recovery_enter','frame':self._control_step,'state':snapshot(self,positions),
                'runner_progress':list(caller.get('progress',[])),'runner_hold':caller.get('hold')})
            try: return super().recover(observation,positions,trigger_reason)
            finally: events.append({'kind':'recovery_exit','frame':self._control_step,'state':snapshot(self,positions)})
    def observed_safe(*args,**kwargs):
        result=original_safe(*args,**kwargs); phase['projection']=result.velocity.tolist(); return result
    def observed_minimize(*args,**kwargs):
        result=original_minimize(*args,**kwargs); phase['solved']=result; return result
    def observed_joint(*args,**kwargs):
        phase.pop('solved',None); result=original_joint(*args,**kwargs); velocity,elapsed=result
        positions,nominal,people,room,dt,limits,obstacle,guide,tangent,request=args
        matrix,bounds=coordinate._rows(positions,people,room,dt,limits,obstacle)
        solved=phase.get('solved'); raw=None
        if solved is not None:
            v=np.asarray(solved.x).reshape(len(positions),2).copy()
            for i,speed in enumerate(np.linalg.norm(v,axis=1)):
                if speed>limits.max_speed: v[i]*=limits.max_speed/speed
            raw={'success':bool(solved.success),'status':int(solved.status),'message':str(solved.message),'iterations':int(solved.nit),
                'velocity_after_original_speed_clip':v.tolist(),'row_residual_m_s':float(np.max(bounds-matrix@v.ravel(),initial=0.)),
                'achieved_m_s':float(np.asarray(tangent)@v[guide]),'progress_shortfall_m_s':max(0.,float(request-np.asarray(tangent)@v[guide]))}
        calls.append({'frame':phase['step'],'guide':int(guide),'tangent':np.asarray(tangent).tolist(),'request_m_s':float(request),
            'accepted':velocity is not None,'returned_residual':None if velocity is None else velocity.residual,
            'returned_velocity':None if velocity is None else velocity.velocity.tolist(),'solver':raw,'solver_wall_s':float(elapsed)})
        return result
    runner.ABCGController=Observed; abcg.safe_velocity=observed_safe; abcg.joint_progress_velocity=observed_joint; coordinate.minimize=observed_minimize
    started=perf_counter()
    try:
        entry=runner.experiment
        proof=None
        if attempt=='diagnostic_continuation_60s': entry,proof=deadline_entry(600)
        with threadpool_limits(limits=1):
            metrics=entry(frozen['initial']['config'],destination,plots=False,protocol='fixed_n',fixed_n=21,
                prescribed_boundary=boundary,prescribed_plan=plan,boundary_source='estimated_disk_union',method_label=frozen['row']['method_label'])
            pools=threadpool_info()
    finally:
        runner.ABCGController=original['ABCGController']; abcg.safe_velocity=original_safe
        abcg.joint_progress_velocity=original_joint; coordinate.minimize=original_minimize
    controller=holder['controller']; actual=arrays(destination/'trajectory.npz'); original_metrics=read(old/'metrics.json')
    prefix={}
    for key in ('positions','nominal_velocity','safe_velocity','waypoint_index'):
        n=min(len(source[key]),len(actual[key])); dif=next((i for i in range(n) if not np.array_equal(source[key][i],actual[key][i])),None)
        prefix[key]={'samples':n,'exact':dif is None,'first_difference':dif}
    replay=independent_rejudge(destination/'trajectory.npz',cfg,metrics)
    trace=assignment_trace_check(expected,actual['assignment'],actual['targets'],metrics['recovery_attempts'])
    persisted_metrics=json.loads(json.dumps(metrics,allow_nan=False))
    old_coord=canonical_events(original_metrics['coordination']); coord=canonical_events(persisted_metrics['coordination'])
    old_events=canonical_events(original_metrics['recovery_attempts']); new_events=canonical_events(persisted_metrics['recovery_attempts'])
    summary={'attempt':attempt,'status':metrics['termination_status'],'diagnostic_completion':bool(replay['success']),
        'original_protocol_success':False,'original_confirmation_status':'TIMEOUT','prefix':prefix,
        'baseline_exact':all(r['exact'] for r in prefix.values()) and len(actual['positions'])==401
            and metrics['termination_status']=='TIMEOUT' and old_events==new_events and old_coord==coord,
        'events_exact_excluding_wall_time':old_events==new_events,'coordination_exact_excluding_wall_time':old_coord==coord,
        'assignment_trace':trace,'motion':replay['motion'],'criteria':replay['criteria'],
        'source':provenance({},None,'fixed_n',21),'numeric_pools':pools,'counterfactual':os.environ['UDC_PRIORITY_STALL_COUNTERFACTUAL'],
        'wall_s':perf_counter()-started,'recoveries':metrics['replan_count'],'AST_transform':proof,
        'input_targets_exact':all(np.array_equal(actual[k],source[k]) for k in ('crowd_positions','crowd_radii','crowd_demand','room','targets','boundary_deployment','boundary_arc'))
            and np.array_equal(actual['positions'][0],source['positions'][0]),
        'config_exact':read_yaml(destination/'config.yaml')==cfg,
        'diagnostic_label':attempt,'observed_frame_count':len(frames),'monitor_frame_count':len(monitors)}
    write(destination/'frames.json',frames); write(destination/'monitor.json',monitors); write(destination/'events.json',events)
    write(destination/'navigation_sets.json',navigation); write(destination/'joint_calls.json',calls)
    write(destination/'terminal_control_state.json',snapshot(controller,actual['positions'][-1]))
    write(destination/'cutoff_40s_state.json',holder.get('cutoff_state'))
    write(destination/'independent_rejudge.json',replay); write(destination/'summary.json',summary)
    if attempt=='diagnostic_continuation_60s':
        base=read(root/'baseline_40s/cutoff_40s_state.json')
        same_cutoff=base==json.loads(json.dumps(holder.get('cutoff_state'),allow_nan=False))
        write(destination/'continuation_audit.json',{'full_cutoff_state_exact':same_cutoff,'original_protocol_success':False,
            'original_status':'TIMEOUT','config_steps_still_400':cfg['simulation']['max_steps']==400,
            'original_prefix_exact':all(r['exact'] for r in prefix.values()),'no_fourth_recovery':metrics['replan_count']<=3,
            'completion_time_s':replay['motion']['simulation_time_s'] if replay['success'] else None,
            'overrun_s':replay['motion']['simulation_time_s']-40 if replay['success'] else None})
        if not same_cutoff or not all(r['exact'] for r in prefix.values()): raise RuntimeError('Continuation changed pre-cutoff control')
    verify(frozen['protected']); verify(frozen['production_sources'])
    if not replay['criteria_consistent'] or not trace['accepted'] or not summary['input_targets_exact'] or not summary['config_exact']:
        raise RuntimeError('Independent or frozen pairing audit failed')
    print({k:summary[k] for k in ('attempt','status','baseline_exact','recoveries','observed_frame_count')},flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--stage',required=True,choices=['freeze','baseline_40s','diagnostic_continuation_60s'])
    p.add_argument('--output',default=OUTPUT); a=p.parse_args()
    if a.stage=='freeze': freeze(a.output)
    else:
        try: execute(a.output,a.stage)
        except BaseException as error:
            write(Path(a.output)/'diagnostic_orchestration_error.json',{'attempt':a.stage,'type':type(error).__name__,
                'error':str(error),'original_confirmation_untouched':True,'no_automatic_retry':True})
            raise


if __name__=='__main__': main()
