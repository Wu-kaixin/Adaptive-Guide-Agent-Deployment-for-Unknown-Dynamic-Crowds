"""Seal an expanded witness search while preserving the first finite search."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import psutil
import yaml

from .static_multigroup import ROOT, digest, scientific_sources, environment_record, write_json
from .frozen_resume import atomic_json
from .worker_resources import initialize_worker


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def prepare(protocol, study):
    study = Path(study).resolve()
    design = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if design['protocol'] != 'directional_feasible_world_development_v2':
        raise ValueError('Explicit directional witness protocol required')
    parent = ROOT / design['parent']
    old = read(parent / 'manifest.json')
    if digest(parent / 'manifest.json') != read(parent / 'seal.json')['manifest_sha256']:
        raise ValueError('Parent manifest changed')
    audit = read(ROOT / design['parent_audit'])
    if not audit['all_match']:
        raise ValueError('Parent witnesses have not passed reconstruction')
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study / 'protocol.yaml')
    sources = scientific_sources()
    for name in sources:
        target = study / 'source_snapshot' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    manifest = {'design': design, 'selected_pairs': old['selected_pairs'], 'original': old['original'],
        'original_manifest_sha256': old['original_manifest_sha256'],
        'parent_manifest_sha256': digest(parent / 'manifest.json'), 'parent_audit_sha256': digest(ROOT / design['parent_audit']),
        'parent_witness_pairs': read(parent / 'summary.json')['witness_pairs'],
        'sources': sources, 'environment': environment_record(), 'new_independent_samples': 0,
        'new_motion_executions': 0, 'source_actions_reoptimized': False,
        'selection': 'same 20 posthoc near-budget qualified old pairs as parent; not representative random sampling'}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})


def execute(study):
    from udc.analysis.directional_world_witness import worker
    study = Path(study).resolve()
    manifest = read(study / 'manifest.json')
    if (digest(study / 'manifest.json') != read(study / 'seal.json')['manifest_sha256']
            or digest(Path(manifest['original']) / 'manifest.json') != manifest['original_manifest_sha256']
            or scientific_sources() != manifest['sources'] or environment_record() != manifest['environment']):
        raise ValueError('Frozen directional witness inputs/sources changed')
    results = []
    with ProcessPoolExecutor(max_workers=manifest['design']['workers'], initializer=initialize_worker) as pool:
        for row in pool.map(worker, [(str(study), index, case) for index, case in enumerate(manifest['selected_pairs'])]):
            results.append(row)
            atomic_json(study / 'status.json', {'phase': 'running', 'completed_pairs': len(results),
                'scheduled_pairs': len(manifest['selected_pairs']), 'verified_witness_pairs': sum(r['best'] is not None for r in results)})
    summary = {'pairs': len(results), 'tested_candidates': sum(r['tested_candidates'] for r in results),
        'admissible_reference_candidates': sum(r['admissible_reference_candidates'] for r in results),
        'witness_candidates': sum(r['witness_candidates'] for r in results),
        'verified_witness_pairs': sum(r['best'] is not None for r in results),
        'parent_witness_pairs': manifest['parent_witness_pairs'],
        'max_witness_integrator_residual': max((r['best_witness_audit']['max_cross_integrator_residual']
            for r in results if r['best_witness_audit']), default=0.),
        'new_independent_samples': 0, 'new_motion_executions': 0,
        'no_witness_is_certificate': False, 'runtime_certificate': False,
        'scope': 'same old observations/layouts; broader admissible static worlds; per-positive-pair best witness independently reconstructed'}
    write_json(study / 'results.json', results)
    write_json(study / 'summary.json', summary)
    atomic_json(study / 'status.json', {'phase': 'completed', **summary})
    return summary


def run(protocol, study):
    study = Path(study).resolve()
    prepare(protocol, study)
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(), 'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.directional_world_study import execute;execute(p)")
    env = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'execution.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=env, stdout=log, stderr=subprocess.STDOUT)
        target = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': child.pid, 'create_time': target.create_time(), 'exe': target.exe(), 'cmdline': target.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Directional witness search stopped; inspect preserved execution.log')
    return read(study / 'summary.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol')
    parser.add_argument('--audit-of')
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    if bool(args.protocol) == bool(args.audit_of):
        parser.error('Exactly one of --protocol and --audit-of is required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result = run_audit(args.audit_of, args.study, auditor='directional_world_audit')
    else:
        result = run(args.protocol, args.study)
    print(json.dumps(result, ensure_ascii=False), flush=True)
