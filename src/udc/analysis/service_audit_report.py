"""Reproducible summaries of the separately frozen static development studies."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path

import numpy as np

from udc.experiments.static_multigroup import ROOT, digest, write_json
from .service_audit import full_ledger_analysis


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def records(study):
    return [read(path) for path in sorted((study / 'results').glob('*/metrics.json'))]


def verify_study(study, trajectories=False):
    """Check frozen input/source snapshots and every completed result receipt."""
    manifest = read(study / 'manifest.json')
    seal = read(study / 'seal.json')
    if digest(study / 'manifest.json') != seal['manifest_sha256']:
        raise ValueError(f'Manifest changed: {study}')
    for relative, expected in manifest['sources'].items():
        if digest(study / 'source_snapshot' / relative) != expected:
            raise ValueError(f'Source snapshot changed: {study} {relative}')
    checked_inputs = set()
    for unit in manifest['units']:
        for field in ('truth', 'observation'):
            if unit[field] in checked_inputs:
                continue
            if digest(study / unit[field]) != unit[field + '_sha256']:
                raise ValueError('Changed frozen input')
            checked_inputs.add(unit[field])
    checked, trajectories_opened = 0, 0
    for path in sorted((study / 'results').glob('*/receipt.json')):
        receipt = read(path)
        if any(digest(path.parent / relative) != expected for relative, expected in receipt.items()):
            raise ValueError(f'Result changed: {path.parent}')
        checked += 1
        if trajectories:
            with np.load(path.parent / 'trajectory.npz', allow_pickle=False) as data:
                positions, velocities, assignment = data['positions'], data['velocities'], data['assignment']
                if positions.shape[1:] != velocities.shape[1:] or len(positions) != len(velocities) + 1:
                    raise ValueError('Invalid saved trajectory dimensions')
                if positions.shape[1] != len(assignment) or not np.isfinite(positions).all() or not np.isfinite(velocities).all():
                    raise ValueError('Invalid saved trajectory values')
                trajectories_opened += 1
    return {'study': str(study), 'manifest_sha256': seal['manifest_sha256'],
            'verified_inputs': len(checked_inputs), 'verified_receipts': checked,
            'trajectories_opened_without_pickle': trajectories_opened}


def audit_summary(study):
    summary = read(study / 'summary.json')
    rows = [read(path) for path in sorted((study / 'results').glob('*.json'))]
    selected = [r for r in summary['paired_effects'] if r.get('shape') == 'concave'
                and r['error_bound_m'] == .12 and 1200 <= r['seed'] <= 1219]
    return {'rows': summary['rows'], 'pairs': summary['pairs'], 'stages': summary['stages'],
            'concave_20seed_means': {str(k): np.mean([r['stage_deltas'] for r in selected if r['group_count'] == k], axis=0).tolist()
                                     for k in (2, 5)},
            'max_planner_reconstruction_error': summary['max_planner_reconstruction_error'],
            'max_sampled_2048_minus_continuous_worst_abs': max(abs(
                r['variants']['exact_reference_exact_euclidean_all_2048']['worst'] -
                r['variants']['exact_reference_exact_euclidean_all_continuous']['worst']) for r in rows),
            'maximum_cross_group_service_gain': max(
                r['variants']['exact_reference_exact_euclidean_assigned_continuous']['worst'] -
                r['variants']['exact_reference_exact_euclidean_all_continuous']['worst'] for r in rows),
            'adverse_ellipse': next(r for r in summary['paired_effects'] if r.get('group_count') == 5
                                    and r['shape'] == 'ellipse' and r['error_bound_m'] == .12),
            'sampled_full_but_continuous_gap_count': summary['sampled_full_but_continuous_gap_count']}


def mechanism_summary(study):
    summary = read(study / 'summary.json')
    grouped = defaultdict(list)
    for effect in summary['paired_effects']:
        key = tuple(effect[k] for k in ('group_count', 'size_pattern', 'demand_pattern', 'resource', 'baseline'))
        grouped[key].append(effect)
    rows = []
    for key, values in sorted(grouped.items()):
        rows.append(dict(zip(('group_count', 'size_pattern', 'demand_pattern', 'resource', 'baseline'), key)) |
                    {'pairs': len(values), 'worst_delta_mean': float(np.mean([r['worst_delta'] for r in values])),
                     'weighted_delta_mean': float(np.mean([r['weighted_delta'] for r in values])),
                     'worst_improved': sum(r['worst_delta'] < 0 for r in values),
                     'worst_worsened': sum(r['worst_delta'] > 0 for r in values)})
    metrics = records(study)
    by_pair = defaultdict(dict)
    for row in metrics:
        by_pair[(row['unit_id'], row['resource'])][row['allocator']] = row
    total_failures = []
    minimax_failures = []
    for key, pair in by_pair.items():
        for baseline in ('perimeter', 'demand', 'minimax'):
            if pair['total']['planning_objective'][1] > pair[baseline]['planning_objective'][1] + 1e-12:
                total_failures.append([key, baseline])
        for baseline in ('perimeter', 'demand', 'total'):
            if pair['minimax']['planning_objective'][0] > pair[baseline]['planning_objective'][0] + 1e-12:
                minimax_failures.append([key, baseline])
    return {'scheduled_rows': summary['scheduled_rows'], 'recorded_rows': summary['recorded_rows'],
            'statuses': summary['statuses'], 'independent_seed_blocks': 40, 'position_configurations': 80,
            'weighted_input_configurations': 160, 'strata': rows,
            'observed_total_dp_dominance_failures': total_failures,
            'observed_minimax_dominance_failures': minimax_failures}


def stability_summary(study):
    grouped = defaultdict(list)
    for row in records(study):
        grouped[(row['group_count'], row['measurement_model'], row['perturbation_m'])].append(row)
    results = []
    for key, values in sorted(grouped.items()):
        matched = [r['matched_target_rms_m'] for r in values if r.get('matched_target_rms_m') is not None]
        results.append({'group_count': key[0], 'measurement_model': key[1], 'perturbation_m': key[2],
                        'scheduled_seeds': 20, 'recorded': len(values),
                        'partition_changes': sum(r.get('partition_unchanged') is False for r in values),
                        'certified': sum(r.get('stability_certificate', {}).get('certified') is True for r in values),
                        'count_changes': sum((r.get('counts_l1') or 0) > 0 for r in values),
                        'matched_pairs': len(matched), 'matched_rms_median_m': float(np.median(matched)) if matched else None,
                        'matched_rms_max_m': max(matched, default=None),
                        'worst_service_delta_mean': float(np.mean([r['continuous_worst_delta'] for r in values if 'continuous_worst_delta' in r])),
                        'max_abs_worst_service_delta': max(abs(r.get('continuous_worst_delta', 0)) for r in values)})
    return read(study / 'summary.json') | {'strata': results}


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def figures(output, audit, mechanism, stability):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    labels = ['Arc\n256', 'Arc\ncontinuous', 'Euclidean\nobserved', 'Reference\ngeometry', 'Reference\ndemand', 'All\nrobots', 'Reference\n2048']
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for ax, k, color in zip(axes, (2, 5), ('#a65c00', '#006b8f')):
        ax.plot(range(7), np.asarray(audit['concave_20seed_means'][str(k)]) * 100, 'o-', color=color)
        ax.axhline(0, color='gray', lw=1)
        ax.set_xticks(range(7), labels, fontsize=8)
        ax.set_title(f'K = {k}; 20 paired concave inputs')
        ax.set_ylabel('Minimax - perimeter worst unmet (pp)')
        ax.grid(axis='y', alpha=.2)
    fig.suptitle('Fixed-target development audit: ordered metric changes')
    for suffix in ('png', 'svg'):
        fig.savefig(output / ('decomposition.' + suffix), dpi=180)
    plt.close(fig)
    rows = [r for r in mechanism['strata'] if r['group_count'] == 5 and r['baseline'] == 'perimeter']
    combinations = sorted({(r['size_pattern'], r['demand_pattern']) for r in rows})
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for ax, metric, title in zip(axes, ('worst_delta_mean', 'weighted_delta_mean'), ('Worst-group unmet', 'Weighted total unmet')):
        matrix = np.array([[next(r[metric] for r in rows if (r['size_pattern'], r['demand_pattern']) == c and r['resource'] == str(b))
                            for b in (3, 5, 7)] for c in combinations]) * 100
        vmax = max(float(np.max(np.abs(matrix))), .1)
        image = ax.imshow(matrix, cmap='RdBu_r', vmin=-vmax, vmax=vmax, aspect='auto')
        ax.set_xticks(range(3), ['3K', '5K', '7K'])
        ax.set_yticks(range(len(combinations)), [f'{s} size / {d}' for s, d in combinations], fontsize=8)
        ax.set_title(title)
        ax.set_xlabel('Available robot budget')
        for i in range(matrix.shape[0]):
            for j in range(3):
                ax.text(j, i, f'{matrix[i,j]:+.2f}', ha='center', va='center', fontsize=9,
                        color='white' if abs(matrix[i, j]) > .55 * vmax else 'black')
        fig.colorbar(image, ax=ax, label='Minimax - perimeter (pp)', shrink=.8)
    fig.suptitle('K = 5; within-group demand mechanism; 20 seeds per cell')
    for suffix in ('png', 'svg'):
        fig.savefig(output / ('mechanism.' + suffix), dpi=180)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for k, color in ((2, '#a65c00'), (5, '#006b8f')):
        for mode, marker in (('bounded_disk', 'o'), ('common_bias', 's')):
            selected = [r for r in stability['strata'] if r['group_count'] == k and r['measurement_model'] == mode and r['perturbation_m'] > 0]
            x = [r['perturbation_m'] * 1000 for r in selected]
            axes[0].plot(x, [r['count_changes'] / r['recorded'] * 100 for r in selected], marker=marker, color=color,
                         linestyle='-' if mode == 'bounded_disk' else '--', label=f'K{k}, {mode}')
            axes[1].plot(x, [r['matched_rms_max_m'] for r in selected], marker=marker, color=color,
                         linestyle='-' if mode == 'bounded_disk' else '--')
    axes[0].set_ylabel('Inputs changing integer allocation (%)')
    axes[1].set_ylabel('Maximum matched target RMS (m)')
    for ax in axes:
        ax.set_xscale('log')
        ax.set_xlabel('Additional bounded point perturbation (mm)')
        ax.grid(alpha=.2)
    axes[0].legend(fontsize=7)
    fig.suptitle('Static observation perturbations; planning only')
    for suffix in ('png', 'svg'):
        fig.savefig(output / ('stability.' + suffix), dpi=180)
    plt.close(fig)


def run(output, include_confirmation=False, verify_trajectories=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = ROOT / 'runs/static_service_audit_20261026'
    audit = audit_summary(root / 'fixed_action_v1')
    mechanism1 = mechanism_summary(root / 'mechanism_v1')
    mechanism2 = mechanism_summary(root / 'mechanism_v2')
    stability = stability_summary(root / 'stability_v1')
    pressure1, pressure2 = read(root / 'pressure_v1/summary.json'), read(root / 'pressure_v2/summary.json')
    combined = {'fixed_action': audit, 'mechanism_v1': mechanism1, 'mechanism_v2': mechanism2,
                'stability': stability, 'pressure_v1': pressure1, 'pressure_v2': pressure2}
    verification = [verify_study(root / name, verify_trajectories) for name in
                    ('mechanism_v1', 'mechanism_v2', 'stability_v1', 'pressure_v1', 'pressure_v2')]
    if include_confirmation:
        study = ROOT / 'runs/static_multigroup_20261026/confirmation_v2_frozen'
        summary = read(study / 'summary.json')
        ledger = full_ledger_analysis(read(study / 'manifest.json'), records(study))
        combined['confirmation'] = {k: v for k, v in summary.items() if k != 'paired_common_reference_differences'}
        combined['confirmation_full_ledger_bounds'] = ledger
        verification.append(verify_study(study, verify_trajectories))
    for name, value in combined.items():
        target = output / (name + '.json')
        if target.exists():
            raise FileExistsError(target)
        write_json(target, value)
    write_csv(output / 'mechanism_v1.csv', mechanism1['strata'])
    write_csv(output / 'mechanism_v2.csv', mechanism2['strata'])
    write_csv(output / 'stability.csv', stability['strata'])
    figures(output, audit, mechanism2, stability)
    write_json(output / 'verification.json', verification)
    return {'output': str(output), 'confirmation_included': include_confirmation,
            'verified_task_receipts': sum(v['verified_receipts'] for v in verification),
            'trajectories_opened': sum(v['trajectories_opened_without_pickle'] for v in verification)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--include-confirmation', action='store_true')
    parser.add_argument('--verify-trajectories', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.include_confirmation, args.verify_trajectories), indent=2))
