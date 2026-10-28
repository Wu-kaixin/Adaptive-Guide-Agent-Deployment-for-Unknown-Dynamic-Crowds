"""Save a full-precision witness separating observed and true service budgets."""
import json
from pathlib import Path

import numpy as np

from udc.experiments.static_multigroup import ROOT, digest, write_json


def main():
    study = ROOT / 'runs/static_phase_retention_20261027/main_v1'
    rows = [json.loads(p.read_text(encoding='utf-8')) for p in (study / 'results').glob('*/metrics.json')]
    lookup = {(r['sequence_id'], r['perturbation_m'], r['layout']): r for r in rows if r['kind'] == 'planning'}
    candidates = []
    for row in rows:
        if row['kind'] == 'planning' and row['layout'] == 'euclidean_retain_0.002' and row['perturbation_m'] == .03:
            greedy = lookup[(row['sequence_id'], .03, 'continuous_euclidean_greedy')]
            difference = row['continuous_common_service']['worst_group_uncovered'] - greedy['continuous_common_service']['worst_group_uncovered']
            candidates.append((difference, row, greedy))
    difference, retained, greedy = max(candidates, key=lambda item: item[0])
    if retained['observation_sha256'] != greedy['observation_sha256'] or retained['truth_sha256'] != greedy['truth_sha256']:
        raise ValueError('Counterexample methods do not share frozen input')
    plans = [json.loads((study / 'results' / r['task_id'] / 'planning.json').read_text(encoding='utf-8')) for r in (greedy, retained)]
    if plans[0]['counts'] != plans[1]['counts'] or plans[0]['shared_pool_sources'] != plans[1]['shared_pool_sources']:
        raise ValueError('Counterexample methods do not share counts and candidates')
    result = {'selection': 'descriptive largest true worst-group loss among all completed 30mm primary-policy pairs',
              'sequence_id': retained['sequence_id'], 'group_count': retained['group_count'], 'shape': retained['shape'],
              'seed': retained['seed'], 'base_measurement_model': retained['base_measurement_model'],
              'max_actual_person_position_error_m': retained['max_actual_observation_error_m'],
              'declared_person_position_error_bound_m': retained['error_bound_m'],
              'observed_per_group_budget': .002,
              'observed_worst_delta': retained['observed_worst_u'] - greedy['observed_worst_u'],
              'observed_weighted_delta': retained['observed_weighted_u'] - greedy['observed_weighted_u'],
              'max_assigned_observed_slack': retained['max_assigned_observed_slack'],
              'true_worst_delta': difference,
              'true_weighted_delta': retained['continuous_common_service']['weighted_uncovered'] - greedy['continuous_common_service']['weighted_uncovered'],
              'counts': plans[0]['counts'], 'retained_groups': retained['retained_groups'],
              'observed_group_loss_vector': (np.asarray(plans[1]['selected_assigned_observed_u']) - plans[0]['selected_assigned_observed_u']).tolist(),
              'true_greedy_group_uncovered': [r['uncovered'] for r in greedy['continuous_common_service']['groups']],
              'true_retained_group_uncovered': [r['uncovered'] for r in retained['continuous_common_service']['groups']],
              'task_ids': [greedy['task_id'], retained['task_id']],
              'source_receipts': {r['task_id']: digest(study / 'results' / r['task_id'] / 'receipt.json') for r in (greedy, retained)},
              'motion_evidence': None,
              'interpretation': 'observed-model bound holds; extending that bound to true-reference coverage is falsified by this planning witness'}
    target = ROOT / 'reports/static_phase_retention_20261027/true_service_witness.json'
    write_json(target, result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
