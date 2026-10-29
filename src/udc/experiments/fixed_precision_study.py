"""Freeze fixed-scalar and conditional axis-invariance development evidence."""
import argparse
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import psutil
import yaml

from .static_multigroup import ROOT,digest,scientific_sources,environment_record,write_json
from .frozen_resume import atomic_json
from .worker_resources import initialize_worker


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))


def prepare(protocol,study):
    from udc.analysis.native_geos_segment import NativeSegmentIntersector
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if design['protocol']!='fixed_precision_development_v1' or design['naive_half_requested_grid_is_contract']:
        raise ValueError('Explicit precision-contract development protocol required')
    parent=ROOT/design['parent'];old=read(parent/'manifest.json')
    if digest(parent/'manifest.json')!=read(parent/'seal.json')['manifest_sha256']:raise ValueError('Parent manifest changed')
    receipts={str(p.relative_to(parent)):digest(p) for p in sorted((parent/'cases').glob('*/receipt.json'))}
    cases=[]
    for model in design['models']:
        grid=float(model['grid']) if 'grid' in model else 1./float(model['grid_via_scale'])
        for i,error in enumerate(design['coordinate_uncertainty_m']):
            cases.append({'case_id':model['name']+f'_e{i}','model_name':model['name'],'grid':grid,'coordinate_uncertainty_m':error})
    with NativeSegmentIntersector() as native:identity=native.identity
    if identity!=old['native_binary_identity']:raise ValueError('Native binaries differ from exposed parent inputs')
    upstream=['src/geom/PrecisionModel.cpp','include/geos/geom/PrecisionModel.h','src/util/math.cpp','include/geos/util/math.h',
        'src/noding/ScaledNoder.cpp','src/operation/buffer/BufferOp.cpp','include/geos/operation/buffer/BufferOp.h',
        'capi/geos_ts_c.cpp','COPYING']
    cached={n:ROOT/design['source_cache']/Path(n).name for n in upstream}
    if not all(p.is_file() for p in cached.values()):raise ValueError('Pinned source cache incomplete')
    study=Path(study).resolve();study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    records={}
    for name,path in cached.items():
        target=study/'upstream_sources'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        records[name]={'sha256':digest(target),'url':'https://raw.githubusercontent.com/libgeos/geos/3.14.1/'+name}
    write_json(study/'manifest.json',{'design':design,'parent':str(parent),'parent_manifest_sha256':digest(parent/'manifest.json'),
        'parent_receipts':receipts,'native_binary_identity':identity,'cases':cases,'sources':sources,
        'upstream_sources':records,'environment':environment_record(),'new_independent_samples':0,'new_motion_executions':0,
        'runtime_certificate_restored':False,'scope':design['scope']})
    write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})


def execute(study):
    from udc.analysis.fixed_precision_diagnostic import worker
    study=Path(study).resolve();m=read(study/'manifest.json')
    if digest(study/'manifest.json')!=read(study/'seal.json')['manifest_sha256'] or scientific_sources()!=m['sources'] or environment_record()!=m['environment']:
        raise ValueError('Frozen precision study changed')
    results=[];active={};tasks=iter(m['cases']);workers=m['design']['workers']
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize_worker) as pool:
        def submit():
            case=next(tasks,None)
            if case is not None:active[pool.submit(worker,(str(study),case))]=case['case_id']
        for _ in range(workers):submit()
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                name=active.pop(future)
                try:results.append(future.result())
                except Exception as exc:
                    atomic_json(study/'error.json',{'case_id':name,'type':type(exc).__name__,'message':str(exc),'scientific_retry':False})
                    for pending in active:pending.cancel()
                    raise
            for _ in range(workers-len(active)):submit()
            atomic_json(study/'status.json',{'phase':'running','completed_cases':len(results),'scheduled_cases':len(m['cases'])})
    summary={'scheduled_cases':len(m['cases']),'completed_cases':len(results),
        'native_point_calls':sum(r['native_point_calls'] for r in results),
        'coordinate_range_and_drift_checks':sum(r['coordinate_range_and_drift_checks'] for r in results),
        'native_overlay_calls':sum(r['native_overlay_calls'] for r in results),
        'native_overlay_empty':sum(r['overlay_empty'] for r in results),
        'overlay_with_verified_axis_closure':sum(r['overlay_with_verified_axis_closure'] for r in results),
        'overlay_axis_closure_uncertified':sum(r['overlay_axis_closure_uncertified'] for r in results),
        'cases_with_naive_half_grid_counterexample':sum(r['naive_half_grid_counterexample'] is not None for r in results),
        'runtime_certificate_restored':False,'new_independent_samples':0,'new_motion_executions':0,'goal_complete':False}
    write_json(study/'results.json',results);write_json(study/'summary.json',summary)
    atomic_json(study/'status.json',{'phase':'completed',**summary})


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()})
    code=("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.fixed_precision_study import execute;execute(p)")
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()});child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',{'exit_code':child.returncode,'scientific_retry':False})
            raise RuntimeError('Fixed precision study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='fixed_precision_diagnostic')
    else:result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
