"""Summarize all saved tasks, retaining failures in the success-rate denominator."""
import argparse
import json
import math
from pathlib import Path

import numpy as np


def wilson_interval(successes, total):
    if total == 0:
        return None
    z = 1.959963984540054
    p = successes/total
    denominator = 1+z*z/total
    center = (p+z*z/(2*total))/denominator
    half = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return [max(0., center-half), min(1., center+half)]


def summarize_results(root):
    groups = {}
    paths = sorted(Path(root).rglob('metrics.json'))
    for path in paths:
        row = json.loads(path.read_text(encoding='utf-8'))
        key = '|'.join(str(row.get(name)) for name in ['scene', 'method', 'protocol'])
        group = groups.setdefault(key, {'tasks': 0, 'successful': 0, 'statuses': {}, 'times': []})
        group['tasks'] += 1
        group['successful'] += int(row['success'])
        status = row['termination_status']
        group['statuses'][status] = group['statuses'].get(status, 0)+1
        if row['success']:
            group['times'].append(row['simulation_time'])
    errors = list(Path(root).rglob('runner_error.txt'))
    for path in errors:
        if not (path.parent/'metrics.json').exists():
            state_file = path.parent/'state.json'
            state = json.loads(state_file.read_text()) if state_file.exists() else {}
            key = ('|'.join(str(state[name]) for name in ['scene', 'method', 'protocol'])
                   if all(name in state for name in ['scene', 'method', 'protocol']) else 'EXPERIMENT_ERROR')
            group = groups.setdefault(key, {'tasks': 0, 'successful': 0, 'statuses': {}, 'times': []})
            group['tasks'] += 1
            group['statuses']['EXPERIMENT_ERROR'] = group['statuses'].get('EXPERIMENT_ERROR', 0)+1
    for group in groups.values():
        times = group.pop('times')
        group['success_rate'] = group['successful']/group['tasks']
        group['success_rate_ci95_wilson'] = wilson_interval(group['successful'], group['tasks'])
        group['successful_time_count'] = len(times)
        group['successful_time_mean'] = float(np.mean(times)) if times else None
        group['successful_time_median'] = float(np.median(times)) if times else None
    total = sum(g['tasks'] for g in groups.values())
    successful = sum(g['successful'] for g in groups.values())
    return {
        'tasks': total, 'successful': successful,
        'success_rate': successful/total if total else None,
        'success_rate_ci95_wilson': wilson_interval(successful, total),
        'groups': groups,
        'time_statistics_scope': 'successful tasks only; failures retained in success rates',
        'interval_scope': 'binomial Wilson intervals assume independent sampled trials; deterministic repeats are not new evidence',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = summarize_results(args.results)
    path = Path(args.output)
    if path.exists():
        raise FileExistsError('Use a new summary file')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
