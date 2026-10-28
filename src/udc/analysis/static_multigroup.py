"""Predeclared paired estimand; failed physical draws stay in the denominator."""
import numpy as np


def primary_analysis(manifest, records):
    spec = manifest['spec']
    stats = spec['statistics']
    selected = {}
    for row in records:
        if (row['layout'] == stats['primary_layout'] and row['resource'] == stats['primary_resource']
                and row['error_bound_m'] == stats['primary_error_bound_m']
                and row['allocator'] in ('perimeter', 'minimax')):
            selected[(row['group_count'], row['shape'], row['seed'], row['allocator'])] = row
    strata, all_draws = [], []
    # Include every scheduled physical unit, including absent/failed results.
    factors = sorted({(task['group_count'], task['shape']) for task in manifest['tasks']
                      if task['layout'] == stats['primary_layout'] and task['resource'] == stats['primary_resource']
                      and task['error_bound_m'] == stats['primary_error_bound_m']})
    for group_count, shape in factors:
        differences = []
        for seed in manifest['seeds']:
            pair = [selected.get((group_count, shape, seed, method)) for method in ('minimax', 'perimeter')]
            if all(row is not None and 'planned_common_service' in row for row in pair):
                differences.append(pair[0]['planned_common_service']['worst_group_uncovered']
                                   - pair[1]['planned_common_service']['worst_group_uncovered'])
            else:
                differences.append(np.nan)
        data = np.asarray(differences)
        valid = data[np.isfinite(data)]
        strata.append({'group_count': group_count, 'shape': shape, 'draws': len(data),
                       'valid_pairs': len(valid), 'missing_pairs': int(np.isnan(data).sum()),
                       'mean_delta': float(valid.mean()) if len(valid) else None,
                       'negative': int(np.sum(valid < 0)), 'zero': int(np.sum(valid == 0)),
                       'positive': int(np.sum(valid > 0))})
        all_draws.append(data)
    means = [row['mean_delta'] for row in strata]
    estimate = float(np.mean(means)) if means and all(m is not None for m in means) else None
    interval, missing_bootstrap_share = None, None
    # A one-seed smoke is a development check, not a confidence interval.
    if estimate is not None and all(row['valid_pairs'] >= 2 for row in strata):
        rng = np.random.default_rng(stats['bootstrap_seed'])
        bootstrap_means = []
        for data in all_draws:
            sample = data[rng.integers(0, len(data), size=(stats['bootstrap_repetitions'], len(data))) ]
            count = np.isfinite(sample).sum(axis=1)
            bootstrap_means.append(np.divide(np.nansum(sample, axis=1), count,
                                             out=np.full(len(sample), np.nan), where=count > 0))
        aggregate = np.mean(bootstrap_means, axis=0)
        missing_bootstrap_share = float(np.mean(~np.isfinite(aggregate)))
        finite = aggregate[np.isfinite(aggregate)]
        if missing_bootstrap_share <= .01 and len(finite):
            tail = (1 - stats['confidence']) / 2
            interval = np.quantile(finite, [tail, 1 - tail]).tolist()
    return {'estimand': stats['primary'], 'evidence': manifest['evidence'], 'strata': strata,
            'equal_stratum_mean_delta': estimate, 'paired_stratified_95_interval': interval,
            'uncalculable_bootstrap_share': missing_bootstrap_share,
            'independent_unit': stats['independent_unit'],
            'interpretation': 'conditional on both methods producing planned targets; full failure ledger retained',
            'supports_preregistered_negative_direction': bool(interval and interval[1] < 0),
            'prospective_confirmation_completed': bool(manifest['evidence'] == 'prospective_confirmation'
                                                       and len(records) == len(manifest['tasks']))}
