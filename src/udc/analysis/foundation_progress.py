"""Read process identity and durable research progress without launching work."""
import argparse
from collections import Counter
import json
from pathlib import Path

import psutil


def process_identity(record):
    """A PID alone or a stale status file never proves the process is alive."""
    try:
        process = psutil.Process(record['pid'])
        if process.create_time() != record['create_time']:
            return {'state': 'pid_reused', 'alive': False}
        if process.exe() != record['exe'] or process.cmdline() != record['cmdline']:
            return {'state': 'identity_changed', 'alive': None}
        return {'state': process.status(), 'alive': process.is_running(),
                'cpu_time_s': sum(process.cpu_times()[:2]),
                'children': len(process.children(recursive=True))}
    except psutil.NoSuchProcess:
        return {'state': 'missing', 'alive': False}
    except psutil.AccessDenied:
        return {'state': 'access_denied', 'alive': None}


def inspect(study):
    study = Path(study).resolve()
    result = {'study': str(study), 'processes': {}, 'goal_complete': False}
    control = study / 'control'
    for name in ('process', 'execute_process', 'audit_process'):
        path = control / (name + '.json')
        if path.exists():
            result['processes'][name] = process_identity(json.loads(path.read_text(encoding='utf-8')))
    for name, path in [('control_status', control / 'status.json'), ('runner_status', study / 'status.json'),
                       ('summary', study / 'summary.json'), ('audit', study / 'readonly_audit/offline_audit.json')]:
        if path.exists():
            result[name] = json.loads(path.read_text(encoding='utf-8'))
    statuses = Counter()
    chunks = 0
    for receipt in (study / 'results').glob('*/chunk_*/receipt.json'):
        # This compact progress scan counts durable receipts. Scientific audit
        # separately hashes every artifact; do not claim revalidation here.
        metrics = json.loads((receipt.parent / 'metrics.json').read_text(encoding='utf-8'))
        statuses.update(m['status'] for m in metrics)
        chunks += 1
    result['durable_chunk_records'] = sum(statuses.values())
    result['durable_chunks'] = chunks
    result['durable_chunk_statuses'] = dict(statuses)
    result['progress_scan_is_full_hash_audit'] = False
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.study), ensure_ascii=False, indent=2))
