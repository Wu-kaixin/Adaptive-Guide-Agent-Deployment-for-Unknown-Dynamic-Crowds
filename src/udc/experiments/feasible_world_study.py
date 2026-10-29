"""Seal a posthoc old-action witness search before alternate-world outcomes."""
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
    diagnostic = ROOT / 'runs/step1_foundation_20261029/service_exchange_development_v1/pairs.json'
    rows = json.loads(diagnostic.read_text(encoding='utf-8'))
    selected = sorted([row for row in rows if row['perturbation_m'] > 0
        and row.get('max_group_net_loss', 1) <= .002
        and row.get('directed_layout_displacement_m', 0) > 1e-8],
        key=lambda row: (.002 - row['max_group_net_loss'], row['sequence_id'], row['perturbation_m']))[:20]
    study.mkdir(parents=True, exist_ok=False)
    sources = scientific_sources()
    for name in sources:
        path = study / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    manifest = {'evidence': 'posthoc_fixed_old_action_feasible_world_development', 'original': str(original),
        'original_manifest_sha256': digest(original / 'manifest.json'), 'diagnostic': str(diagnostic),
        'diagnostic_sha256': digest(diagnostic), 'sources': sources, 'environment': environment_record(),
        'selection': '20 nearest-budget net-qualified nonidentical nonzero-perturbation old pairs; posthoc development, not random sampling',
        'selected_pairs': [{k: row[k] for k in ('sequence_id', 'seed', 'perturbation_m', 'max_group_net_loss')} for row in selected],
        'directions': 16, 'scales': [.5, 1.], 'translation_radius': '.9 * (epsilon - max_error_over_all_four_saved_observations)',
        'delta': .002, 'witness_margin': .00001, 'new_independent_samples': 0,
        'source_actions_reoptimized': False, 'new_motion_executions': 0,
        'truth_usage': 'evaluator counterfactuals only; NEVER used by runtime acceptance'}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.analysis.feasible_world_witness import search;search(p)")
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'search.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=environment, stdout=log, stderr=subprocess.STDOUT)
        worker = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': child.pid, 'create_time': worker.create_time(),
            'exe': worker.exe(), 'cmdline': worker.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Frozen witness study stopped; inspect preserved evidence and search.log')
    return json.loads((study / 'summary.json').read_text(encoding='utf-8'))


def run_audit(original_study, audit_study, auditor='feasible_world_audit'):
    if auditor not in ('feasible_world_audit', 'directional_world_audit', 'convex_transfer_replay', 'local_interval_diagnostic','native_intersector_diagnostic','fixed_precision_diagnostic','static_person_enclosure_audit','circle_source_audit','universal_precision_audit','finite_offset_audit','round_buffer_source_audit','union_transfer_audit','scale_selector_audit'):
        raise ValueError('Unknown feasible-world audit protocol')
    original_study, audit_study = Path(original_study).resolve(), Path(audit_study).resolve()
    audit_study.mkdir(parents=True, exist_ok=False)
    sources = scientific_sources()
    for name in sources:
        path = audit_study / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    write_json(audit_study / 'manifest.json', {'study': str(original_study),
        'study_manifest_sha256': digest(original_study / 'manifest.json'),
        'sources': sources, 'environment': environment_record(),
        'auditor': auditor,
        'scope': 'read-only reconstruction; separate audit snapshot; no source study modification'})
    write_json(audit_study / 'seal.json', {'manifest_sha256': digest(audit_study / 'manifest.json')})
    process = psutil.Process()
    write_json(audit_study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        f"from udc.analysis.feasible_world_witness import read,checked;from udc.analysis.{auditor} import audit;"
        "m=read(p/'manifest.json');checked(p/'manifest.json',read(p/'seal.json')['manifest_sha256']);"
        "[checked(p/'source_snapshot'/n,s) for n,s in m['sources'].items()];"
        "checked(Path(m['study'])/'manifest.json',m['study_manifest_sha256']);audit(m['study'],p/'results')")
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (audit_study / 'audit.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(audit_study)], env=environment, stdout=log, stderr=subprocess.STDOUT)
        worker = psutil.Process(child.pid)
        write_json(audit_study / 'worker_process.json', {'pid': child.pid, 'create_time': worker.create_time(),
            'exe': worker.exe(), 'cmdline': worker.cmdline()})
        child.wait()
        if child.returncode:
            write_json(audit_study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Read-only reconstruction stopped; inspect preserved audit.log')
    return json.loads((audit_study / 'results/audit.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    parser.add_argument('--audit-of')
    args = parser.parse_args()
    print(json.dumps(run_audit(args.audit_of, args.study) if args.audit_of else run(args.study), ensure_ascii=False), flush=True)
