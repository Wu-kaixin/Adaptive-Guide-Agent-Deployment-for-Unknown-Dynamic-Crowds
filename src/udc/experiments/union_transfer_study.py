"""Freeze old-input conditional union and resampling coordinate checks."""
import argparse
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

import psutil
import yaml

from .static_multigroup import ROOT,digest,scientific_sources,environment_record,write_json
from .worker_resources import initialize_worker
from .frozen_resume import atomic_json
from .universal_precision_study import UPSTREAM
from udc.analysis.convex_transfer_audit import read


def prepare(protocol,study):
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'));parent=ROOT/design['parent']
    old=read(parent/'manifest.json');audit=read(ROOT/design['parent_audit'])
    assert design['protocol'] in ('union_transfer_development_v1','union_transfer_development_v2') and not design['runtime_service_gate_changed']
    assert design.get('binary_quantizer_limit',2) in (1,2)
    assert audit['all_match'] and audit['study_manifest_sha256']==digest(parent/'manifest.json')
    assert digest(parent/'manifest.json')==read(parent/'seal.json')['manifest_sha256']
    original=Path(old['original']);assert digest(original/'manifest.json')==old['original_manifest_sha256']
    model=read(original/'manifest.json')['base_spec']['model']
    study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    inventory=read(ROOT/design['source_inventory']);upstream={}
    # Preserve full upstream paths: buffer and overlayng have a same-name
    # PolygonBuilder.cpp, which cannot share a flattened cache binding.
    locations={name:ROOT/'.tmp/geos_3141_sources'/Path(name).name for name in UPSTREAM}
    for row in inventory:
        assert row['status']==200 and digest(ROOT/row['local'])==row['sha256']
        locations[row['source']]=ROOT/row['local']
    for name,source in locations.items():
        target=study/'upstream_sources'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        upstream[name]=dict(sha256=digest(target),url='https://raw.githubusercontent.com/libgeos/geos/3.14.1/'+name)
    receipts={t['task_id']:digest(parent/'inputs'/t['task_id']/'receipt.json') for t in old['tasks']}
    write_json(study/'manifest.json',dict(design=design,parent=str(parent),parent_manifest_sha256=digest(parent/'manifest.json'),
        original=str(original),original_manifest_sha256=old['original_manifest_sha256'],model=model,tasks=old['tasks'],
        parent_receipts=receipts,scheduled_groups=old['scheduled_groups'],sources=sources,upstream_sources=upstream,
        source_inventory_sha256=digest(ROOT/design['source_inventory']),environment=environment_record(),
        component_limit_proved=False,all_inputs_defined=False,original_reference_enclosed=False,service_certificate=False))
    write_json(study/'seal.json',dict(manifest_sha256=digest(study/'manifest.json')))


def execute(study):
    from udc.analysis.union_transfer_audit import worker
    m=read(study/'manifest.json');assert digest(study/'manifest.json')==read(study/'seal.json')['manifest_sha256']
    assert scientific_sources()==m['sources'] and environment_record()==m['environment']
    tasks=iter(range(len(m['tasks'])));active={};results=[];stopped=False
    with ProcessPoolExecutor(max_workers=m['design']['workers'],initializer=initialize_worker) as pool:
        def submit():
            index=next(tasks,None)
            if index is not None:active[pool.submit(worker,(str(study),index))]=index
        for _ in range(m['design']['workers']):submit()
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                index=active.pop(future)
                if future.cancelled():continue
                try:result=future.result()
                except BaseException:
                    atomic_json(study/'STOP.json',dict(reason='program_or_model_error',index=index,scientific_retry=False))
                    atomic_json(study/'error.json',dict(index=index,traceback=traceback.format_exc(),scientific_retry=False))
                    for pending in active:pending.cancel()
                    raise
                results.append(result);stopped|=result['refuted']
            if stopped:
                for pending in active:pending.cancel()
            else:
                for _ in range(m['design']['workers']-len(active)):submit()
            atomic_json(study/'status.json',dict(completed_inputs=len(results),scheduled_inputs=len(m['tasks']),stopped=stopped))
    summary=dict(recorded_inputs=len(results),scheduled_inputs=len(m['tasks']),not_executed_inputs=len(m['tasks'])-len(results),
        **{k:sum(r[k] for r in results) for k in ('groups_checked','disks_coordinates_checked','reference_coordinates_checked')},
        refuted_inputs=sum(r['refuted'] for r in results),stopped=stopped,
        union_width_inflation_range=[min(r['union_width_inflation_vs_raw_source'] for r in results),max(r['union_width_inflation_vs_raw_source'] for r in results)],
        new_native_geometry_calls=0,new_independent_samples=0,new_motion_executions=0,
        component_limit_proved=False,original_reference_enclosed=False,service_certificate=False,goal_complete=False)
    summary['not_checked_groups']=m['scheduled_groups']-summary['groups_checked']
    if not stopped:assert summary['not_checked_groups']==0
    write_json(study/'summary.json',summary)
    write_json(study/'completion.json',dict(completed=not stopped,receipts={r['task_id']:digest(study/'inputs'/r['task_id']/'receipt.json') for r in results}))


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',dict(pid=p.pid,create_time=p.create_time(),exe=p.exe(),cmdline=p.cmdline()))
    code="import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));from udc.experiments.union_transfer_study import execute;execute(p)"
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',dict(pid=p.pid,create_time=p.create_time(),exe=p.exe(),cmdline=p.cmdline()));child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',dict(exit_code=child.returncode,scientific_retry=False));raise RuntimeError('Union study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one protocol or audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='union_transfer_audit')
    else:result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
