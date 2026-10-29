"""Seal initial-circle source transfer probes on fixed old allowed worlds."""
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
from udc.analysis.convex_transfer_audit import read


def prepare(protocol,study):
    from udc.analysis.native_geos_segment import NativeSegmentIntersector
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if design['protocol']!='circle_source_development_v1' or design['whole_buffer_transfer_proved']:
        raise ValueError('Explicit finite initial-circle transfer protocol required')
    person=ROOT/design['person_study'];pm=read(person/'manifest.json');audit_path=ROOT/design['person_audit'];audit=read(audit_path)
    if (digest(person/'manifest.json')!=read(person/'seal.json')['manifest_sha256'] or not audit['all_match']
            or audit['study_manifest_sha256']!=digest(person/'manifest.json')):
        raise ValueError('Person enclosure parent not verified')
    upstream=['src/operation/buffer/OffsetSegmentGenerator.cpp','include/geos/operation/buffer/OffsetSegmentGenerator.h',
        'src/operation/buffer/OffsetCurveBuilder.cpp','include/geos/operation/buffer/OffsetSegmentString.h',
        'src/operation/buffer/BufferCurveSetBuilder.cpp','src/operation/buffer/BufferBuilder.cpp',
        'src/algorithm/Angle.cpp','include/geos/algorithm/Angle.h','src/operation/buffer/BufferOp.cpp','COPYING']
    study=Path(study).resolve();study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    records={}
    for name in upstream:
        target=study/'upstream_sources'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/design['source_cache']/Path(name).name,target)
        records[name]={'sha256':digest(target),'url':'https://raw.githubusercontent.com/libgeos/geos/3.14.1/'+name}
    with NativeSegmentIntersector() as native:identity=native.identity
    write_json(study/'manifest.json',{'design':design,'person_study':str(person),'person_manifest_sha256':digest(person/'manifest.json'),
        'person_audit_sha256':digest(audit_path),'person_receipts':{p.relative_to(person).as_posix():digest(p) for p in sorted((person/'cases').glob('*/receipt.json'))},
        'parent':pm['parent'],'parent_manifest_sha256':pm['parent_manifest_sha256'],'parent_receipts':pm['parent_receipts'],
        'scheduled_pairs':pm['scheduled_pairs'],'scheduled_worlds':audit['worlds_checked'],
        'scheduled_person_world_memberships':audit['person_world_memberships'],'expected_witness_worlds':audit['witness_worlds_preserved'],
        'sources':sources,'upstream_sources':records,'environment':environment_record(),'native_binary_identity':identity,
        'trig_range_assumption':'finite sinCosSnap components in [-1,1]; no correctly-rounded transcendental theorem claimed',
        'source_capture':'execute actual Python outer_disk radius expression with process-local Point interception; no native call during capture',
        'scope':'conditional raw arc source box, separately tested against complete initial Point.buffer outputs; no all-world buffer transfer or service proof',
        'new_independent_samples':0,'new_motion_executions':0,'runtime_service_gate_changed':False})
    write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})


def execute(study):
    from udc.analysis.circle_source_audit import worker
    study=Path(study);m=read(study/'manifest.json')
    if (digest(study/'manifest.json')!=read(study/'seal.json')['manifest_sha256'] or scientific_sources()!=m['sources']
            or environment_record()!=m['environment']):raise ValueError('Frozen circle source study changed')
    active={};results=[];tasks=iter(range(m['scheduled_pairs']));workers=m['design']['workers'];stopped=False;cancelled=[]
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize_worker) as pool:
        def submit():
            index=next(tasks,None)
            if index is not None:active[pool.submit(worker,(str(study),index))]=index
        for _ in range(workers):submit()
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                index=active.pop(future)
                if future.cancelled():cancelled.append(index);continue
                try:result=future.result()
                except Exception as exc:
                    atomic_json(study/'STOP.json',{'reason':'program_or_model_error','index':index,'type':type(exc).__name__,'message':str(exc),'scientific_retry':False})
                    atomic_json(study/'error.json',{'index':index,'type':type(exc).__name__,'message':str(exc),'scientific_retry':False})
                    for pending in active:pending.cancel()
                    raise
                results.append(result);stopped|=result['hypothesis_refuted']
            if stopped:
                for pending in active:pending.cancel()
            else:
                for _ in range(workers-len(active)):submit()
            atomic_json(study/'status.json',{'phase':'draining_after_refutation' if stopped else 'running',
                'completed_cases':len(results),'scheduled_cases':m['scheduled_pairs'],'in_flight':len(active)})
    totals={key:sum(r[key] for r in results) for key in ['worlds_checked','person_world_memberships','native_circle_calls',
        'stored_circle_coordinate_pairs','witness_worlds_preserved','captured_radius_expressions']}
    summary={'status':'HYPOTHESIS_REFUTED_BATCH_STOPPED' if stopped else 'FINITE_NATIVE_PROBE_COMPLETED',
        'scheduled_cases':m['scheduled_pairs'],'recorded_cases':len(results),'not_executed_cases':m['scheduled_pairs']-len(results),
        **totals,'not_checked_worlds':m['scheduled_worlds']-totals['worlds_checked'],
        'not_checked_person_world_memberships':m['scheduled_person_world_memberships']-totals['person_world_memberships'],
        'refuted_cases':sum(r['hypothesis_refuted'] for r in results),'cancelled_before_execution':cancelled,
        'whole_buffer_transfer_proved':False,'runtime_service_gate_changed':False,'new_independent_samples':0,'new_motion_executions':0,'goal_complete':False}
    if not stopped and (summary['not_checked_worlds'] or summary['not_checked_person_world_memberships']
            or totals['witness_worlds_preserved']!=m['expected_witness_worlds']):raise ValueError('Finite probe denominator changed')
    write_json(study/'results.json',results);write_json(study/'summary.json',summary)
    atomic_json(study/'status.json',{'phase':'completed',**summary})


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()})
    code=("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.circle_source_study import execute;execute(p)")
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()});child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',{'exit_code':child.returncode,'scientific_retry':False})
            raise RuntimeError('Circle source study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='circle_source_audit')
    else:result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
