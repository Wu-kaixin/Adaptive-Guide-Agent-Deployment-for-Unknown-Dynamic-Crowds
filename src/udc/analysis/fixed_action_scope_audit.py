"""Audit old fixed actions and reject unsupported service certificates."""
import argparse
import json
from pathlib import Path

import numpy as np
from udc.experiments.static_multigroup import digest, write_json
from udc.multigroup.translation_service_bound import retention_certificate


def run(study, output):
    study, output = Path(study), Path(output)
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    seal = json.loads((study / 'seal.json').read_text(encoding='utf-8'))
    if digest(study / 'manifest.json') != seal['manifest_sha256']:
        raise ValueError('Original manifest changed')
    rows, pairs, witnesses = [], [], []
    for folder in (study / 'results').iterdir():
        if not folder.is_dir():
            continue
        receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
        if any(digest(folder / name) != sha for name, sha in receipt.items()):
            raise ValueError('Old artifact changed: ' + folder.name)
        row = json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))
        if row.get('kind') == 'planning':
            rows.append(row)
    lookup = {r['task_id']: r for r in rows}
    for row in rows:
        if row.get('layout') != 'euclidean_retain_0.002':
            continue
        baseline = lookup[row['task_id'].replace('euclidean_retain_0.002', 'continuous_euclidean_greedy')]
        certificate = retention_certificate(None, [], [], 'old_reobservation_independent_or_mixed', .12)
        if certificate['status'] != 'UNCERTIFIED' or certificate['accept_retained']:
            raise ValueError('Unsupported original study incorrectly certified')
        loss = row['continuous_common_service']['worst_group_uncovered'] - baseline['continuous_common_service']['worst_group_uncovered']
        pair = {'task_id': row['task_id'], 'loss': loss, 'certificate': certificate,
                'metric': 'original continuous world-reference worst-group gap',
                'original_receipt_sha256': digest(study / 'results' / row['task_id'] / 'receipt.json')}
        pairs.append(pair)
        if row['seed'] == 2210 and row['perturbation_m'] == .03:
            with np.load(study / row['truth'], allow_pickle=False) as truth, np.load(study / row['observation'], allow_pickle=False) as obs:
                delta = obs['positions'] - truth['positions']
                pair['maximum_person_error_m'] = float(np.linalg.norm(delta, axis=1).max())
                pair['maximum_residual_from_first_person_bias_m'] = float(np.linalg.norm(delta - delta[0], axis=1).max())
            witnesses.append(pair)
    result = {'old_manifest_sha256': seal['manifest_sha256'], 'fixed_planning_records': len(rows),
              'retention_greedy_pairs': len(pairs), 'unsupported_pairs_rejected': len(pairs),
              'maximum_original_true_loss': max(p['loss'] for p in pairs),
              'seed2210_30mm_witnesses': witnesses, 'source_actions_reoptimized': False,
              'new_independent_samples': 0,
              'limit': 'Conservative rejection regression; does not solve original general-error service bound'}
    write_json(output / 'fixed_action_scope_audit.json', result)
    write_json(output / 'fixed_action_pairs.json', pairs)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.study, args.output), ensure_ascii=False, indent=2))
