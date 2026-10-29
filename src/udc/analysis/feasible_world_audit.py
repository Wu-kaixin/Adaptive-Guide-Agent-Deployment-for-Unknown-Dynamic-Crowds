"""Read-only reconstruction of saved numeric feasible-world witnesses.

Use the witness study's frozen Python sources. Cross-check signed exchange
against the separate complete-coverage integrator on reconstructed references.
"""
import argparse
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from .feasible_world_witness import read, checked, admissibility, translation_grid
from udc.deployment.planners import demand_profile
from udc.multigroup.evaluation import reference_groups
from udc.multigroup.observation import StaticTruth
from udc.multigroup.service import continuous_euclidean_service
from udc.experiments.static_multigroup import digest, write_json


def audit(study, output):
    study, output = Path(study), Path(output)
    manifest, summary = read(study / 'manifest.json'), read(study / 'summary.json')
    checked(study / 'manifest.json', read(study / 'seal.json')['manifest_sha256'])
    for name, checksum in manifest['sources'].items():
        checked(study / 'source_snapshot' / name, checksum)
    original = Path(manifest['original'])
    checked(original / 'manifest.json', manifest['original_manifest_sha256'])
    old = read(original / 'manifest.json')
    sequences = {s['sequence_id']: s for s in old['sequences']}
    config, room, epsilon = old['base_spec']['model'], old['base_spec']['room_m'], old['spec']['declared_error_bound_m']
    mismatches, maximum_residual, worlds, witness_count, witness_pairs = [], 0., 0, 0, 0
    with threadpool_limits(limits=1):
        for index, selected in enumerate(manifest['selected_pairs']):
            pair = study / 'pairs' / f'{index:03d}'
            for name, checksum in read(pair / 'receipt.json').items():
                checked(pair / name, checksum)
            with np.load(pair / 'shared.npz', allow_pickle=False) as data:
                observations, layouts = data['observations'].copy(), (data['greedy'].copy(), data['retained'].copy())
            sequence = sequences[selected['sequence_id']]
            checked(original / sequence['truth'], sequence['truth_sha256'])
            with np.load(original / sequence['truth'], allow_pickle=False) as data:
                base = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            for k, frame in enumerate(sequence['frames']):
                checked(original / frame['observation'], frame['observation_sha256'])
                with np.load(original / frame['observation'], allow_pickle=False) as data:
                    if not np.array_equal(data['positions'], observations[k]):
                        mismatches.append(f'{index}:OBSERVATION')
            prefix = sequence['sequence_id'] + f'_p{selected["perturbation_m"]}_'
            for layout, method in zip(layouts, ('continuous_euclidean_greedy', 'euclidean_retain_0.002')):
                folder = original / 'results' / (prefix + method)
                for name, checksum in read(folder / 'receipt.json').items():
                    checked(folder / name, checksum)
                with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                    if not np.array_equal(layout, data['targets']):
                        mismatches.append(f'{index}:LAYOUT')
            translations = [None] + translation_grid(base.positions, observations, epsilon, manifest['directions'], tuple(manifest['scales']))
            losses, admissible = [], []
            for number, translation in enumerate(translations):
                folder = pair / ('baseline' if translation is None else f'candidate_{number - 1:03d}')
                for name, checksum in read(folder / 'receipt.json').items():
                    checked(folder / name, checksum)
                with np.load(folder / 'truth.npz', allow_pickle=False) as data:
                    truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
                target = base.positions if translation is None else base.positions + translation
                if not (np.array_equal(truth.positions, target) and np.array_equal(truth.radii, base.radii)
                        and np.array_equal(truth.demand, base.demand) and np.array_equal(truth.group_ids, base.group_ids)):
                    mismatches.append(f'{index}:{number}:TRUTH')
                evidence = read(folder / 'evidence.json')
                physical = admissibility(truth, observations, layouts, room, config, epsilon)
                if any(evidence[k] != v for k, v in physical.items()):
                    mismatches.append(f'{index}:{number}:PHYSICAL')
                valid, net = physical['admissible_before_reference'], None
                if valid and evidence.get('reference_defined'):
                    groups = reference_groups(truth, room, config)
                    group_losses = []
                    with np.load(folder / 'references.npz', allow_pickle=False) as data:
                        for k, group in enumerate(groups):
                            rows = list(group.rows)
                            density = demand_profile(group.boundary, truth.positions[rows], truth.demand[rows], config['bandwidth_m'])
                            if not (np.array_equal(data[f'points_{k}'], group.boundary.deployment)
                                    and np.array_equal(data[f'density_{k}'], density)
                                    and np.array_equal(data[f'rows_{k}'], rows)):
                                mismatches.append(f'{index}:{number}:{k}:REFERENCE')
                            greedy, retained = [continuous_euclidean_service(group.boundary, density, q, config['max_gap_m'] / 2)
                                                for q in layouts]
                            loss = retained['uncovered'] - greedy['uncovered']
                            residual = abs(loss - evidence['group_exchange'][k]['net_uncovered_difference'])
                            maximum_residual = max(maximum_residual, residual)
                            if residual > 1e-12:
                                mismatches.append(f'{index}:{number}:{k}:MASS_BALANCE')
                            group_losses.append(loss)
                    net = max(group_losses)
                else:
                    valid = False
                admissible.append(valid)
                losses.append(net)
                worlds += 1
            found = [k - 1 for k in range(1, len(losses)) if admissible[0] and admissible[k]
                and losses[0] <= manifest['delta'] and losses[k] > manifest['delta'] + manifest['witness_margin']]
            saved = read(pair / 'result.json')
            if found != saved['witness_candidate_indices']:
                mismatches.append(f'{index}:WITNESSES')
            witness_count += len(found)
            witness_pairs += bool(found)
    if witness_count != summary['witness_candidates'] or witness_pairs != summary['witness_pairs']:
        mismatches.append('SUMMARY')
    result = {'study_manifest_sha256': digest(study / 'manifest.json'), 'worlds_reconstructed': worlds,
        'witness_pairs': witness_pairs, 'witness_candidates': witness_count,
        'max_cross_integrator_mass_balance_residual': maximum_residual,
        'mismatches': mismatches, 'all_match': not mismatches,
        'scope': 'float64 reconstruction and separate service integration; not formal interval proof',
        'new_independent_samples': 0, 'new_motion_executions': 0}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'audit.json', result)
    if mismatches:
        raise ValueError('Feasible-world reconstruction mismatch; inspect saved audit')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(audit(args.study, args.output), flush=True)
