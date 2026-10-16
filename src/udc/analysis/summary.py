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
    'simulation_time', 'path_length', 'wall_time', 'mean_curve_distance', 'planner_seconds',
}


def executed_metric(row, name):
    """Trajectory quantities are missing when planning never produced an execution.

    A stored simulation time of 0 means no steps were taken. It is not a measured
    deployment time and must not enter a mean.
    """
    if name in {'simulation_time', 'path_length', 'actual_max_gap', 'actual_demand_weighted_cost',
                'actual_excess_weighted_cost', 'actual_hotspot_concentration',
                'mean_curve_distance'} and not row.get('plan_found'):
        return None
    value = row.get(name)
    if name == 'simulation_time' and not row.get('success') and value == 0:
        return None
    return _number(value) if name != 'simulation_time' else value


def retained_improvement(reference, candidate, unconstrained):
    """Share of the unconstrained improvement over ``reference`` that ``candidate`` keeps.

    This is not the candidate's improvement relative to ``reference``. The result
    is undefined when the unconstrained cost is not better than the reference.
    """
    if None in (reference, candidate, unconstrained):
        return None
    span = float(reference) - float(unconstrained)
    if span <= 1e-12:
        return None
    return (float(reference) - float(candidate)) / span


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


def _criteria(row):
    criteria = row.get('criteria')
    if isinstance(criteria, dict):
        return criteria
    output = row.get('output')
    if not output:
        return None
    path = Path(output) / 'metrics.json'
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8')).get('criteria')


def _bootstrap_mean(values, replicates, rng):
    sample = np.asarray(list(values), dtype=float)
    if len(sample) == 0:
        return {'seeds': 0, 'mean': None, 'ci95': None}
    draws = rng.integers(0, len(sample), size=(replicates, len(sample)))
    means = sample[draws].mean(axis=1)
    return {
        'seeds': int(len(sample)),
        'mean': float(sample.mean()),
        'ci95': [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))],
    }


def _paired_delta(rows, reference, candidate, metric, require_plan=False):
    by_seed = {}
    defined = missing = better = worse = tie = 0
    for row in rows:
        if row.get('method') != candidate:
            continue
        base = next((item for item in rows
                     if item.get('method') == reference and item.get('cell') == row.get('cell')
                     and item.get('seed') == row.get('seed')), None)
        if base is None:
            continue
        if require_plan and not (row.get('plan_found') and base.get('plan_found')):
            missing += 1
            continue
        tracked = {
            'simulation_time', 'path_length', 'actual_max_gap', 'actual_demand_weighted_cost',
            'actual_excess_weighted_cost', 'actual_hotspot_concentration', 'mean_curve_distance',
        }
        mine = executed_metric(row, metric) if metric in tracked else _number(row.get(metric))
        theirs = executed_metric(base, metric) if metric in tracked else _number(base.get(metric))
        if mine is None or theirs is None:
            missing += 1
            continue
        delta = mine - theirs
        defined += 1
        by_seed.setdefault(row['seed'], []).append(delta)
        if abs(delta) <= 1e-9:
            tie += 1
        elif (delta < 0) == (metric in LOWER_IS_BETTER or metric.endswith('_cost') or metric.endswith('_gap')):
            better += 1
        else:
            worse += 1
    return {
        'pairs_defined': defined,
        'pairs_missing': missing,
        'better': better,
        'worse': worse,
        'tie': tie,
        'seed_means': {str(seed): float(np.mean(values)) for seed, values in sorted(by_seed.items())},
    }


def _stratum_cluster(rows, reference, candidate, metric, key, replicates, rng, require_plan=False):
    labels = sorted({row.get(key) for row in rows if row.get('method') == candidate})
    report = {}
    for label in labels:
        selected = [row for row in rows if row.get(key) == label]
        paired = _paired_delta(selected, reference, candidate, metric, require_plan)
        paired['seed_cluster'] = _bootstrap_mean(paired['seed_means'].values(), replicates, rng)
        report[str(label)] = paired
    return report


def seed_cluster_report(rows, reference='equal_arc', candidates=('mass_cvt', 'projected_mass'),
                        replicates=10000, rng_seed=20261016):
    """Paired confirmation summary. Resampling draws whole seeds, not cells inside a seed.

    Planning failures stay in the success denominator. A missing trajectory metric is
    omitted from that metric's mean; it is not replaced by zero. Demand remains a
    planning weight. The interval is a percentile bootstrap of seed-level means.
    """
    rng = np.random.default_rng(rng_seed)
    methods = sorted({row['method'] for row in rows})
    by_method = {}
    for method in methods:
        selected = [row for row in rows if row['method'] == method]
        by_seed = {}
        stages = {}
        for row in selected:
            bucket = by_seed.setdefault(row['seed'], [0, 0])
            bucket[1] += 1
            bucket[0] += int(bool(row['success']))
            label = str(row.get('stage_diagnosis'))
            stages[label] = stages.get(label, 0) + 1
        rates = [successes / count for successes, count in by_seed.values() if count]
        by_method[method] = {
            'tasks': len(selected),
            'successful': sum(int(bool(row['success'])) for row in selected),
            'stages': stages,
            'seed_success_rate': _bootstrap_mean(rates, replicates, rng),
        }
    necessary = {'met': 0, 'violated': 0, 'unknown': 0}
    for row in rows:
        if row.get('method') != reference:
            continue
        flag = (row.get('feasibility') or {}).get('necessary_condition_met')
        if flag is True:
            necessary['met'] += 1
        elif flag is False:
            necessary['violated'] += 1
        else:
            necessary['unknown'] += 1
    constraints = {
        'tasks_with_criteria': 0,
        'safety_violations': 0,
        'gap_violations': 0,
        'curve_violations': 0,
        'coverage_violations': 0,
    }
    for row in rows:
        # A planning failure has no executed trajectory. Its false criteria record
        # the missing plan; they are not a safety or coverage breach of a deployment.
        if not row.get('plan_found'):
            continue
        criteria = _criteria(row)
        if not criteria:
            continue
        constraints['tasks_with_criteria'] += 1
        constraints['safety_violations'] += int(criteria.get('safety_ok') is False)
        constraints['gap_violations'] += int(criteria.get('gap_ok') is False)
        constraints['curve_violations'] += int(criteria.get('curve_ok') is False)
        constraints['coverage_violations'] += int(criteria.get('coverage_ok') is False)
    metrics = (
        'planned_demand_weighted_cost', 'actual_demand_weighted_cost',
        'planned_excess_weighted_cost', 'actual_excess_weighted_cost',
        'planned_background_weighted_cost', 'actual_background_weighted_cost',
        'path_length', 'simulation_time', 'wall_time', 'actual_max_gap',
        'mean_curve_distance', 'planner_seconds',
    )
    paired = {}
    for candidate in candidates:
        entry = {}
        for metric in metrics:
            require_plan = metric.startswith('actual_') or metric in {
            'path_length', 'simulation_time', 'mean_curve_distance'}
            overall = _paired_delta(rows, reference, candidate, metric, require_plan)
            overall['seed_cluster'] = _bootstrap_mean(overall['seed_means'].values(), replicates, rng)
            overall['by_shape'] = _stratum_cluster(
                rows, reference, candidate, metric, 'shape', replicates, rng, require_plan)
            overall['by_demand'] = _stratum_cluster(
                rows, reference, candidate, metric, 'demand', replicates, rng, require_plan)
            overall['by_resource'] = _stratum_cluster(
                rows, reference, candidate, metric, 'resource', replicates, rng, require_plan)
            entry[metric] = overall
        paired[candidate] = entry
    return {
        'tasks': len(rows),
        'reference': reference,
        'methods': by_method,
        'necessary_condition_pairs': necessary,
        'constraints': constraints,
        'paired': paired,
        'interval': (
            'percentile bootstrap of seed-level means; cells that share a seed are not '
            'independent replicates. replicates=%d, rng_seed=%d' % (replicates, rng_seed)
        ),
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
