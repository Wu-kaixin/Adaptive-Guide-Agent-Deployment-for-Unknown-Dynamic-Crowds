"""Seal a read-only wall-obstruction diagnostic on completed controlled inputs."""
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
    original = ROOT / 'runs/step1_foundation_20261029/population_wall_development_v1'
    if not json.loads((original / 'completion.json').read_text(encoding='utf-8'))['all_physical_audits_match']:
        raise ValueError('Original physical audit did not pass')
    study.mkdir(parents=True, exist_ok=False)
    sources = scientific_sources()
    for name in sources:
        path = study / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    write_json(study / 'manifest.json', {'original': str(original), 'sources': sources, 'environment': environment_record(),
        'parent_hashes': {n: digest(original / n) for n in ('manifest.json', 'completion.json', 'results.json')},
        'evidence': 'read_only_geometric_necessary_condition_development', 'new_independent_samples': 0, 'new_motion_executions': 0})
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(), 'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys,json;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.static_multigroup import digest;from udc.analysis.wall_service_bound import audit;"
        "m=json.loads((p/'manifest.json').read_text(encoding='utf-8'));o=Path(m['original']);"
        "assert all(digest(o/n)==s for n,s in m['parent_hashes'].items());"
        "old=json.loads((o/'manifest.json').read_text(encoding='utf-8'));"
        "assert all(digest(p/'source_snapshot'/n)==s for n,s in old['sources'].items());audit(o,p)")
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'audit.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=environment, stdout=log, stderr=subprocess.STDOUT)
        worker = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': child.pid, 'create_time': worker.create_time(), 'exe': worker.exe(), 'cmdline': worker.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Frozen wall diagnostic stopped; inspect saved audit.log')
    return json.loads((study / 'summary.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.study), ensure_ascii=False), flush=True)
