"""One user-authorized final experiment; no scheduler, retry or follow-up run."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import psutil

from . import sequential_deployment as sequential
from . import session_integration as integration
from .static_multigroup import ROOT,verify,digest,write_json,scientific_sources,environment_record
from .frozen_resume import atomic_json

_SEQUENTIAL_WORKER=sequential.worker
_API_CASE=integration.execute_case


def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))


def timed_worker(argument):
    study,unit,_=argument;t=time.perf_counter();cpu=time.process_time()
    try:return _SEQUENTIAL_WORKER(argument)
    finally:
        memory=psutil.Process().memory_info()
        write_json(Path(study)/'unit_timings'/(unit['unit_id']+'.json'),dict(unit_id=unit['unit_id'],wall_s=time.perf_counter()-t,
            cpu_s=time.process_time()-cpu,rss_bytes=memory.rss,process_lifetime_peak_working_set_bytes=getattr(memory,'peak_wset',None)))


def timed_api_case(argument):
    from udc.multigroup.session import StaticDeploymentSession
    original=StaticDeploymentSession.step;latencies=[]
    def measured(self,frame):
        t=time.perf_counter();cpu=time.process_time()
        try:return original(self,frame)
        finally:latencies.append(dict(frame_id=frame.frame_id,wall_s=time.perf_counter()-t,cpu_s=time.process_time()-cpu))
    StaticDeploymentSession.step=measured
    try:return _API_CASE(argument)
    finally:
        StaticDeploymentSession.step=original
        write_json(Path(argument[0])/'case_timings'/(argument[1]['case_id']+'.json'),latencies)


def prepare_api(primary):
    old=read(primary/'manifest.json');settings=old['design']['final_assessment'];study=primary/'api_contract_audit'
    assert read(primary/'readonly_audit/offline_audit.json')['all_match']
    study.mkdir(exist_ok=False);units=[];cases=[]
    selected=[u for u in old['units'] if u['seed']==settings['api_subset_seed']]
    design=dict(selected_units=[u['unit_id'] for u in selected],resources=old['design']['resources'],methods=old['design']['methods'],
        patterns=settings['api_patterns'],updates=old['design']['steps'],restart_after_updates=settings['api_restart_after_updates'],
        workers=settings['api_workers'],selection='Predeclared smallest seed in every K/shape stratum; same final-experiment inputs; contract audit only')
    for unit in selected:
        exported=dict(unit);folder=study/'inputs'/unit['unit_id'];folder.mkdir(parents=True)
        for field in ('truth','observation'):
            source=primary/unit[field];assert digest(source)==unit[field+'_sha256'];target=folder/(field+'.npz');shutil.copyfile(source,target)
            exported[field]=target.relative_to(study).as_posix()
        exported['series']={}
        for pattern in design['patterns']:
            series=unit['series'][pattern];source=primary/series['path'];assert digest(source)==series['sha256']
            target=folder/(pattern+'.npz');shutil.copyfile(source,target);exported['series'][pattern]=dict(path=target.relative_to(study).as_posix(),sha256=digest(target))
            for resource in design['resources']:
                nominal=primary/'results'/(unit['unit_id']+'_nominal_'+resource)
                for method in design['methods']:
                    episode=primary/'results'/f'{unit["unit_id"]}_{resource}_{pattern}_{method}';completion=read(episode/'completion.json')
                    cases.append(dict(case_id=unit['unit_id']+'_'+resource+'_'+pattern+'_'+method,unit_id=unit['unit_id'],resource=resource,method=method,pattern=pattern,
                        old_nominal=str(nominal),old_episode=str(episode),old_nominal_receipt_sha256=digest(nominal/'receipt.json'),
                        old_completion_sha256=digest(episode/'completion.json'),old_config_sha256=completion['config_sha256']))
        units.append(exported)
    assert len(cases)==settings['expected_api_sessions']
    for name,h in old['sources'].items():
        source=primary/'source_snapshot'/name;assert digest(source)==h
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    manifest=dict(evidence='predeclared_api_and_event_boundary_restore_audit_within_final_experiment',new_independent_samples=0,
        spec=old['spec'],design=design,units=units,cases=cases,sources=old['sources'],environment=old['environment'],
        model=old['spec']['model']|old['design']['model'],source_manifest_sha256=digest(primary/'manifest.json'),
        expected_actual_events=settings['expected_api_events'])
    write_json(study/'manifest.json',manifest);write_json(study/'seal.json',dict(manifest_sha256=digest(study/'manifest.json')))


def phase(study,name,workers):
    if name=='execute':
        sequential.worker=timed_worker
        return sequential.run(study,workers)
    if name=='audit':
        from udc.analysis.sequential_audit import audit
        return audit(study,workers,study/'readonly_audit')
    if name=='api':
        prepare_api(study);integration.execute_case=timed_api_case
        return integration.execute(study/'api_contract_audit')
    raise ValueError('Unknown final phase')


def run(study,workers):
    study=Path(study).resolve();m=verify(study);settings=m['design']['final_assessment'];control=study/'final_control'
    overlap=read(study/'input_overlap.json');assert overlap['all_new_physical_inputs_unique'] and overlap['study_manifest_sha256']==digest(study/'manifest.json')
    assert len(m['units'])==settings['expected_physical_blocks'] and m['scheduled_step_records']==settings['expected_step_records_including_nominal']
    assert 1<=workers<=m['design']['maximum_workers'];control.mkdir(exist_ok=False);process=psutil.Process()
    write_json(control/'process.json',dict(pid=process.pid,create_time=process.create_time(),exe=process.exe(),cmdline=process.cmdline()))
    environment=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1'}
    started=time.perf_counter();failure=None
    try:
        for name in ('execute','audit','api'):
            atomic_json(control/'status.json',dict(phase=name,retries=0,last_experiment=True))
            code="import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));from udc.experiments.final_evaluation import phase;phase(p,sys.argv[2],int(sys.argv[3]))"
            with (control/(name+'.log')).open('x',encoding='utf-8') as log:
                child=subprocess.Popen([sys.executable,'-c',code,str(study),name,str(workers)],env=environment,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
                write_json(control/(name+'_process.json'),dict(pid=p.pid,create_time=p.create_time(),exe=p.exe(),cmdline=p.cmdline()));child.wait()
                if child.returncode:raise RuntimeError(name+' phase failed; no retry; inspect preserved log')
            if name=='execute' and read(study/'summary.json')['stopped']:raise RuntimeError('Scientific stop; remaining phases not dispatched')
    except Exception:
        failure=traceback.format_exc();write_json(control/'terminal_failure.json',dict(traceback=failure,no_retry=True))
    from udc.analysis.final_assessment import assess
    result=assess(study,failure,time.perf_counter()-started)
    atomic_json(control/'status.json',dict(phase='finished' if failure is None else 'stopped',last_experiment=True,whole_goal_achieved=False))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--study',required=True);parser.add_argument('--workers',type=int,default=24);args=parser.parse_args()
    print(json.dumps(run(args.study,args.workers),ensure_ascii=False),flush=True)
