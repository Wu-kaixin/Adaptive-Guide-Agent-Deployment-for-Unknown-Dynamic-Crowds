"""Supplementary full-ledger bounds; never replace a frozen primary estimand."""
import numpy as np


def bounded_pair_effect(minimax, perimeter):
    """Sharp difference bounds if an unobserved unmet fraction lies in [0,1]."""
    for value in (minimax, perimeter):
        if value is not None and (not np.isfinite(value) or not 0 <= value <= 1):
            raise ValueError('An observed unmet fraction must be in [0,1]')
    return ((0. if minimax is None else minimax) - (1. if perimeter is None else perimeter),
            (1. if minimax is None else minimax) - (0. if perimeter is None else perimeter))


def full_ledger_analysis(manifest, records):
    """Equal-stratum bounds include all predeclared primary physical pairs."""
    stats = manifest['spec']['statistics']
    def primary(row):
        return (row['layout'] == stats['primary_layout'] and row['resource'] == stats['primary_resource']
                and row['error_bound_m'] == stats['primary_error_bound_m']
                and row['allocator'] in ('minimax', 'perimeter'))
    expected = sorted({(t['group_count'], t['shape'], t['seed']) for t in manifest['tasks'] if primary(t)})
    lookup = {(r['group_count'], r['shape'], r['seed'], r['allocator']): r for r in records if primary(r)}
    strata = {}
    for unit in expected:
        rows = [lookup.get((*unit, method)) for method in ('minimax', 'perimeter')]
        key = unit[:2]
        if key not in strata:
            strata[key] = {'group_count': key[0], 'shape': key[1], 'scheduled_pairs': 0,
                           'planned': [], 'safely_completed_actual': [], 'missing_planned_pairs': 0,
                           'missing_completed_actual_pairs': 0}
        item = strata[key]
        item['scheduled_pairs'] += 1
        for field, metric, gated in [('planned', 'planned_common_service', False),
                                      ('safely_completed_actual', 'actual_common_service', True)]:
            values = [r[metric]['worst_group_uncovered'] if r and r.get(metric)
                      and (not gated or r.get('deployment_completed')) else None for r in rows]
            item[field].append(bounded_pair_effect(*values))
            missing_key = 'missing_completed_actual_pairs' if gated else 'missing_planned_pairs'
            item[missing_key] += int(any(v is None for v in values))
    result = []
    for item in strata.values():
        for field in ('planned', 'safely_completed_actual'):
            item[field + '_mean_bounds'] = np.mean(item.pop(field), axis=0).tolist()
        result.append(item)
    return {'strata': result, 'scheduled_pairs': len(expected),
            **{field + '_equal_stratum_bounds': np.mean([s[field + '_mean_bounds'] for s in result], axis=0).tolist()
               for field in ('planned', 'safely_completed_actual')},
            'interpretation': 'identification bounds, not confidence intervals; missing values not imputed as failures'}
