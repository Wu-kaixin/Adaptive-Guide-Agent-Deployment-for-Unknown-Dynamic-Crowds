"""Freeze observation-only static position boxes on all old feasible worlds."""
import argparse
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
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
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if design['protocol'] not in ['static_person_enclosure_development_v1','static_person_enclosure_development_v2'] or not design['use_all_parent_pairs_and_worlds']:
        raise ValueError('Explicit complete parent population required')
    parent=ROOT/design['parent'];old=read(parent/'manifest.json')
    if digest(parent/'manifest.json')!=read(parent/'seal.json')['manifest_sha256']:raise ValueError('Parent seal changed')
    audit_path=ROOT/design['parent_audit'];audit=read(audit_path)
    if not audit['all_match'] or audit['study_manifest_sha256']!=digest(parent/'manifest.json'):
        raise ValueError('Parent all-world audit not verified')
    receipts={p.relative_to(parent).as_posix():digest(p) for p in sorted((parent/'pairs').rglob('receipt.json'))}
    study=Path(study).resolve();study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    write_json(study/'manifest.json',{'design':design,'parent':str(parent),'parent_manifest_sha256':digest(parent/'manifest.json'),
        'parent_audit_sha256':digest(audit_path),'parent_receipts':receipts,'scheduled_pairs':len(old['selected_pairs']),
        'expected_worlds':audit['worlds_reconstructed'],'expected_witness_candidates':audit['witness_candidates'],
        'sources':sources,'environment':environment_record(),'new_independent_samples':0,'new_motion_executions':0,
        'identity_scope':'stable legacy row indices; retrospective history order, not measured release times',
        'runtime_service_gate_changed':False})
    write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})


def execute(study):
    from udc.analysis.static_person_enclosure_audit import worker
    study=Path(study);m=read(study/'manifest.json')
    if (digest(study/'manifest.json')!=read(study/'seal.json')['manifest_sha256'] or
            scientific_sources()!=m['sources'] or environment_record()!=m['environment']):
        raise ValueError('Frozen person enclosure study changed')
    active={};results=[];tasks=iter(range(m['scheduled_pairs']));workers=m['design']['workers']
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize_worker) as pool:
        def submit():
            index=next(tasks,None)
            if index is not None:active[pool.submit(worker,(str(study),index))]=index
        for _ in range(workers):submit()
        while active:
            done,_=wait(active,return_when=FIRST_COMPLETED)
            for future in done:
                index=active.pop(future)
                try:results.append(future.result())
                except Exception as exc:
                    atomic_json(study/'error.json',{'index':index,'type':type(exc).__name__,'message':str(exc),'scientific_retry':False})
                    for pending in active:pending.cancel()
                    raise
            for _ in range(workers-len(active)):submit()
            atomic_json(study/'status.json',{'phase':'running','completed_pairs':len(results),'scheduled_pairs':m['scheduled_pairs']})
    totals={key:sum(r[key] for r in results) for key in ['people','worlds_checked','person_world_memberships',
        'witness_worlds_preserved','strictly_tighter_person_boxes','propagation_trace_steps',
        'disk_pair_tightening_steps','single_disk_tightening_steps']}
    if totals['worlds_checked']!=m['expected_worlds'] or totals['witness_worlds_preserved']!=m['expected_witness_candidates']:
        raise ValueError('All-world or counterexample denominator changed')
    ratios=[r['relative_width_reduction'] for r in results]
    summary={'scheduled_pairs':m['scheduled_pairs'],'completed_pairs':len(results),**totals,
        'sum_coordinate_width_reduction_range':[min(ratios),max(ratios)],
        'new_independent_samples':0,'new_motion_executions':0,'runtime_service_gate_changed':False,
        'original_reference_enclosed':False,'service_certificate':False,'goal_complete':False}
    write_json(study/'results.json',results);write_json(study/'summary.json',summary)
    atomic_json(study/'status.json',{'phase':'completed',**summary})


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()})
    code=("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.static_person_enclosure_study import execute;execute(p)")
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()});child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',{'exit_code':child.returncode,'scientific_retry':False})
            raise RuntimeError('Static person enclosure study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='static_person_enclosure_audit')
    else:result=run(args.protocol,args.study)
    import json
    print(json.dumps(result,ensure_ascii=False),flush=True)
