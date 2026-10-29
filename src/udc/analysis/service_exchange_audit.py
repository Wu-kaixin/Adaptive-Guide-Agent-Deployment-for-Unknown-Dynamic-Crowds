"""Explain conservative low-resource fallbacks using fixed evaluator truth.

No runtime certification, new input generation, target selection or motion.
Lost and gained mass must be distinguished from their much smaller net effect.
"""
from collections import defaultdict
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from udc.deployment.planners import demand_profile
from udc.multigroup.observation import StaticTruth
from udc.multigroup.evaluation import reference_groups
from udc.multigroup.service import continuous_service_exchange
from udc.experiments.static_multigroup import digest, write_json
from udc.experiments.frozen_resume import atomic_json


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checked(path, expected):
    if digest(path) != expected:
        raise ValueError('Fixed service exchange artifact changed: ' + str(path))


def summarize(rows, delta):
    valid = [row for row in rows if row['evaluable']]
    return {'pairs': len(rows), 'evaluable': len(valid), 'not_evaluable': len(rows) - len(valid),
        'net_within_budget': sum(row['max_group_net_loss'] <= delta for row in valid),
        'gross_loss_within_budget': sum(row['max_group_lost_mass'] <= delta for row in valid),
        'net_within_but_gross_outside': sum(row['max_group_net_loss'] <= delta < row['max_group_lost_mass'] for row in valid),
        'max_numeric_mass_balance_residual': max((row['max_numeric_mass_balance_residual'] for row in valid), default=None),
        'maximum_net_group_loss': max((row['max_group_net_loss'] for row in valid), default=None),
        'maximum_gross_lost_mass': max((row['max_group_lost_mass'] for row in valid), default=None),
        'median_directed_layout_displacement_m': float(np.median([row['directed_layout_displacement_m'] for row in valid])) if valid else None}


def audit(original, output):
    original, output = Path(original), Path(output)
    manifest = read(original / 'manifest.json')
    checked(original / 'manifest.json', read(original / 'seal.json')['manifest_sha256'])
    config, room = manifest['base_spec']['model'], manifest['base_spec']['room_m']
    rows, references = [], {}
    with threadpool_limits(limits=1):
        for sequence_index, sequence in enumerate(manifest['sequences']):
            checked(original / sequence['truth'], sequence['truth_sha256'])
            key = sequence['truth_sha256']
            if key not in references:
                with np.load(original / sequence['truth'], allow_pickle=False) as data:
                    truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
                groups = reference_groups(truth, room, config)
                densities = [demand_profile(group.boundary, truth.positions[list(group.rows)],
                    truth.demand[list(group.rows)], config['bandwidth_m']) for group in groups]
                references[key] = groups, densities
            groups, densities = references[key]
            for frame in sequence['frames']:
                checked(original / frame['observation'], frame['observation_sha256'])
                prefix = sequence['sequence_id'] + f'_p{frame["perturbation_m"]}_'
                points, metrics = {}, {}
                for method in ('continuous_euclidean_greedy', 'euclidean_retain_0.002'):
                    folder = original / 'results' / (prefix + method)
                    for name, checksum in read(folder / 'receipt.json').items():
                        checked(folder / name, checksum)
                    metrics[method] = read(folder / 'metrics.json')
                    with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                        points[method] = data['targets'].copy()
                first, second = metrics.values()
                row = {'sequence_id': sequence['sequence_id'], 'seed': sequence['seed'],
                    'perturbation_m': frame['perturbation_m'], 'evaluator_only': True,
                    'evaluable': 'continuous_common_service' in first and 'continuous_common_service' in second}
                if row['evaluable']:
                    greedy, retained = points.values()
                    exchange = [continuous_service_exchange(group.boundary, density, greedy, retained, config['max_gap_m'] / 2)
                                for group, density in zip(groups, densities)]
                    losses = [b['uncovered'] - a['uncovered'] for a, b in zip(
                        first['continuous_common_service']['groups'], second['continuous_common_service']['groups'])]
                    residuals = [value['net_uncovered_difference'] - loss for value, loss in zip(exchange, losses)]
                    h = float(np.linalg.norm(greedy[:, None] - retained[None], axis=2).min(axis=1).max()) if len(greedy) else 0.
                    row.update(group_exchange=exchange, stored_original_group_net_losses=losses,
                        max_group_net_loss=max(losses), max_group_lost_mass=max(value['lost_mass_fraction'] for value in exchange),
                        max_numeric_mass_balance_residual=max(abs(value) for value in residuals),
                        directed_layout_displacement_m=h)
                rows.append(row)
            atomic_json(output / 'status.json', {'phase': 'running', 'completed_sequences': sequence_index + 1,
                'scheduled_sequences': len(manifest['sequences']), 'fixed_pairs': len(rows), 'new_independent_samples': 0})
    grouped = defaultdict(list)
    for row in rows:
        grouped[str(row['perturbation_m'])].append(row)
    result = {'evidence': 'fixed_old_action_evaluator_development_not_a_certificate',
        'new_independent_samples': 0, 'source_actions_reoptimized': False, 'new_motion_executions': 0,
        'delta': .002, 'all_pairs': summarize(rows, .002),
        'by_perturbation': {key: summarize(value, .002) for key, value in grouped.items()},
        'controller_may_use_truth_oracle_to_accept': False,
        'seed2210_over_budget': [row for row in rows if row['seed'] == 2210 and row.get('max_group_net_loss', 0.) > .002],
        'scope': 'Original fixed polyline and nodal density; numeric mass-balance residual reported without changing scientific thresholds'}
    write_json(output / 'pairs.json', rows)
    write_json(output / 'summary.json', result)
    atomic_json(output / 'status.json', {'phase': 'completed', 'fixed_pairs': len(rows), 'new_independent_samples': 0})
    return result
