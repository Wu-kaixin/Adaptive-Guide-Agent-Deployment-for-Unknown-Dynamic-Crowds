"""Freeze a source-aware buffer-premise audit without altering old geometry."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import psutil

from .static_multigroup import ROOT, digest, scientific_sources, environment_record, write_json


def run(study):
    study = Path(study).resolve()
    original = ROOT / 'runs/static_phase_retention_20261027/main_v1'
    study.mkdir(parents=True, exist_ok=False)
    sources = scientific_sources()
    for name in sources:
        target = study / 'source_snapshot' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write_json(study / 'manifest.json', {'original': str(original), 'original_manifest_sha256': digest(original / 'manifest.json'),
        'sources': sources, 'environment': environment_record(), 'evidence': 'read_only_buffer_premise_and_source_family_development',
        'sources_consulted': ['https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/OffsetSegmentGenerator.cpp',
            'https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/OffsetCurveBuilder.cpp'],
        'diagnostic_candidate_turn_ratio': 1.01, 'new_motion_executions': 0, 'new_independent_samples': 0})
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(), 'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys,json;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.static_multigroup import digest;from udc.analysis.buffer_bound_audit import audit;"
        "m=json.loads((p/'manifest.json').read_text(encoding='utf-8'));o=Path(m['original']);"
        "assert digest(o/'manifest.json')==m['original_manifest_sha256'];audit(o,p)")
    env = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'audit.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=env, stdout=log, stderr=subprocess.STDOUT)
        worker = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': child.pid, 'create_time': worker.create_time(), 'exe': worker.exe(), 'cmdline': worker.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Frozen buffer audit stopped; inspect preserved audit.log')
    return json.loads((study / 'summary.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--study', required=True)
    args = p.parse_args()
    print(json.dumps(run(args.study), ensure_ascii=False), flush=True)
