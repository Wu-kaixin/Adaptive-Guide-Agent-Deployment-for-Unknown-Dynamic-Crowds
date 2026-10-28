"""Paired physical-block analysis for the static phase-retention experiment."""
import argparse
from collections import Counter, defaultdict
import csv
import itertools
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

from udc.experiments.static_multigroup import ROOT, write_json, digest, environment_record
from udc.multigroup.phase_retention import METHODS


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def score_difference_bounds(treatment, control, limit):
    if any(v is not None and (not np.isfinite(v) or not 0 <= v <= limit) for v in (treatment, control)):
        raise ValueError('Bounded score is outside its declared range')
    return ((0 if treatment is None else treatment) - (limit if control is None else control),
            (limit if treatment is None else treatment) - (0 if control is None else control))


def primary_analysis(manifest, rows):
    spec = manifest['spec']
    lookup = {(r['sequence_id'], r['perturbation_m'], r['layout']): r for r in rows if r['kind'] == 'planning'}
    strata, blocks = [], []
    for k, shape in itertools.product(spec['group_counts'], spec['shapes']):
        block_values, bounds, greedy_values, retained_values = [], [], [], []
        for seed in range(spec['seeds']['start'], spec['seeds']['start'] + spec['seeds']['count']):
            differences, interval, control_scores, treatment_scores = [], [], [], []
            for mode in spec['base_measurement_models']:
                sequence = f'K{k}_{shape}_s{seed}_{mode}'
                pair = [lookup.get((sequence, .001, m), {}) for m in ('euclidean_retain_0.002', 'continuous_euclidean_greedy')]
                values = [r.get('matched_target_rms_m') for r in pair]
                interval.append(score_difference_bounds(*values, 50.))
                differences.append(values[0] - values[1] if all(v is not None for v in values) else np.nan)
                control_scores.append(values[1]); treatment_scores.append(values[0])
            complete = bool(np.isfinite(differences).all())
            block_values.append(float(np.mean(differences)) if complete else np.nan)
            bounds.append(np.mean(interval, axis=0))
            if complete:
                greedy_values.append(float(np.mean(control_scores)))
                retained_values.append(float(np.mean(treatment_scores)))
        values = np.asarray(block_values)
        finite = values[np.isfinite(values)]
        blocks.append(values)
        strata.append({'group_count': k, 'shape': shape, 'scheduled_blocks': len(values),
                       'valid_blocks': len(finite), 'missing_blocks': int(np.isnan(values).sum()),
                       'mean_rms_delta_m': float(finite.mean()) if len(finite) else None,
                       'mean_greedy_rms_m': float(np.mean(greedy_values)) if greedy_values else None,
                       'mean_retained_rms_m': float(np.mean(retained_values)) if retained_values else None,
                       'full_denominator_mean_bounds_m': np.mean(bounds, axis=0).tolist()})
    complete_strata = all(r['mean_rms_delta_m'] is not None for r in strata)
    estimate = float(np.mean([r['mean_rms_delta_m'] for r in strata])) if complete_strata else None
    interval, uncalculable = None, None
    if all(r['valid_blocks'] >= 2 for r in strata):
        rng = np.random.default_rng(spec['statistics']['bootstrap_seed'])
        estimates = []
        for values in blocks:
            samples = values[rng.integers(0, len(values), size=(spec['statistics']['bootstrap_repetitions'], len(values)))]
            count = np.isfinite(samples).sum(axis=1)
            estimates.append(np.divide(np.nansum(samples, axis=1), count, out=np.full(len(count), np.nan), where=count > 0))
        aggregate = np.mean(estimates, axis=0)
        uncalculable = float(np.mean(~np.isfinite(aggregate)))
        if uncalculable <= .01:
            interval = np.quantile(aggregate[np.isfinite(aggregate)], [.025, .975]).tolist()
    return {'evidence': 'development_only', 'strata': strata, 'equal_stratum_mean_delta_m': estimate,
            'paired_physical_block_bootstrap_95_interval_m': interval, 'uncalculable_bootstrap_share': uncalculable,
            'full_denominator_mean_bounds_m': np.mean([r['full_denominator_mean_bounds_m'] for r in strata], axis=0).tolist(),
            'independent_unit': 'K_shape_seed_with_both_models_together', 'scheduled_independent_blocks': sum(r['scheduled_blocks'] for r in strata),
            'interpretation': 'conditional point estimate; full-denominator bounds are sensitivity ranges, not confidence intervals'}


def verify_study(study, trajectories=True):
    manifest = read(study / 'manifest.json')
    if digest(study / 'manifest.json') != read(study / 'seal.json')['manifest_sha256']:
        raise ValueError('Changed manifest')
    if digest(study / 'protocol.yaml') != manifest['protocol_sha256']:
        raise ValueError('Changed protocol')
    if environment_record() != manifest['environment']:
        raise ValueError('Changed numerical environment')
    for name, sha in manifest['sources'].items():
        if digest(study / 'source_snapshot' / name) != sha:
            raise ValueError(f'Changed source snapshot: {name}')
    inputs = set()
    for sequence in manifest['sequences']:
        for item, field in [(sequence, 'truth'), *((f, 'observation') for f in sequence['frames'])]:
            if item[field] not in inputs:
                path = (study / item[field]).resolve()
                if not path.is_relative_to(study) or digest(path) != item[field + '_sha256']:
                    raise ValueError('Changed input')
                inputs.add(item[field])
    checked, opened = 0, 0
    for path in sorted((study / 'results').glob('*/receipt.json')):
        receipt = read(path)
        if any(digest(path.parent / name) != sha for name, sha in receipt.items()):
            raise ValueError(f'Changed task: {path.parent}')
        checked += 1
        if trajectories:
            with np.load(path.parent / 'trajectory.npz', allow_pickle=False) as data:
                p, v, assignment = data['positions'], data['velocities'], data['assignment']
                if (len(p) != len(v) + 1 or p.shape[1:] != v.shape[1:] or p.shape[1] != len(assignment)
                        or not np.isfinite(p).all() or not np.isfinite(v).all()):
                    raise ValueError('Invalid trajectory')
            opened += 1
    return {'manifest_sha256': digest(study / 'manifest.json'), 'verified_inputs': len(inputs),
            'verified_receipts': checked, 'opened_trajectories_without_pickle': opened}


def csv_table(path, rows):
    if rows:
        with path.open('w', newline='', encoding='utf-8-sig') as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def capture_original_interim(study, destination):
    """Accounting snapshot only; do not estimate the unfinished primary effect."""
    study, destination = Path(study).resolve(), Path(destination).resolve()
    statuses, by_k, path_reasons = Counter(), defaultdict(Counter), Counter()
    path_by_supply = defaultdict(Counter)
    receipts = {}
    for folder in sorted((study / 'results').iterdir()):
        receipt = folder / 'receipt.json'
        if not receipt.is_file():
            continue
        row = read(folder / 'metrics.json')
        statuses[row['status']] += 1
        by_k[row['group_count']][row['status']] += 1
        if row['status'] == 'PATH_NOT_FOUND':
            state = read(folder / 'state.json')
            path_reasons[state.get('evidence', {}).get('reason', 'unspecified')] += 1
            path_by_supply[str(row['group_count'])][row['resource']] += 1
        receipts[folder.name] = digest(receipt)
    result = {'evidence': 'unfinished_original_confirmation_accounting_snapshot_only',
              'captured_at': datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds'),
              'original_manifest_sha256': digest(study / 'manifest.json'), 'recorded_rows': sum(statuses.values()),
              'scheduled_rows': len(read(study / 'manifest.json')['tasks']), 'statuses': dict(statuses),
              'by_K': {str(k): dict(v) for k, v in by_k.items()}, 'path_failure_reasons': dict(path_reasons),
              'path_failures_by_K_and_resource': {k: dict(v) for k, v in path_by_supply.items()},
              'recorded_receipt_hashes': receipts,
              'interpretation': 'K5 unfinished and task order not randomized; no overall success-rate or primary-effect claim'}
    write_json(destination, result)
    return {k: v for k, v in result.items() if k != 'recorded_receipt_hashes'}


def summarize(study, output):
    study, output = Path(study).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError('Use a new analysis directory')
    verified = verify_study(study)
    manifest, summary = read(study / 'manifest.json'), read(study / 'summary.json')
    rows = [read(p) for p in sorted((study / 'results').glob('*/metrics.json'))]
    spec = manifest['spec']
    lookup = {(r['sequence_id'], r['perturbation_m'], r['layout']): r for r in rows if r['kind'] == 'planning'}
    cells = defaultdict(list)
    for row in rows:
        if row['kind'] == 'planning':
            cells[(row['group_count'], row['shape'], row['base_measurement_model'], row['perturbation_m'], row['layout'])].append(row)
    cell_rows = []
    for key, values in sorted(cells.items()):
        valid = [r for r in values if 'continuous_common_service' in r]
        cell_rows.append(dict(zip(('group_count', 'shape', 'base_model', 'perturbation_m', 'method'), key)) |
                         {'scheduled': 20, 'recorded': len(values), 'valid': len(valid),
                          'mean_rms_m': float(np.mean([r['matched_target_rms_m'] for r in valid])) if valid else None,
                          'maximum_rms_m': max((r['matched_target_rms_m'] for r in valid), default=None),
                          'mean_true_worst_u': float(np.mean([r['continuous_common_service']['worst_group_uncovered'] for r in valid])) if valid else None,
                          'mean_true_weighted_u': float(np.mean([r['continuous_common_service']['weighted_uncovered'] for r in valid])) if valid else None,
                          'mean_retained_groups': float(np.mean([r['retained_groups'] for r in valid])) if valid else None})
    contrasts, guard_failures, pool_failures, dominance_failures = [], [], [], []
    pairs = [('continuous_arc_greedy', 'sampled_arc_greedy'),
             ('continuous_euclidean_greedy', 'continuous_arc_greedy'),
             ('euclidean_retain_0.002', 'continuous_euclidean_greedy'),
             ('euclidean_retain_0.005', 'continuous_euclidean_greedy')]
    for k, shape, mode, eta, (method, control) in itertools.product(spec['group_counts'], spec['shapes'],
            spec['base_measurement_models'], spec['additional_perturbation_m'], pairs):
        valid = []
        for seed in range(spec['seeds']['start'], spec['seeds']['start'] + spec['seeds']['count']):
            sequence = f'K{k}_{shape}_s{seed}_{mode}'
            a, b = lookup.get((sequence, eta, method)), lookup.get((sequence, eta, control))
            if a and b and all('continuous_common_service' in r for r in (a, b)):
                valid.append((a, b))
        contrasts.append({'group_count': k, 'shape': shape, 'base_model': mode, 'perturbation_m': eta,
                          'treatment': method, 'control': control, 'scheduled_pairs': 20, 'valid_pairs': len(valid),
                          'mean_true_worst_delta': float(np.mean([a['continuous_common_service']['worst_group_uncovered'] - b['continuous_common_service']['worst_group_uncovered'] for a, b in valid])) if valid else None,
                          'mean_true_weighted_delta': float(np.mean([a['continuous_common_service']['weighted_uncovered'] - b['continuous_common_service']['weighted_uncovered'] for a, b in valid])) if valid else None,
                          'mean_rms_delta_m': float(np.mean([a['matched_target_rms_m'] - b['matched_target_rms_m'] for a, b in valid])) if valid else None,
                          'true_worst_loss_above_proxy_budget_count': sum(
                              a['continuous_common_service']['worst_group_uncovered'] - b['continuous_common_service']['worst_group_uncovered']
                              > float(method.rsplit('_', 1)[1]) for a, b in valid) if method.startswith('euclidean_retain_') else None,
                          'maximum_true_worst_loss': max((a['continuous_common_service']['worst_group_uncovered'] - b['continuous_common_service']['worst_group_uncovered'] for a, b in valid), default=None)})
    for sequence in manifest['sequences']:
        for frame in sequence['frames']:
            cohort = [lookup.get((sequence['sequence_id'], frame['perturbation_m'], method)) for method in METHODS]
            actual = [r for r in cohort if r and 'continuous_common_service' in r]
            plans = [read(study / 'results' / r['task_id'] / 'planning.json') for r in actual]
            if plans and any(p['shared_pool_sources'] != plans[0]['shared_pool_sources'] or p['counts'] != plans[0]['counts'] for p in plans):
                pool_failures.append(sequence['sequence_id'])
            by_method = {r['layout']: p for r, p in zip(actual, plans)}
            for method, control, field in [('continuous_arc_greedy', 'sampled_arc_greedy', 'selected_continuous_arc_u'),
                                           ('continuous_euclidean_greedy', 'continuous_arc_greedy', 'selected_assigned_observed_u')]:
                if method in by_method and control in by_method:
                    if any(a > b + 1e-12 for a, b in zip(by_method[method][field], by_method[control][field])):
                        dominance_failures.append([sequence['sequence_id'], frame['perturbation_m'], method, control])
            for r in actual:
                if r['layout'].startswith('euclidean_retain_'):
                    tolerance = float(r['layout'].rsplit('_', 1)[1])
                    if r['max_assigned_observed_slack'] > tolerance + 1e-12:
                        guard_failures.append(r['task_id'])
    motion_rows = [r for r in rows if r['kind'] != 'planning']
    motion = {'by_kind': {}, 'redeployment_pairs': []}
    for kind in ('nominal_deployment', 'redeployment'):
        selected = [r for r in motion_rows if r['kind'] == kind]
        motion['by_kind'][kind] = {'recorded': len(selected), 'statuses': dict(Counter(r['status'] for r in selected)),
            'safely_completed': sum(bool(r.get('deployment_completed')) for r in selected),
            'executed_intervals': sum(r.get('motion', {}).get('executed_intervals', 0) for r in selected),
            'unsafe_intervals': sum(r.get('motion', {}).get('unsafe_interval_count') or 0 for r in selected)}
    redeploy = {(r['sequence_id'], r['layout']): r for r in motion_rows if r['kind'] == 'redeployment'}
    start_failures = []
    for sequence in manifest['sequences']:
        if sequence['base_measurement_model'] != spec['motion_subset']['base_measurement_model']:
            continue
        control, treatment = [redeploy.get((sequence['sequence_id'], m)) for m in spec['motion_subset']['methods']]
        complete = bool(control and treatment and control.get('deployment_completed') and treatment.get('deployment_completed'))
        if control and treatment:
            with (np.load(study / 'results' / control['task_id'] / 'trajectory.npz', allow_pickle=False) as a,
                  np.load(study / 'results' / treatment['task_id'] / 'trajectory.npz', allow_pickle=False) as b):
                if not np.array_equal(a['positions'][0], b['positions'][0]):
                    start_failures.append(sequence['sequence_id'])
        motion['redeployment_pairs'].append({'sequence_id': sequence['sequence_id'], 'group_count': sequence['group_count'],
             'shape': sequence['shape'], 'seed': sequence['seed'], 'both_safely_completed': complete,
             'greedy_travel_m': control['motion']['travel_m'] if control and control.get('motion') else None,
             'retained_travel_m': treatment['motion']['travel_m'] if treatment and treatment.get('motion') else None,
             'travel_delta_m': treatment['motion']['travel_m'] - control['motion']['travel_m'] if complete else None})
    motion['paired_initial_state_mismatches'] = start_failures
    output.mkdir(parents=True)
    result = {'summary': summary, 'verification': verified, 'primary': primary_analysis(manifest, rows),
              'guard_failures': guard_failures, 'shared_pool_failures': pool_failures,
              'observed_scoring_dominance_failures': dominance_failures,
              'max_actual_observation_error_m': max((r.get('max_actual_observation_error_m', 0) for r in rows)),
              'group_partition_mismatches': sum(r.get('group_partition_matches_truth') is False for r in rows),
              'planning_cells': cell_rows, 'contrasts': contrasts, 'motion': motion}
    write_json(output / 'analysis.json', result)
    csv_table(output / 'planning_cells.csv', cell_rows)
    csv_table(output / 'contrasts.csv', contrasts)
    csv_table(output / 'redeployments.csv', motion['redeployment_pairs'])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = summarize(args.study, args.output)
    print(json.dumps({key: value for key, value in result.items() if key not in ('planning_cells', 'contrasts', 'motion')}, indent=2))
