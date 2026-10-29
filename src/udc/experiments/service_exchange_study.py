"""Freeze and dispatch the old-action evaluator exchange diagnostic."""
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
        path = study / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    manifest = {'evidence': 'fixed_old_action_evaluator_development_not_a_certificate',
        'new_independent_samples': 0, 'original': str(original), 'source_manifest_sha256': digest(original / 'manifest.json'),
        'sources': sources, 'environment': environment_record(), 'delta': .002,
        'purpose': 'distinguish gross lost/gained mass from stored original per-group net loss',
        'controller_may_use_truth_oracle_to_accept': False, 'source_actions_reoptimized': False}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys;from pathlib import Path;study=Path(sys.argv[1]);sys.path.insert(0,str(study/'source_snapshot/src'));"
        "from udc.experiments.static_multigroup import digest;from udc.analysis.service_exchange_audit import audit;"
        "import json;manifest=json.loads((study/'manifest.json').read_text(encoding='utf-8'));"
        "assert digest(Path(manifest['original'])/'manifest.json')==manifest['source_manifest_sha256'];"
        "audit(Path(manifest['original']),study)")
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'audit.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=environment, stdout=log, stderr=subprocess.STDOUT)
        worker = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': child.pid, 'create_time': worker.create_time(),
            'exe': worker.exe(), 'cmdline': worker.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Frozen evaluator diagnostic stopped; inspect preserved audit.log')
    return json.loads((study / 'summary.json').read_text(encoding='utf-8'))['all_pairs']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.study), ensure_ascii=False), flush=True)
