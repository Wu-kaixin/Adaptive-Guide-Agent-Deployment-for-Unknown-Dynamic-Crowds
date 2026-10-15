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


COMPARISON_METRICS = (
    'actual_max_gap', 'planned_max_gap', 'actual_demand_weighted_cost',
    'planned_demand_weighted_cost', 'actual_uniform_cost',
    'actual_uncovered_demand_share', 'planned_uncovered_demand_share',
    'actual_hotspot_concentration', 'planned_hotspot_concentration',
    'actual_hotspot_mean_spacing', 'actual_hotspot_max_gap',
    'min_net_clearance', 'simulation_time', 'path_length', 'wall_time',
    'planned_concentration',
)
# Sign that counts as an improvement over the reference method.
LOWER_IS_BETTER = {
    'actual_max_gap', 'planned_max_gap', 'actual_demand_weighted_cost',
    'planned_demand_weighted_cost', 'actual_uniform_cost',
    'actual_uncovered_demand_share', 'planned_uncovered_demand_share',
    'actual_hotspot_mean_spacing', 'actual_hotspot_max_gap',
    'simulation_time', 'path_length', 'wall_time',
}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _metric(row, name):
    if name == 'min_net_clearance':
        clearances = row.get('minimum_net_clearances') or {}
        values = [v for v in clearances.values() if _number(v) is not None]
        return min(values) if values else None
    return _number(row.get(name))


def _describe(values):
    return {'defined': len(values),
            'mean': float(np.mean(values)) if values else None,
            'median': float(np.median(values)) if values else None,
            'min': float(np.min(values)) if values else None,
            'max': float(np.max(values)) if values else None}


def summarize_comparison(rows, reference='equal_arc'):
    """Per cell and method: success rates over all tasks plus paired deltas vs ``reference``.

    Failed tasks stay in every denominator. Metric means only cover tasks
    where the quantity is defined, and the ``defined`` count is reported next
    to each mean so partial coverage is visible.
    """
    groups = {}
    for row in rows:
        key = f"{row['cell']}|{row['method']}"
        group = groups.setdefault(key, {
            'cell': row['cell'], 'method': row['method'], 'fixed_n': row.get('fixed_n'),
            'tasks': 0, 'successful': 0, 'failure_classes': {}, 'stages': {},
            'statuses': {}, 'equal_arc_fallbacks': 0, '_values': {name: [] for name in COMPARISON_METRICS},
        })
        group['tasks'] += 1
        group['successful'] += int(bool(row['success']))
        for field, bucket in (('failure_class', 'failure_classes'),
                              ('stage_diagnosis', 'stages'), ('termination_status', 'statuses')):
            label = str(row.get(field))
            group[bucket][label] = group[bucket].get(label, 0)+1
        group['equal_arc_fallbacks'] += int(bool(row.get('equal_arc_fallback')))
        for name in COMPARISON_METRICS:
            value = _metric(row, name)
            if value is not None:
                group['_values'][name].append(value)
    for group in groups.values():
        values = group.pop('_values')
        group['success_rate'] = group['successful']/group['tasks']
        group['success_rate_ci95_wilson'] = wilson_interval(group['successful'], group['tasks'])
        group['metrics'] = {name: _describe(values[name]) for name in COMPARISON_METRICS}
    by_pair = {}
    for row in rows:
        by_pair.setdefault((row['cell'], row['seed']), {})[row['method']] = row
    paired = {}
    for (cell, seed), methods in by_pair.items():
        base = methods.get(reference)
        if base is None:
            continue
        for method, row in methods.items():
            if method == reference:
                continue
            entry = paired.setdefault(f'{cell}|{method}', {
                'cell': cell, 'method': method, 'reference': reference, 'pairs': 0,
                'success_both': 0, 'success_only_method': 0, 'success_only_reference': 0,
                'success_neither': 0, 'deltas': {name: {'n': 0, 'values': [], 'better': 0, 'worse': 0, 'tie': 0}
                                                 for name in COMPARISON_METRICS},
            })
            entry['pairs'] += 1
            a, b = bool(row['success']), bool(base['success'])
            entry['success_both' if a and b else 'success_only_method' if a else
                  'success_only_reference' if b else 'success_neither'] += 1
            for name in COMPARISON_METRICS:
                mine, theirs = _metric(row, name), _metric(base, name)
                if mine is None or theirs is None:
                    continue
                delta = mine-theirs
                item = entry['deltas'][name]
                item['n'] += 1
                item['values'].append(delta)
                if abs(delta) <= 1e-9:
                    item['tie'] += 1
                elif (delta < 0) == (name in LOWER_IS_BETTER):
                    item['better'] += 1
                else:
                    item['worse'] += 1
    for entry in paired.values():
        for item in entry['deltas'].values():
            values = item.pop('values')
            item['mean_delta'] = float(np.mean(values)) if values else None
            item['median_delta'] = float(np.median(values)) if values else None
    return {
        'tasks': len(rows), 'successful': sum(bool(row['success']) for row in rows),
        'reference_method': reference,
        'groups': groups, 'paired': paired,
        'scope': ('development seeds only; success rates keep every task in the denominator; '
                  'metric means cover the tasks where the metric is defined (see "defined")'),
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
