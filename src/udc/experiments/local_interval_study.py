"""Freeze a local arithmetic development study; never execute deployment."""
import argparse
from concurrent.futures import ProcessPoolExecutor,FIRST_COMPLETED,wait
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import psutil
import yaml

from .static_multigroup import ROOT,digest,scientific_sources,environment_record,write_json
from .frozen_resume import atomic_json
from .worker_resources import initialize_worker


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def prepare(protocol,study):
    from udc.multigroup.offset_interval import PRECISION_BITS
    spec=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    valid=((spec['protocol']=='local_offset_interval_development_v1' and not spec.get('tighten_normal'))
        or (spec['protocol']=='local_offset_interval_development_v2' and spec.get('tighten_normal') is True))
    if not valid or spec['interval_precision_bits']!=PRECISION_BITS:
        raise ValueError('Explicit local interval development protocol required')
    source=ROOT/spec['source_study']
    if digest(source/'manifest.json')!=read(source/'seal.json')['manifest_sha256']:
        raise ValueError('Source study manifest changed')
    geometry=source/spec['source_geometry']
    receipt=read(geometry.parent/'receipt.json')
    if digest(geometry)!=receipt[geometry.name]:
        raise ValueError('Saved geometry changed')
    with np.load(geometry,allow_pickle=False) as data:
        count=len(data['closing_positive'])
    edges=sorted({int(k*count/spec['uniform_edge_count']) for k in range(spec['uniform_edge_count'])}
        |set(spec['include_targeted_edges']))
    if any(i<0 or i>=count for i in edges+spec['intersection_vertices']):
        raise ValueError('Protocol refers to absent saved vertices')
    cases=[]
    for edge in edges:
        for i,error in enumerate(spec['coordinate_uncertainty_m']):
            cases.append({'case_id':f'offset_{edge:04d}_e{i}','kind':'offset',
                'source_vertex_indices':[edge,(edge+1)%count],'coordinate_uncertainty_m':error})
    cases.extend({'case_id':f'intersection_e{i}','kind':'intersection',
        'source_vertex_indices':spec['intersection_vertices'],'coordinate_uncertainty_m':error}
        for i,error in enumerate(spec['coordinate_uncertainty_m']))
    study=Path(study).resolve();study.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        destination=study/'source_snapshot'/name
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,destination)
    write_json(study/'manifest.json',{'design':spec,'source_geometry':str(geometry),
        'source_geometry_sha256':digest(geometry),'source_manifest_sha256':digest(source/'manifest.json'),
        'cases':cases,'selected_edges':edges,'sources':sources,'environment':environment_record(),
        'selection':'32 evenly indexed old edges plus two posthoc counterexample edges; not random or independent samples',
        'new_independent_samples':0,'new_motion_executions':0,'runtime_certificate_restored':False})
    write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})


def execute(study):
    from udc.analysis.local_interval_diagnostic import worker
    study=Path(study).resolve();m=read(study/'manifest.json')
    if (digest(study/'manifest.json')!=read(study/'seal.json')['manifest_sha256']
            or scientific_sources()!=m['sources'] or environment_record()!=m['environment']):
        raise ValueError('Frozen local arithmetic study changed')
    tasks=iter(m['cases']);active={};results=[];workers=m['design']['workers']
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize_worker) as pool:
        def submit():
            case=next(tasks,None)
            if case is not None:
                active[pool.submit(worker,(str(study),case))]=case['case_id']
        for _ in range(workers):submit()
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                case_id=active.pop(future)
                try:results.append(future.result())
                except Exception as exc:
                    atomic_json(study/'error.json',{'case_id':case_id,'type':type(exc).__name__,'message':str(exc),
                        'classification':'MODEL_BOUND_VIOLATION' if 'MODEL_BOUND_VIOLATION' in str(exc) else 'PROGRAM_ERROR',
                        'scientific_retry':False})
                    for pending in active:pending.cancel()
                    raise
            for _ in range(workers-len(active)):submit()
            atomic_json(study/'status.json',{'phase':'running','completed_cases':len(results),'scheduled_cases':len(m['cases'])})
    summary={'scheduled_cases':len(m['cases']),'completed_cases':len(results),
        'enclosed_local_cases':sum(r['status']=='ENCLOSED_LOCAL_OPERATION' for r in results),
        'uncertified_cases':sum(r['status']=='UNCERTIFIED' for r in results),
        'admissible_zero_length_witnesses':sum(r['admissible_zero_length_witness'] is not None for r in results),
        'sampled_expression_traces':sum(r['trace_count'] for r in results),
        'observed_operation_checks':sum(r['observed_operation_checks'] for r in results),
        'enclosure_violations':sum(r['enclosure_violations'] for r in results),
        'saved_closing_witness_enclosed':any(r['saved_closing_witness_enclosed'] for r in results),
        'new_independent_samples':0,'new_motion_executions':0,'runtime_certificate_restored':False,
        'scope':m['design']['scope'],'goal_complete':False}
    write_json(study/'results.json',results);write_json(study/'summary.json',summary)
    atomic_json(study/'status.json',{'phase':'completed',**summary})


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study)
    current=psutil.Process()
    write_json(study/'process.json',{'pid':current.pid,'create_time':current.create_time(),'exe':current.exe(),'cmdline':current.cmdline()})
    code=("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.local_interval_study import execute;execute(p)")
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT)
        target=psutil.Process(child.pid)
        write_json(study/'worker_process.json',{'pid':target.pid,'create_time':target.create_time(),'exe':target.exe(),'cmdline':target.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',{'exit_code':child.returncode,'scientific_retry':False})
            raise RuntimeError('Local interval batch stopped; inspect preserved error.json and execution.log')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True)
    args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='local_interval_diagnostic')
    else:result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
