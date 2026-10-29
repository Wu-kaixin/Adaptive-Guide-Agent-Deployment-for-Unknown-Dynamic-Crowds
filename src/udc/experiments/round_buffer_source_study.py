"""Freeze conditional round-buffer source comparisons on all old stages."""
import argparse
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from fractions import Fraction as F
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
from .universal_precision_study import UPSTREAM
from udc.analysis.convex_transfer_audit import read


def prepare(protocol,study):
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if (design['protocol']!='round_buffer_source_development_v1' or design['raw_floating_box_is_all_precision_contract']
            or design['closing_factors']!=[1,80] or design['unknown_precision_stages']!=2):
        raise ValueError('Explicit conditional round-source protocol required')
    parent=ROOT/design['parent'];old=read(parent/'manifest.json');audit_path=ROOT/design['parent_audit'];audit=read(audit_path)
    if (digest(parent/'manifest.json')!=read(parent/'seal.json')['manifest_sha256'] or not audit['all_match']
            or audit['study_manifest_sha256']!=digest(parent/'manifest.json')):raise ValueError('Old stage parent not verified')
    original=Path(old['original'])
    if digest(original/'manifest.json')!=old['original_manifest_sha256']:raise ValueError('Old stage model binding changed')
    config=read(original/'manifest.json')['base_spec']['model']
    stages=[row|{'distance':float(config[row['distance_field']])*row['sign']} for row in design['stages']]
    study=Path(study).resolve();study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    upstream={}
    paths=sorted(set(UPSTREAM+['src/operation/buffer/BufferInputLineSimplifier.cpp','include/geos/operation/buffer/BufferInputLineSimplifier.h',
        'include/geos/operation/buffer/OffsetSegmentGenerator.h','src/algorithm/Angle.cpp','include/geos/algorithm/Angle.h']))
    for name in paths:
        target=study/'upstream_sources'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/design['source_cache']/Path(name).name,target)
        upstream[name]={'sha256':digest(target),'url':'https://raw.githubusercontent.com/libgeos/geos/3.14.1/'+name}
    receipts={p.relative_to(parent).as_posix():digest(p) for p in sorted((parent/'inputs').glob('*/receipt.json'))}
    if len(receipts)!=len(old['tasks']):raise ValueError('Source input denominator changed')
    write_json(study/'manifest.json',{'design':design,'parent':str(parent),'parent_manifest_sha256':digest(parent/'manifest.json'),
        'parent_audit_sha256':digest(audit_path),'parent_receipts':receipts,'tasks':old['tasks'],'stages':stages,
        'scheduled_groups':old['scheduled_groups'],'scheduled_stage_transfers':old['scheduled_groups']*len(stages),
        'sources':sources,'upstream_sources':upstream,'environment':environment_record(),
        'selection':'all saved old v2 input/group coordinates; repeated old worlds and observations, not new independent samples',
        'source_scope':'manual default round/non-single-sided buffer coordinate flow under finite operations, positive norms, unit-range trig, correctly rounded sqrt and standard copy semantics',
        'new_native_geometry_calls':0,'new_independent_samples':0,'new_motion_executions':0,'runtime_service_gate_changed':False})
    write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})


def execute(study):
    from udc.analysis.round_buffer_source_audit import worker
    study=Path(study);m=read(study/'manifest.json')
    if (digest(study/'manifest.json')!=read(study/'seal.json')['manifest_sha256'] or scientific_sources()!=m['sources']
            or environment_record()!=m['environment']):raise ValueError('Frozen round-source study changed')
    tasks=iter(range(len(m['tasks'])));active={};results=[];workers=m['design']['workers'];stopped=False;cancelled=[]
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
                try:row=future.result()
                except Exception as exc:
                    atomic_json(study/'STOP.json',{'reason':'program_or_model_error','index':index,'scientific_retry':False})
                    atomic_json(study/'error.json',{'index':index,'type':type(exc).__name__,'message':str(exc),'scientific_retry':False})
                    for pending in active:pending.cancel()
                    raise
                results.append(row);stopped|=row['hypothesis_refuted']
            if stopped:
                for pending in active:pending.cancel()
            else:
                for _ in range(workers-len(active)):submit()
            atomic_json(study/'status.json',{'phase':'draining_after_refutation' if stopped else 'running','completed_inputs':len(results),'scheduled_inputs':len(m['tasks'])})
    summary={'status':'HYPOTHESIS_REFUTED_BATCH_STOPPED' if stopped else 'FINITE_SAVED_STAGE_PROBE_COMPLETED',
        'scheduled_inputs':len(m['tasks']),'recorded_inputs':len(results),'not_executed_inputs':len(m['tasks'])-len(results),
        **{k:sum(r[k] for r in results) for k in ['groups_checked','stage_transfers_checked','saved_output_coordinates_checked','raw_floating_diagnostic_counterexamples']},
        'refuted_inputs':sum(r['hypothesis_refuted'] for r in results),'cancelled_before_execution':cancelled,
        'new_native_geometry_calls':0,'new_independent_samples':0,'new_motion_executions':0,
        'runtime_service_gate_changed':False,'all_inputs_defined':False,'whole_geometry_pipeline_proved':False,'goal_complete':False}
    summary['not_checked_stage_transfers']=m['scheduled_stage_transfers']-summary['stage_transfers_checked']
    if not stopped and summary['not_checked_stage_transfers']:raise ValueError('Round-source stage denominator changed')
    write_json(study/'results.json',results);write_json(study/'summary.json',summary)
    probes=[]
    for x in m['design']['weighted_identity_probe_values']:
        y=(80*float(x)+float(x))/81
        probes.append({'input_hex':float(x).hex(),'separate_output_hex':y.hex(),'identity_in_exact_reals':True,
            'outside_input_singleton':y!=x,'exact_difference_fraction':str(F.from_float(y)-F.from_float(x))})
    write_json(study/'weighted_identity_probes.json',probes)


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()})
    code=("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.round_buffer_source_study import execute;execute(p)")
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()});child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',{'exit_code':child.returncode,'scientific_retry':False})
            raise RuntimeError('Round-source study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='round_buffer_source_audit')
    else:result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
