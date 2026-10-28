"""Resume missing frozen units with an explicit user-authorized runtime amendment.

Only dispatch changes. Each child imports the original source snapshot and calls
its original unit worker. Completed artifacts are checked and never rewritten.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
STOP_STATUSES = {'EXPERIMENT_ERROR', 'SAFETY_VIOLATION'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.025)


def read_ledger(study, manifest, verify_hashes=True):
    records, receipt_hashes, incomplete = {}, {}, []
    for task in manifest['tasks']:
        folder = study / 'results' / task['task_id']
        if Path(task['task_id']).name != task['task_id'] or folder.is_symlink():
            raise ValueError('Unsafe task identity or result path')
        receipt_path = folder / 'receipt.json'
        if not receipt_path.is_file():
            if folder.exists():
                incomplete.append(task['task_id'])
            continue
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if verify_hashes:
            for name, expected in receipt.items():
                path = folder / name
                if Path(name).name != name or path.is_symlink() or digest(path) != expected:
                    raise ValueError(f'Completed artifact hash mismatch: {task["task_id"]}/{name}')
        row = json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))
        if row['task_id'] != task['task_id']:
            raise ValueError('Task identity mismatch')
        records[task['task_id']] = row
        receipt_hashes[task['task_id']] = digest(receipt_path)
    return records, receipt_hashes, incomplete


def pending_units(manifest, completed):
    grouped = defaultdict(list)
    for task in manifest['tasks']:
        grouped[task['unit_id']].append(task)
    return [(unit, tasks) for unit, tasks in grouped.items()
            if any(task['task_id'] not in completed for task in tasks)]


def validate_amendment(study, amendment, workers):
    if amendment['manifest_sha256'] != digest(study / 'manifest.json'):
        raise ValueError('Runtime amendment binds a different manifest')
    if workers < 1 or workers != amendment['new_workers']:
        raise ValueError('Workers do not match the authorized runtime amendment')
    if not amendment.get('user_authorized_all_cores') or amendment['numerical_threads_per_worker'] != 1:
        raise ValueError('Missing all-core authorization or invalid numerical thread cap')


def run_unit(study, unit, runtime, backend_policy=None):
    log = runtime / 'units' / (unit + '.json')
    error = log.with_suffix('.stderr.log')
    entrypoint = ROOT / 'scripts/frozen_unit_worker.py'
    if backend_policy:
        policy = json.loads(Path(backend_policy).read_text(encoding='utf-8'))
        entrypoint = Path(policy['worker_entrypoint']).resolve()
        if digest(entrypoint) != policy['worker_entrypoint_sha256']:
            raise ValueError('Frozen worker entrypoint changed')
    command = [sys.executable, '-u', str(entrypoint),
               '--study', str(study), '--unit', unit]
    if backend_policy:
        command += ['--backend-policy', str(backend_policy), '--backend-receipt',
                    str(runtime / 'units' / (unit + '.backend.json'))]
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        environment[name] = '1'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with log.open('wb') as stdout, error.open('wb') as stderr:
        process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
                                   creationflags=flags)
        atomic_json(log.with_suffix('.process.json'), {'pid': process.pid, 'unit_id': unit,
                    'started_at': datetime.now().astimezone().isoformat(timespec='seconds')})
        code = process.wait()
    if code:
        raise RuntimeError(f'Frozen unit {unit} exited {code}; inspect {error}')
    return json.loads(log.read_text(encoding='utf-8'))


def finalize_frozen(study, records, stopped):
    # This short subprocess also imports the snapshot, avoiding current analysis code.
    code = ("import json,sys; from pathlib import Path; "
            "from udc.experiments.static_multigroup import summarize,write_json; "
            "from udc.analysis.static_multigroup import primary_analysis; "
            "p=Path(sys.argv[1]); m=json.loads((p/'manifest.json').read_text(encoding='utf-8')); "
            "r=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')); "
            "s=summarize(m,r); s['primary_analysis']=primary_analysis(m,r); "
            "s['stopped_on_error_or_safety']=sys.argv[3]=='true'; write_json(p/'summary.json',s)")
    environment = os.environ.copy()
    environment['PYTHONPATH'] = str(study / 'source_snapshot/src')
    subprocess.run([sys.executable, '-c', code, str(study), str(records), str(stopped).lower()],
                   env=environment, cwd=ROOT, check=True)


def run(study, runtime, amendment_path, workers, backend_policy=None):
    study, runtime = Path(study).resolve(), Path(runtime).resolve()
    amendment_path = Path(amendment_path).resolve()
    amendment = json.loads(amendment_path.read_text(encoding='utf-8'))
    validate_amendment(study, amendment, workers)
    if backend_policy:
        backend_policy = Path(backend_policy).resolve()
        policy = json.loads(backend_policy.read_text(encoding='utf-8'))
        if policy['manifest_sha256'] != digest(study / 'manifest.json'):
            raise ValueError('Acceleration policy binds a different manifest')
        if digest(Path(policy['backend_source'])) != policy['backend_sha256']:
            raise ValueError('Acceleration source differs from policy')
    if (study / 'summary.json').exists():
        raise FileExistsError('Study already has summary; do not restart')
    runtime.mkdir(parents=True, exist_ok=False)
    (runtime / 'units').mkdir()
    lock = study / 'parallel_resume.lock'
    with lock.open('x', encoding='utf-8') as file:
        json.dump({'pid': os.getpid(), 'runtime': str(runtime), 'amendment': str(amendment_path)}, file)
    subprocess.run([sys.executable, str(ROOT / 'scripts/frozen_unit_worker.py'),
                    '--study', str(study), '--verify'], check=True, cwd=ROOT)
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    records, preserved, incomplete = read_ledger(study, manifest)
    if incomplete:
        raise ValueError(f'Incomplete artifacts require preservation/inspection first: {incomplete}')
    if any(row['status'] in STOP_STATUSES for row in records.values()):
        raise ValueError('Prior scientific stop exists; continuation is not authorized by a runtime change')
    atomic_json(runtime / 'preserved_receipts.json', preserved)
    entrypoint = Path(policy['worker_entrypoint']).resolve() if backend_policy else ROOT / 'scripts/frozen_unit_worker.py'
    atomic_json(runtime / 'dispatch_sources.json', {str(path.relative_to(ROOT)): digest(path) for path in
                [Path(__file__).resolve(), entrypoint]})
    work = pending_units(manifest, records)
    known = set(records)
    stopped, completed_units = False, 0
    started = time.monotonic()

    def status(pending, phase):
        for tasks in pending.values():
            for task in tasks:
                if (study / 'results' / task['task_id'] / 'receipt.json').is_file():
                    known.add(task['task_id'])
        counts = Counter(task['group_count'] for task in manifest['tasks'] if task['task_id'] in known)
        value = {'phase': phase, 'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                 'runner_pid': os.getpid(), 'workers': workers, 'numerical_threads_per_worker': 1,
                 'recorded_rows': len(known), 'scheduled_rows': len(manifest['tasks']),
                 'by_K': dict(counts), 'in_flight_units': len(pending), 'completed_resumed_units': completed_units,
                 'remaining_units_at_start': len(work), 'initial_preserved_rows': len(preserved),
                 'elapsed_seconds': round(time.monotonic() - started, 1),
                 'runtime_amendment': str(amendment_path), 'scientific_retries': 0,
                 'operational_restart': True, 'stopped_on_error_or_safety': stopped}
        value['backend_policy'] = str(backend_policy) if backend_policy else None
        atomic_json(runtime / 'status.json', value)

    pending, iterator = {}, iter(work)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def submit_next():
            item = next(iterator, None)
            if item:
                unit, tasks = item
                pending[pool.submit(run_unit, study, unit, runtime, backend_policy)] = tasks
        for _ in range(workers):
            submit_next()
        status(pending, 'running')
        try:
            while pending:
                done, _ = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
                for future in done:
                    tasks = pending.pop(future)
                    result = future.result()
                    completed_units += 1
                    known.update(task['task_id'] for task in tasks
                                 if (study / 'results' / task['task_id'] / 'receipt.json').is_file())
                    stopped |= result['stopped_on_error_or_safety']
                if not stopped:
                    for _ in done:
                        submit_next()
                status(pending, 'draining_after_scientific_stop' if stopped else 'running')
        except Exception:
            stopped = True
            status(pending, 'orchestration_error_draining')
            raise
    records, after, incomplete = read_ledger(study, manifest)
    if incomplete or any(after.get(task) != sha for task, sha in preserved.items()):
        raise ValueError('Incomplete or modified preserved task after resume')
    stopped |= any(row['status'] in STOP_STATUSES for row in records.values())
    ordered = [records[t['task_id']] for t in manifest['tasks'] if t['task_id'] in records]
    atomic_json(runtime / 'ordered_records.json', ordered)
    atomic_json(runtime / 'completion.json', {'preserved_rows_verified': len(preserved),
                'recorded_rows': len(records), 'scheduled_rows': len(manifest['tasks']),
                'all_scheduled_recorded': len(records) == len(manifest['tasks']),
                'stopped_on_error_or_safety': stopped, 'manifest_sha256': digest(study / 'manifest.json'),
                'runtime_amendment': str(amendment_path), 'backend_policy': str(backend_policy) if backend_policy else None})
    finalize_frozen(study, runtime / 'ordered_records.json', stopped)
    status({}, 'stopped_and_summarized' if stopped else 'complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--amendment', required=True)
    parser.add_argument('--workers', type=int, required=True)
    parser.add_argument('--backend-policy')
    args = parser.parse_args()
    try:
        run(args.study, args.runtime, args.amendment, args.workers, args.backend_policy)
    except Exception:
        runtime = Path(args.runtime)
        if runtime.is_dir():
            atomic_json(runtime / 'runner_error.json', {'at': datetime.now().astimezone().isoformat(),
                        'traceback': traceback.format_exc(), 'scientific_retry': False})
        raise


if __name__ == '__main__':
    main()
