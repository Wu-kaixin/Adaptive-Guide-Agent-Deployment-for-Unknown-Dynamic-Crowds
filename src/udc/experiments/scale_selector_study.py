"""Freeze native scalar selector and conditional scale-floor development."""
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
from udc.analysis.convex_transfer_audit import read
from udc.analysis.native_precision_selectors import NativePrecisionSelectors


def prepare(protocol,study):
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'));parent=ROOT/design['parent'];coords=ROOT/design['coordinate_parent']
    m=read(parent/'manifest.json');a=read(ROOT/design['parent_audit'])
    assert design['protocol']=='scale_selector_development_v1' and not design['all_admissible_world_scales_proved']
    assert a['all_match'] and a['study_manifest_sha256']==digest(parent/'manifest.json')
    assert digest(parent/'manifest.json')==read(parent/'seal.json')['manifest_sha256']
    assert digest(coords/'manifest.json')==read(coords/'seal.json')['manifest_sha256']
    study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for n in sources:
        target=study/'source_snapshot'/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,target)
    selected=['src/operation/overlayng/PrecisionUtil.cpp','include/geos/operation/overlayng/PrecisionUtil.h',
        'src/operation/buffer/BufferOp.cpp','include/geos/operation/buffer/BufferOp.h','capi/geos_ts_c.cpp','COPYING']
    upstream={}
    for n in selected:
        source=ROOT/design['source_cache']/n;target=study/'upstream_sources'/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        upstream[n]=dict(sha256=digest(target),url='https://raw.githubusercontent.com/libgeos/geos/3.14.1/'+n)
    with NativePrecisionSelectors() as native:identity=native.identity
    write_json(study/'manifest.json',dict(design=design,parent=str(parent),coordinate_parent=str(coords),tasks=m['tasks'],
        parent_manifest_sha256=digest(parent/'manifest.json'),coordinate_manifest_sha256=digest(coords/'manifest.json'),
        parent_receipts={t['task_id']:digest(parent/'inputs'/t['task_id']/'receipt.json') for t in m['tasks']},
        coordinate_receipts={t['task_id']:digest(coords/'inputs'/t['task_id']/'receipt.json') for t in m['tasks']},
        sources=sources,upstream_sources=upstream,environment=environment_record(),native_identity=identity,
        scheduled_transfers=read(parent/'summary.json')['stage_transfers_checked'],
        all_admissible_world_scales_proved=False,runtime_service_gate_changed=False))
    write_json(study/'seal.json',dict(manifest_sha256=digest(study/'manifest.json')))


def execute(study):
    from udc.analysis.scale_selector_audit import worker,calibrate
    m=read(study/'manifest.json');assert digest(study/'manifest.json')==read(study/'seal.json')['manifest_sha256']
    assert scientific_sources()==m['sources'] and environment_record()==m['environment']
    tasks=iter(range(len(m['tasks'])));active={};rows=[];stopped=False
    with ProcessPoolExecutor(max_workers=m['design']['workers'],initializer=initialize_worker) as pool:
        def submit():
            i=next(tasks,None)
            if i is not None:active[pool.submit(worker,(str(study),i))]=i
        for _ in range(m['design']['workers']):submit()
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                i=active.pop(future)
                if future.cancelled():continue
                try:r=future.result()
                except BaseException:
                    atomic_json(study/'STOP.json',dict(reason='program_or_model_or_native_error',input_index=i,scientific_retry=False))
                    atomic_json(study/'error.json',dict(input_index=i,traceback=traceback.format_exc(),scientific_retry=False))
                    for pending in active:pending.cancel()
                    raise
                rows.append(r);stopped|=r['refuted']
            if stopped:
                for pending in active:pending.cancel()
            else:
                for _ in range(m['design']['workers']-len(active)):submit()
            atomic_json(study/'status.json',dict(completed_inputs=len(rows),scheduled_inputs=len(m['tasks']),stopped=stopped))
    if not stopped:
        try:calibrate(study)
        except BaseException:
            write_json(study/'error.json',dict(phase='calibration',traceback=traceback.format_exc(),scientific_retry=False));raise
    c=read(study/'calibration.json') if not stopped else dict(native_point_creations=0,native_selector_calls=0)
    summary=dict(recorded_inputs=len(rows),scheduled_inputs=len(m['tasks']),not_executed_inputs=len(m['tasks'])-len(rows),
        **{k:sum(r[k] for r in rows) for k in ('stage_transfers','saved_output_coordinates','native_point_creations','native_selector_calls')},
        stopped=stopped,refuted_inputs=sum(r['refuted'] for r in rows),
        minimum_measured_internal_scale=min(r['minimum_scale_floor'] for r in rows),
        width_inflation_range=[min(r['minimum_width_inflation'] for r in rows),max(r['maximum_width_inflation'] for r in rows)],
        calibration_point_creations=c['native_point_creations'],calibration_selector_calls=c['native_selector_calls'],
        calibration_decimal_comment_differences=c.get('decimal_comment_differences'),
        full_native_buffer_or_overlay_calls=0,new_independent_crowd_inputs=0,new_motion_executions=0,
        all_admissible_world_scales_proved=False,original_reference_enclosed=False,service_certificate=False,goal_complete=False)
    summary['not_checked_stage_transfers']=m['scheduled_transfers']-summary['stage_transfers']
    if not stopped:assert summary['not_checked_stage_transfers']==0
    write_json(study/'summary.json',summary)
    write_json(study/'completion.json',dict(completed=not stopped,receipts={r['task_id']:digest(study/'inputs'/r['task_id']/'receipt.json') for r in rows},
        calibration_sha256=digest(study/'calibration.json') if not stopped else None))


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',dict(pid=p.pid,create_time=p.create_time(),exe=p.exe(),cmdline=p.cmdline()))
    code="import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));from udc.experiments.scale_selector_study import execute;execute(p)"
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',dict(pid=p.pid,create_time=p.create_time(),exe=p.exe(),cmdline=p.cmdline()));child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',dict(exit_code=child.returncode,scientific_retry=False));raise RuntimeError('Selector study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one protocol or audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        r=run_audit(args.audit_of,args.study,auditor='scale_selector_audit')
    else:r=run(args.protocol,args.study)
    print(json.dumps(r,ensure_ascii=False),flush=True)
