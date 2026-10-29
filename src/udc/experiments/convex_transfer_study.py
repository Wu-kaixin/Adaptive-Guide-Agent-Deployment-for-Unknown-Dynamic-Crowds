"""Freeze and stop-at-counterexample execution of convex support diagnostics."""
import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
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
    from udc.analysis.convex_transfer_audit import DIRECTIONS
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    valid_protocol = ((spec['protocol'] == 'convex_support_transfer_development_v1' and 'interpolation_enclosure' not in spec)
        or (spec['protocol'] == 'convex_support_transfer_development_v2' and spec.get('interpolation_enclosure') == 'rational_binary64_v1')
        or (spec['protocol'] == 'convex_support_transfer_development_v3' and spec.get('interpolation_enclosure') == 'rational_binary64_v1'
            and spec.get('direction_mode') == 'all_source_hull_facets'))
    if (not valid_protocol
            or not spec['stop_on_first_refuted_stage'] or tuple(map(tuple, spec['directions'])) != DIRECTIONS):
        raise ValueError('Explicit frozen stop-at-counterexample protocol required')
    original, study = ROOT / spec['original'], Path(study).resolve()
    old = read(original / 'manifest.json')
    if digest(original / 'manifest.json') != read(original / 'seal.json')['manifest_sha256']:
        raise ValueError('Original manifest changed')
    tasks, truths = [], set()
    for sequence in old['sequences']:
        if sequence['truth_sha256'] not in truths:
            tasks.append({'input': sequence['truth'], 'input_sha256': sequence['truth_sha256'],
                'kind': 'old_truth', 'error_bound_m': 0., 'group_count': sequence['group_count']})
            truths.add(sequence['truth_sha256'])
        for frame in sequence['frames']:
            tasks.append({'input': frame['observation'], 'input_sha256': frame['observation_sha256'],
                'kind': 'old_observation', 'error_bound_m': old['spec']['declared_error_bound_m'],
                'group_count': sequence['group_count']})
    tasks = [row | {'task_id': f'input_{i:04d}'} for i, row in enumerate(tasks)]
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study / 'protocol.yaml')
    sources = scientific_sources()
    for name in sources:
        destination = study / 'source_snapshot' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    write_json(study / 'manifest.json', {'design': spec, 'original': str(original),
        'original_manifest_sha256': digest(original / 'manifest.json'), 'tasks': tasks,
        'scheduled_old_truths': len(truths), 'scheduled_groups': sum(t['group_count'] for t in tasks),
        'sources': sources, 'environment': environment_record(), 'new_independent_samples': 0,
        'new_motion_executions': 0, 'runtime_certification_restored': False,
        'scope': 'exact finite-direction tests of ideal convex support lemmas on actual GEOS stage coordinates'})
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})


def execute(study):
    from udc.analysis.convex_transfer_audit import scan_input
    study = Path(study).resolve()
    manifest = read(study / 'manifest.json')
    if (digest(study / 'manifest.json') != read(study / 'seal.json')['manifest_sha256']
            or scientific_sources() != manifest['sources'] or environment_record() != manifest['environment']
            or digest(Path(manifest['original']) / 'manifest.json') != manifest['original_manifest_sha256']):
        raise ValueError('Frozen support-transfer study changed')
    tasks = iter(manifest['tasks'])
    results, active, stopped, cancelled = [], {}, False, []
    workers = manifest['design']['workers']
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize_worker) as pool:
        def submit_one():
            task = next(tasks, None)
            if task is not None:
                active[pool.submit(scan_input, str(study), task)] = task['task_id']
        for _ in range(workers):
            submit_one()
        while active:
            done, _ = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                task_id = active.pop(future)
                if future.cancelled():
                    cancelled.append(task_id)
                    continue
                try:
                    result = future.result()
                except Exception as exc:
                    atomic_json(study / 'program_error.json', {'task_id': task_id,
                        'error_type': type(exc).__name__, 'message': str(exc), 'scientific_retry': False})
                    for pending in active:
                        pending.cancel()
                    raise
                results.append(result)
                stopped |= result['proposed_enclosure_refuted']
            if stopped:
                for future in active:
                    future.cancel()
            else:
                for _ in range(workers - len(active)):
                    submit_one()
            atomic_json(study / 'status.json', {'phase': 'draining_after_refutation' if stopped else 'running',
                'completed_inputs': len(results), 'scheduled_inputs': len(manifest['tasks']),
                'in_flight': len(active), 'refuted_inputs': sum(r['proposed_enclosure_refuted'] for r in results)})
    checked_groups = sum(r['groups_checked'] for r in results)
    summary = {'status': 'HYPOTHESIS_REFUTED_BATCH_STOPPED' if stopped else 'FINITE_PROBE_COMPLETED',
        'scheduled_inputs': len(manifest['tasks']), 'completed_inputs': len(results),
        'not_executed_inputs': len(manifest['tasks']) - len(results),
        'scheduled_groups': manifest['scheduled_groups'], 'checked_groups': checked_groups,
        'not_checked_groups': manifest['scheduled_groups'] - checked_groups,
        'refuted_inputs': sum(r['proposed_enclosure_refuted'] for r in results),
        'tested_stage_groups': sum(len(r['records']) for r in results),
        'counterexample_stage_groups': sum(bool(t['violations']) for r in results for t in r['records']),
        'stopped_on_first_refutation': stopped, 'cancelled_before_execution': cancelled,
        'runtime_certificate_restored': False, 'new_independent_samples': 0, 'new_motion_executions': 0,
        'scope': manifest['scope'], 'goal_complete': False}
    write_json(study / 'results.json', results)
    write_json(study / 'summary.json', summary)
    atomic_json(study / 'status.json', {'phase': 'completed', **summary})
    return summary


def run(protocol, study):
    study = Path(study).resolve()
    prepare(protocol, study)
    current = psutil.Process()
    write_json(study / 'process.json', {'pid': current.pid, 'create_time': current.create_time(),
        'exe': current.exe(), 'cmdline': current.cmdline()})
    code = ("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.convex_transfer_study import execute;execute(p)")
    env = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'execution.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=env, stdout=log, stderr=subprocess.STDOUT)
        target = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': target.pid, 'create_time': target.create_time(),
            'exe': target.exe(), 'cmdline': target.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Support-transfer audit stopped with program error; inspect preserved execution.log')
    return read(study / 'summary.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol')
    parser.add_argument('--audit-of')
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    if bool(args.protocol)==bool(args.audit_of):
        parser.error('Exactly one of --protocol and --audit-of required')
    if args.audit_of:
        from .feasible_world_study import run_audit
        result=run_audit(args.audit_of,args.study,auditor='convex_transfer_replay')
    else:
        result=run(args.protocol,args.study)
    print(json.dumps(result,ensure_ascii=False),flush=True)
