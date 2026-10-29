"""Wait for a completed frozen development audit, then dispatch frozen work.

Only orchestration lives here; the executed science and audit are imported
from the confirmation study's existing source snapshot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import psutil


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def readiness(parent):
    parent = Path(parent)
    status = read(parent / 'control/status.json')
    if status['phase'] in ('scientific_stop', 'orchestration_error'):
        return {'state': 'rejected', 'reason': status['phase']}
    completion = parent / 'control/completion.json'
    if not completion.exists():
        record = read(parent / 'control/process.json')
        try:
            process = psutil.Process(record['pid'])
            alive = (process.create_time() == record['create_time'] and process.exe() == record['exe']
                     and process.cmdline() == record['cmdline'] and process.is_running())
        except psutil.NoSuchProcess:
            alive = False
        if not alive:
            return {'state': 'rejected', 'reason': 'parent_missing_or_identity_changed_without_completed_audit'}
        return {'state': 'waiting', 'phase': status['phase'], 'verified_parent_pid': record['pid']}
    manifest, summary, audit = (read(parent / name) for name in
        ('manifest.json', 'summary.json', 'readonly_audit/offline_audit.json'))
    activated = audit['by_method']['counts_phase_guard']['nontrivial_hold_frames'] > 0 and audit['by_method']['recompute_all']['count_switch_events'] > 0
    passed = (read(completion)['batch_completed'] and not summary['stopped']
        and summary['recorded_step_records'] == summary['scheduled_step_records'] == manifest['scheduled_step_records']
        and summary['sequence_completed'] == summary['episodes_recorded']
        and audit['all_match'] and not audit['mismatches']
        and audit['checked_step_records'] == manifest['scheduled_step_records']
        and not set(summary['statuses']) - {'FULL_SUCCESS', 'PARTIAL_SERVICE'} and activated)
    return {'state': 'ready' if passed else 'rejected', 'activation_passed': activated,
        'parent_manifest_sha256': digest(parent / 'manifest.json'),
        'parent_summary_sha256': digest(parent / 'summary.json'),
        'parent_audit_sha256': digest(parent / 'readonly_audit/offline_audit.json'),
        'parent_completion_sha256': digest(completion)}


def write(path, value, immutable=False):
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if immutable:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(text)
    else:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--after-study', required=True)
    parser.add_argument('--study', required=True)
    parser.add_argument('--control', required=True)
    parser.add_argument('--workers', type=int, default=32)
    args = parser.parse_args()
    parent, study, control = (Path(p).resolve() for p in (args.after_study, args.study, args.control))
    control.mkdir(parents=True, exist_ok=False)
    process = psutil.Process()
    write(control / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline(), 'launcher_sha256': digest(Path(__file__))}, True)
    while True:
        gate = readiness(parent)
        write(control / 'status.json', {'phase': 'development_gate', 'gate': gate, 'goal_complete': False})
        if gate['state'] != 'waiting':
            break
        time.sleep(10)
    write(control / 'gate_receipt.json', gate, True)
    if gate['state'] != 'ready':
        raise RuntimeError('Development gate rejected; no confirmation launched')
    if (study / 'control').exists() or (study / 'results').exists():
        raise FileExistsError('Refuse duplicate confirmation launch')
    code = ("import sys;from pathlib import Path;study=Path(sys.argv[1]);"
            "sys.path.insert(0,str(study/'source_snapshot/src'));"
            "from udc.experiments.foundation_sequence import run;run(study,int(sys.argv[2]))")
    write(control / 'status.json', {'phase': 'confirmation', 'goal_complete': False})
    with (control / 'confirmation.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study), str(args.workers)], stdout=log, stderr=subprocess.STDOUT)
        target = psutil.Process(child.pid)
        write(control / 'confirmation_process.json', {'pid': child.pid, 'create_time': target.create_time(),
            'exe': target.exe(), 'cmdline': target.cmdline()}, True)
        child.wait()
        write(control / 'dispatch_completion.json', {'exit_code': child.returncode, 'goal_complete': False}, True)
        if child.returncode:
            raise RuntimeError('Confirmation orchestration failed; inspect preserved log')
    write(control / 'status.json', {'phase': 'dispatch_finished', 'goal_complete': False})


if __name__ == '__main__':
    main()
