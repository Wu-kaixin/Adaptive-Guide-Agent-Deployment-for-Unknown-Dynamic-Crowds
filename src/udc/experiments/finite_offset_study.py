"""Freeze finite-only offset contracts without upgrading old domain refusals."""
import argparse
import json
import math
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
from udc.analysis.convex_transfer_audit import read


def prepare(protocol,study):
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if design['protocol']!='finite_offset_development_v1' or not design['include_all_parent_offset_cases']:
        raise ValueError('Explicit finite-only offset development protocol required')
    parent=ROOT/design['parent'];old=read(parent/'manifest.json');audit_path=ROOT/design['parent_audit'];audit=read(audit_path)
    if (digest(parent/'manifest.json')!=read(parent/'seal.json')['manifest_sha256'] or not audit['all_match']
            or audit['study_manifest_sha256']!=digest(parent/'manifest.json')):raise ValueError('Old offset parent not verified')
    cases=[];receipts={}
    for source in old['cases']:
        if source['kind']!='offset':continue
        folder=parent/'cases'/source['case_id'];receipts[f'cases/{source["case_id"]}/receipt.json']=digest(folder/'receipt.json')
        for name,h in read(folder/'receipt.json').items():
            if digest(folder/name)!=h:raise ValueError('Parent local arithmetic payload changed')
        with np.load(folder/'input.npz',allow_pickle=False) as data:points=data['points'].tolist()
        result=read(folder/'result.json');traces=read(folder/'traces.json')
        cases.append({'case_id':source['case_id'],'kind':'parent_offset','parent_case_id':source['case_id'],
            'center_points':points,'coordinate_uncertainty_m':source['coordinate_uncertainty_m'],
            'input_samples':[r['input_points'] for r in traces],'parent_full_family_status':result['status']})
        if design['include_parent_zero_length_witnesses'] and result['admissible_zero_length_witness'] is not None:
            p=result['admissible_zero_length_witness'];cases.append({'case_id':source['case_id']+'_zero_witness',
                'kind':'parent_zero_witness','parent_case_id':source['case_id'],'center_points':[p,p],
                'coordinate_uncertainty_m':0.,'input_samples':[[p,p]],'parent_full_family_status':result['status']})
    for exponent in design['fixture_edge_exponents']:
        points=[[0.,0.],[math.ldexp(1.,exponent),0.]]
        cases.append({'case_id':f'fixture_exp_{exponent}','kind':'arithmetic_fixture','center_points':points,
            'coordinate_uncertainty_m':0.,'input_samples':[points]})
    if design['include_tiny_unit_normal_refutation_fixture']:
        x=math.nextafter(math.sqrt(1.5)*math.ldexp(1.,-537),0.);points=[[0.,0.],[x,0.]]
        cases.append({'case_id':'fixture_tiny_unit_normal_refutation','kind':'arithmetic_fixture',
            'center_points':points,'coordinate_uncertainty_m':0.,'input_samples':[points]})
    if design['include_coincident_fixture']:
        points=[[1.,1.],[1.,1.]];cases.append({'case_id':'fixture_coincident','kind':'arithmetic_fixture',
            'center_points':points,'coordinate_uncertainty_m':0.,'input_samples':[points]})
    study=Path(study).resolve();study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    upstream={}
    for name in ['src/operation/buffer/OffsetSegmentGenerator.cpp','include/geos/operation/buffer/OffsetSegmentGenerator.h','COPYING']:
        target=study/'upstream_sources'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/design['source_cache']/Path(name).name,target)
        upstream[name]={'sha256':digest(target),'url':'https://raw.githubusercontent.com/libgeos/geos/3.14.1/'+name}
    write_json(study/'manifest.json',{'design':design,'cases':cases,'parent':str(parent),'parent_manifest_sha256':digest(parent/'manifest.json'),
        'parent_audit_sha256':digest(audit_path),'parent_receipts':receipts,'sources':sources,'upstream_sources':upstream,
        'environment':environment_record(),'scope':'conditional finite outputs of local source expression; no whole-family domain upgrade, native C++ trace, original reference or service certificate',
        'fixture_selection':'deterministic arithmetic stress including a posthoc unit-normal refutation fixture; not independent crowd inputs',
        'new_native_geometry_calls':0,'new_independent_samples':0,'new_motion_executions':0,'runtime_service_gate_changed':False})
    write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})


def execute(study):
    from udc.analysis.finite_offset_audit import execute as calculate
    study=Path(study);m=read(study/'manifest.json')
    if (digest(study/'manifest.json')!=read(study/'seal.json')['manifest_sha256'] or scientific_sources()!=m['sources']
            or environment_record()!=m['environment']):raise ValueError('Frozen finite-offset study changed')
    try:calculate(study)
    except Exception as exc:
        atomic_json(study/'error.json',{'type':type(exc).__name__,'message':str(exc),'scientific_retry':False});raise


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()})
    code=("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.finite_offset_study import execute;execute(p)")
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()});child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',{'exit_code':child.returncode,'scientific_retry':False})
            raise RuntimeError('Finite-offset study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='finite_offset_audit')
    else:result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
