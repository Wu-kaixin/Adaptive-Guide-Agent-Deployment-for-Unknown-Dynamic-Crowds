"""Distribute independently running frozen workers across all logical CPUs."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import time

import psutil

from .frozen_resume import atomic_json


def manage(runtime, runner_pid, count):
    runtime = Path(runtime).resolve()
    allowed = psutil.Process(runner_pid).cpu_affinity()
    if count != len(allowed):
        raise ValueError('All-core policy must match the runner\'s allowed logical CPUs')
    assigned = {}
    while psutil.pid_exists(runner_pid):
        for pid in list(assigned):
            try:
                if psutil.Process(pid).create_time() != assigned[pid]['created']:
                    del assigned[pid]
            except psutil.NoSuchProcess:
                del assigned[pid]
        free = [cpu for cpu in allowed if cpu not in {row['cpu'] for row in assigned.values()}]
        for path in sorted((runtime / 'units').glob('*.process.json')):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                pid = record['pid']
                wrapper = psutil.Process(pid)
                created = wrapper.create_time()
                recorded_at = datetime.fromisoformat(record['started_at']).timestamp()
                if abs(created - recorded_at) > 10 or wrapper.ppid() != runner_pid:
                    continue  # Exited worker or reused PID; never touch an unrelated process.
                if pid not in assigned:
                    if not free:
                        continue
                    assigned[pid] = {'cpu': free.pop(0), 'created': created, 'unit': record['unit_id']}
                row = assigned[pid]
                # Pin the venv launcher and its actual Python child; console hosts are excluded.
                processes = [wrapper] + [p for p in wrapper.children(recursive=True)
                                         if p.name().lower() in ('python.exe', 'pythonw.exe', 'python')]
                compute_pids = []
                for process in processes:
                    process.cpu_affinity([row['cpu']])
                    process.nice(psutil.NORMAL_PRIORITY_CLASS)
                    if process.pid != pid:
                        compute_pids.append(process.pid)
                row['compute_pids'] = compute_pids
            except (psutil.NoSuchProcess, FileNotFoundError):
                continue
        atomic_json(runtime / 'cpu_affinity_status.json', {
            'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'runner_pid': runner_pid, 'manager_pid': __import__('os').getpid(),
            'policy': 'one independent scientific worker per logical CPU; normal priority',
            'allowed_cpus': allowed, 'active_assignments': assigned})
        if (runtime / 'completion.json').exists() or (runtime / 'runner_error.json').exists():
            break
        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--runner-pid', required=True, type=int)
    parser.add_argument('--workers', required=True, type=int)
    args = parser.parse_args()
    manage(args.runtime, args.runner_pid, args.workers)


if __name__ == '__main__':
    main()
