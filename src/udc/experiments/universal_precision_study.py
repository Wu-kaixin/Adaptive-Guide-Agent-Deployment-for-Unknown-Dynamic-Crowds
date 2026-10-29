"""Freeze scale-independent scalar and initial Point-buffer source analysis."""
import argparse
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
from udc.analysis.convex_transfer_audit import read


UPSTREAM=[
    'src/geom/PrecisionModel.cpp','src/util/math.cpp','src/operation/buffer/BufferOp.cpp',
    'src/operation/buffer/BufferBuilder.cpp','src/operation/buffer/OffsetSegmentGenerator.cpp',
    'include/geos/operation/buffer/OffsetSegmentString.h','src/operation/buffer/OffsetCurveBuilder.cpp',
    'src/operation/buffer/BufferCurveSetBuilder.cpp','src/noding/ScaledNoder.cpp','include/geos/noding/ScaledNoder.h',
    'src/noding/snapround/SnapRoundingNoder.cpp','src/noding/snapround/SnapRoundingIntersectionAdder.cpp',
    'include/geos/noding/snapround/SnapRoundingIntersectionAdder.h','src/noding/snapround/HotPixelIndex.cpp',
    'include/geos/noding/snapround/HotPixelIndex.h','src/noding/snapround/HotPixel.cpp','include/geos/noding/snapround/HotPixel.h',
    'src/noding/IntersectionAdder.cpp','src/noding/NodedSegmentString.cpp','include/geos/noding/NodedSegmentString.h',
    'src/noding/SegmentNodeList.cpp','include/geos/noding/SegmentNodeList.h','include/geos/noding/SegmentNode.h',
    'src/geomgraph/EdgeRing.cpp','src/geomgraph/PlanarGraph.cpp','src/operation/buffer/PolygonBuilder.cpp',
    'src/operation/buffer/MaximalEdgeRing.cpp','src/operation/buffer/MinimalEdgeRing.cpp',
    'src/operation/valid/RepeatedPointRemover.cpp','src/geom/GeometryFactory.cpp','include/geos/geom/GeometryFactory.h',
    'include/geos/operation/buffer/BufferParameters.h','capi/geos_ts_c.cpp','COPYING']


def prepare(protocol,study):
    from udc.analysis.native_geos_segment import NativeSegmentIntersector
    design=yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if design['protocol']!='universal_precision_development_v1' or design['expected_domain_refusal_is_native_failure']:
        raise ValueError('Explicit universal precision protocol required')
    parent=ROOT/design['parent'];old=read(parent/'manifest.json');audit_path=ROOT/design['parent_audit'];audit=read(audit_path)
    if (digest(parent/'manifest.json')!=read(parent/'seal.json')['manifest_sha256'] or not audit['all_match']
            or audit['study_manifest_sha256']!=digest(parent/'manifest.json')):raise ValueError('Parent circle audit changed')
    cases=[{'case_id':f'path_{i:02d}_x_{j:02d}','grids':[float(v) for v in path],'x_hex':x}
        for i,path in enumerate(design['grid_paths']) for j,x in enumerate(design['input_x_hex'])]
    study=Path(study).resolve();study.mkdir(parents=True,exist_ok=False);shutil.copyfile(protocol,study/'protocol.yaml')
    sources=scientific_sources()
    for name in sources:
        target=study/'source_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    upstream={}
    for name in UPSTREAM:
        target=study/'upstream_sources'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/design['source_cache']/Path(name).name,target)
        upstream[name]={'sha256':digest(target),'url':'https://raw.githubusercontent.com/libgeos/geos/3.14.1/'+name}
    with NativeSegmentIntersector() as native:identity=native.identity
    write_json(study/'manifest.json',{'design':design,'cases':cases,'parent':str(parent),
        'parent_manifest_sha256':digest(parent/'manifest.json'),'parent_audit_sha256':digest(audit_path),
        'parent_receipts':{p.relative_to(parent).as_posix():digest(p) for p in sorted((parent/'cases').glob('*/receipt.json'))},
        'sources':sources,'upstream_sources':upstream,'environment':environment_record(),'native_binary_identity':identity,
        'source_review_scope':'manual coordinate-production review of default round, non-single-sided FLOATING Point.buffer; conditional basic arithmetic, trig range and copy/storage contracts; not binary formal verification',
        'new_independent_samples':0,'new_motion_executions':0,'runtime_service_gate_changed':False})
    write_json(study/'seal.json',{'manifest_sha256':digest(study/'manifest.json')})


def execute(study):
    from udc.analysis.universal_precision_audit import execute as calculate
    study=Path(study);m=read(study/'manifest.json')
    if (digest(study/'manifest.json')!=read(study/'seal.json')['manifest_sha256'] or scientific_sources()!=m['sources']
            or environment_record()!=m['environment']):raise ValueError('Universal precision snapshot changed')
    try:calculate(study)
    except Exception as exc:
        atomic_json(study/'error.json',{'type':type(exc).__name__,'message':str(exc),'scientific_retry':False});raise


def run(protocol,study):
    study=Path(study).resolve();prepare(protocol,study);p=psutil.Process()
    write_json(study/'process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()})
    code=("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.universal_precision_study import execute;execute(p)")
    env=os.environ|{'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'}
    with (study/'execution.log').open('x',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-c',code,str(study)],env=env,stdout=log,stderr=subprocess.STDOUT);p=psutil.Process(child.pid)
        write_json(study/'worker_process.json',{'pid':p.pid,'create_time':p.create_time(),'exe':p.exe(),'cmdline':p.cmdline()});child.wait()
        if child.returncode:
            write_json(study/'orchestration_error.json',{'exit_code':child.returncode,'scientific_retry':False})
            raise RuntimeError('Universal precision study stopped; inspect preserved artifacts')
    return read(study/'summary.json')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--audit-of');parser.add_argument('--study',required=True);args=parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='universal_precision_audit')
    else:result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
