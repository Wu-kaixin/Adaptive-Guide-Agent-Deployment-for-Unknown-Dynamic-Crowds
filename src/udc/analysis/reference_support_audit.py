"""Audit original fixed actions against general-error support enclosures."""
import argparse
import json
from pathlib import Path

import numpy as np
from shapely.geometry import LineString
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import StaticTruth, BoundedMeasurement
from udc.multigroup.geometry import construct_groups
from udc.multigroup.evaluation import reference_groups
from udc.multigroup.reference_support import build_support, certificate
from udc.experiments.static_multigroup import digest, write_json


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def audit(study, output):
    manifest = read(study / 'manifest.json')
    if digest(study / 'manifest.json') != read(study / 'seal.json')['manifest_sha256']:
        raise ValueError('Old manifest changed')
    base = manifest['base_spec']
    config = base['model'] | {'support_closing_enclosure': 'round_buffer_sagitta'}
    checked, pairs, violations, witnesses = 0, [], [], []
    with threadpool_limits(limits=1):
        for sequence in manifest['sequences']:
            if digest(study / sequence['truth']) != sequence['truth_sha256']:
                raise ValueError('Old truth changed')
            with np.load(study / sequence['truth'], allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            reference = reference_groups(truth, base['room_m'], config)
            for frame in sequence['frames']:
                if digest(study / frame['observation']) != frame['observation_sha256']:
                    raise ValueError('Old observation changed')
                with np.load(study / frame['observation'], allow_pickle=False) as data:
                    measurement = BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                        manifest['spec']['declared_error_bound_m'], 'old_independent_or_mixed_bounded')
                groups, _ = construct_groups(measurement, base['room_m'], config)
                support = build_support(measurement, groups, config)
                identity = tuple(g.rows for g in reference) == support.rows
                if support.grouping_certificate['identity_given_original_reference_defined'] and not identity:
                    violations.append({'sequence': sequence['sequence_id'], 'frame': frame['perturbation_m'], 'kind': 'conditional_identity_false'})
                if identity:
                    for index, group in enumerate(reference):
                        line = LineString(np.vstack([group.boundary.deployment, group.boundary.deployment[0]]))
                        outside = line.difference(support.regions[index])
                        if not outside.is_empty:
                            violations.append({'sequence': sequence['sequence_id'], 'frame': frame['perturbation_m'],
                                               'kind': 'original_reference_outside_support', 'outside_length_m': outside.length})
                prefix = sequence['sequence_id'] + f'_p{frame["perturbation_m"]}_'
                folders = {name: study / 'results' / (prefix + name) for name in
                           ('continuous_euclidean_greedy','euclidean_retain_0.002')}
                data_rows, points = {}, {}
                for method, folder in folders.items():
                    receipt = read(folder / 'receipt.json')
                    if any(digest(folder / name) != sha for name, sha in receipt.items()):
                        raise ValueError('Old action artifacts changed')
                    data_rows[method] = read(folder / 'metrics.json')
                    with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                        points[method] = data['targets'].copy()
                greedy, retained = data_rows['continuous_euclidean_greedy'], data_rows['euclidean_retain_0.002']
                cert = certificate(support, points['euclidean_retain_0.002'], points['continuous_euclidean_greedy'], config['max_gap_m']/2)
                loss = retained['continuous_common_service']['worst_group_uncovered'] - greedy['continuous_common_service']['worst_group_uncovered']
                row = {'sequence_id': sequence['sequence_id'], 'seed': sequence['seed'], 'perturbation_m': frame['perturbation_m'],
                       'certificate': cert, 'original_true_worst_loss': loss,
                       'conditional_partition_certificate': support.grouping_certificate, 'actual_partition_matches': identity}
                if cert['accept_retained'] and loss > .002:
                    violations.append({'sequence': sequence['sequence_id'], 'frame': frame['perturbation_m'], 'kind': 'true_loss_exceeds_declared_budget', 'loss': loss})
                pairs.append(row)
                if sequence['seed'] == 2210 and loss > .002:
                    witnesses.append(row)
                checked += 1
    output.mkdir(parents=True, exist_ok=False)
    result = {'old_manifest_sha256': digest(study / 'manifest.json'), 'fixed_pairs': checked,
              'accepted': sum(p['certificate']['accept_retained'] for p in pairs), 'violations': violations,
              'seed2210_counterexample_regressions': witnesses, 'source_actions_reoptimized': False,
              'new_independent_samples': 0, 'reference': 'original_world_reference',
              'condition': 'original reference is defined; float64 geometry audit, not formal machine intervals'}
    write_json(output / 'summary.json', result)
    write_json(output / 'pairs.json', pairs)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(audit(Path(args.study), Path(args.output)), ensure_ascii=False, indent=2), flush=True)
