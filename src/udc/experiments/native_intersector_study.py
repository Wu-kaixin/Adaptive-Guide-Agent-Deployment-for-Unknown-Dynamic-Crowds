"""Seal an actual native-segment study on old local traces and fixed fixtures."""
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
    if design['protocol']!='native_intersector_development_v1' or design['geos_version']!='3.14.1' or design['precision_model']!='none':
        raise ValueError('Explicit pinned native intersector protocol required')
    parent=ROOT/design['parent'];old=read(parent/'manifest.json')
    if digest(parent/'manifest.json')!=read(parent/'seal.json')['manifest_sha256']:raise ValueError('Parent changed')
    cases=[]
    for case in old['cases']:
        if case['kind']=='intersection':
            path=parent/'cases'/case['case_id']/'traces.json'
            cases.append({'case_id':'old_'+case['case_id'],'kind':'old_interval_family',
                'parent_case_id':case['case_id'],'traces_sha256':digest(path),
                'coordinate_uncertainty_m':case['coordinate_uncertainty_m']})
    cases.extend({'case_id':'fixture_'+r['name'],'kind':'fixture',**r} for r in design['fixtures'])
    with NativeSegmentIntersector() as native:identity=native.identity
    # Preserve original copyright notices and COPYING with archived source.
    upstream=['include/geos/algorithm/LineIntersector.h','src/algorithm/LineIntersector.cpp',
        'src/geom/LineSegment.cpp','src/geom/PrecisionModel.cpp','capi/geos_ts_c.cpp','capi/geos_c.h.in','COPYING']
    cached={name:ROOT/design['source_cache']/Path(name).name for name in upstream}
    if not all(p.is_file() for p in cached.values()):raise ValueError('Pinned upstream source cache incomplete')
    study=Path(study).resolve();study.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    upstream_records={}
    for name,path in cached.items():
        target=study/'upstream_sources'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        upstream_records[name]={'sha256':digest(target),'url':'https://raw.githubusercontent.com/libgeos/geos/3.14.1/'+name}
    write_json(study/'manifest.json',{'design':design,'parent':str(parent),'parent_manifest_sha256':digest(parent/'manifest.json'),
        'cases':cases,'sources':sources,'environment':environment_record(),'native_binary_identity':identity,
        'upstream_sources':upstream_records,'new_independent_samples':0,'new_motion_executions':0,
        'runtime_certificate_restored':False,'scope':'actual native returned-point enclosure; endpoint boxes are not person-error certificates'})
    write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})


def execute(study):
    from udc.analysis.native_intersector_diagnostic import worker
    study=Path(study).resolve();m=read(study/'manifest.json')
    if digest(study/'manifest.json')!=read(study/'seal.json')['manifest_sha256'] or scientific_sources()!=m['sources'] or environment_record()!=m['environment']:
        raise ValueError('Frozen native study sources/environment changed')
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
        'native_calls':sum(r['completed_native_calls'] for r in results),
        'native_point_outputs':sum(r['native_point_outputs'] for r in results),
        'native_empty_outputs':sum(r['native_empty_outputs'] for r in results),
        'bounded_returned_point_families':sum(r['family_envelope'] is not None for r in results),
        'upstream_uncertified_families':sum(r['family_envelope'] is None for r in results),
        'postcondition_violations':sum(r['postcondition_violations'] for r in results),
        'topology_disagreements':sum(r['topology_disagreements'] for r in results),
        'not_correctly_rounded_exact_point':sum(r['not_correctly_rounded_exact_point'] for r in results),
        'runtime_certificate_restored':False,'new_independent_samples':0,'new_motion_executions':0,'goal_complete':False}
    write_json(study/'results.json',results);write_json(study/'summary.json',summary)
    atomic_json(study/'status.json',{'phase':'completed',**summary})


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()})
    code=("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.native_intersector_study import execute;execute(p)")
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()});child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',{'exit_code':child.returncode,'scientific_retry':False})
            raise RuntimeError('Native study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='native_intersector_diagnostic')
    else:result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
