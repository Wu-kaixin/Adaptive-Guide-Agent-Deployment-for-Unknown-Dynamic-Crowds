"""Evaluator-only, finite indistinguishable-world searches on old fixed actions.

The observation history and layouts stay identical. These are counterfactual
model witnesses, not new generator samples, runtime certificates or motion runs.
"""
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from udc.deployment.planners import demand_profile
from udc.interfaces import Observation
from udc.multigroup.evaluation import reference_groups
from udc.multigroup.geometry import ScientificFailure
from udc.multigroup.observation import StaticTruth, static_groups
from udc.multigroup.service import continuous_service_exchange
from udc.experiments.static_multigroup import digest, write_json
from udc.experiments.frozen_resume import atomic_json


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checked(path, expected):
    if digest(path) != expected:
        raise ValueError('Witness source changed: ' + str(path))


def translation_grid(positions, observations, epsilon, directions=16, scales=(.5, 1.)):
    """Triangle-inequality subset of the intersection of ALL observation balls.

    This finite search is deliberately incomplete. The candidate is checked
    again using the actual saved float64 coordinates before it can be a witness.
    """
    positions, observations = np.asarray(positions), np.asarray(observations)
    error = float(np.linalg.norm(observations - positions, axis=-1).max())
    slack = epsilon - error
    if slack <= 0:
        return []
    return [(.9 * slack * scale * np.array([np.cos(2 * np.pi * k / directions),
                                           np.sin(2 * np.pi * k / directions)])).tolist()
            for scale in scales for k in range(directions)]


def admissibility(truth, observations, layouts, room, config, epsilon):
    """Strict physical and observation checks, with no added acceptance epsilon."""
    error = float(np.linalg.norm(observations - truth.positions, axis=-1).max())
    human_wall = float(np.minimum(truth.positions - truth.radii[:, None],
        np.asarray(room) - truth.positions - truth.radii[:, None]).min())
    i, j = np.triu_indices(len(truth.positions), 1)
    human_pair = float((np.linalg.norm(truth.positions[i] - truth.positions[j], axis=1)
        - truth.radii[i] - truth.radii[j]).min()) if len(i) else None
    layout_rows = []
    for points in layouts:
        a, b = np.triu_indices(len(points), 1)
        layout_rows.append({
            'human_clearance_m': float((np.linalg.norm(points[:, None] - truth.positions[None], axis=2)
                - truth.radii[None] - config['guide_radius_m'] - config['clearance_m']).min()),
            'wall_clearance_m': float(np.minimum(points, np.asarray(room) - points).min() - config['wall_distance_m']),
            'guide_clearance_m': float((np.linalg.norm(points[a] - points[b], axis=1)
                - config['guide_distance_m']).min()) if len(a) else None})
    margins = [human_wall, human_pair] + [v for row in layout_rows for v in row.values()]
    expected = tuple(tuple(np.flatnonzero(truth.group_ids == label)) for label in np.unique(truth.group_ids))
    partition = static_groups(Observation(truth.positions, truth.radii, truth.demand), config['group_link_m'])
    valid = error <= epsilon and all(v is None or v >= 0 for v in margins) and partition == expected
    return {'admissible_before_reference': bool(valid), 'max_history_error_m': error,
        'declared_error_m': epsilon, 'human_wall_clearance_m': human_wall,
        'human_pair_clearance_m': human_pair, 'layouts': layout_rows,
        'true_partition_matches_original': partition == expected}


def evaluate_world(truth, observations, layouts, room, config, epsilon):
    evidence = admissibility(truth, observations, layouts, room, config, epsilon)
    if not evidence['admissible_before_reference']:
        return evidence, []
    try:
        groups = reference_groups(truth, room, config)
    except ScientificFailure as exc:
        evidence.update(reference_defined=False, reference_failure=exc.status, reference_evidence=exc.evidence)
        return evidence, []
    references, exchanges = [], []
    for group in groups:
        rows = list(group.rows)
        density = demand_profile(group.boundary, truth.positions[rows], truth.demand[rows], config['bandwidth_m'])
        references.append((group, density))
        exchanges.append(continuous_service_exchange(group.boundary, density, *layouts, config['max_gap_m'] / 2))
    evidence.update(reference_defined=True, group_exchange=exchanges,
        max_group_net_loss=max(row['net_uncovered_difference'] for row in exchanges))
    return evidence, references


def crosses_budget(baseline, candidate, delta, margin):
    return bool(baseline.get('reference_defined') and candidate.get('reference_defined')
        and baseline['max_group_net_loss'] <= delta
        and candidate['max_group_net_loss'] > delta + margin)


def save_world(folder, truth, evidence, references):
    folder.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(folder / 'truth.npz', positions=truth.positions, radii=truth.radii,
        demand=truth.demand, group_ids=truth.group_ids)
    arrays = {}
    for index, (group, density) in enumerate(references):
        arrays[f'points_{index}'] = group.boundary.deployment
        arrays[f'density_{index}'] = density
        arrays[f'rows_{index}'] = np.asarray(group.rows, int)
    np.savez_compressed(folder / 'references.npz', **arrays)
    write_json(folder / 'evidence.json', evidence)
    write_json(folder / 'receipt.json', {name: digest(folder / name)
        for name in ('truth.npz', 'references.npz', 'evidence.json')})


def search(study):
    study = Path(study)
    manifest = read(study / 'manifest.json')
    checked(study / 'manifest.json', read(study / 'seal.json')['manifest_sha256'])
    original = Path(manifest['original'])
    checked(original / 'manifest.json', manifest['original_manifest_sha256'])
    old = read(original / 'manifest.json')
    config, room = old['base_spec']['model'], old['base_spec']['room_m']
    epsilon = old['spec']['declared_error_bound_m']
    sequences = {s['sequence_id']: s for s in old['sequences']}
    results = []
    with threadpool_limits(limits=1):
        for index, selected in enumerate(manifest['selected_pairs']):
            sequence = sequences[selected['sequence_id']]
            checked(original / sequence['truth'], sequence['truth_sha256'])
            with np.load(original / sequence['truth'], allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            observations = []
            for frame in sequence['frames']:
                checked(original / frame['observation'], frame['observation_sha256'])
                with np.load(original / frame['observation'], allow_pickle=False) as data:
                    if not np.array_equal(data['radii'], truth.radii) or not np.array_equal(data['demand'], truth.demand):
                        raise ValueError('History does not share exact observable attributes')
                    observations.append(data['positions'].copy())
            observations = np.array(observations)
            prefix = sequence['sequence_id'] + f'_p{selected["perturbation_m"]}_'
            layouts = []
            for method in ('continuous_euclidean_greedy', 'euclidean_retain_0.002'):
                folder = original / 'results' / (prefix + method)
                for name, checksum in read(folder / 'receipt.json').items():
                    checked(folder / name, checksum)
                with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                    layouts.append(data['targets'].copy())
            folder = study / 'pairs' / f'{index:03d}'
            folder.mkdir(parents=True, exist_ok=False)
            np.savez_compressed(folder / 'shared.npz', observations=observations, greedy=layouts[0], retained=layouts[1])
            baseline, references = evaluate_world(truth, observations, layouts, room, config, epsilon)
            save_world(folder / 'baseline', truth, baseline, references)
            if not baseline.get('reference_defined'):
                raise ValueError('Selected original world is not admissible; preserve evidence and inspect')
            residual = abs(baseline['max_group_net_loss'] - selected['max_group_net_loss'])
            if residual > 1e-12:
                raise ValueError('Baseline diagnostic changed; do not change scientific thresholds')
            candidates, witnesses = [], []
            for number, translation in enumerate(translation_grid(truth.positions, observations, epsilon,
                    manifest['directions'], tuple(manifest['scales']))):
                candidate = StaticTruth(truth.positions + translation, truth.radii, truth.demand, truth.group_ids)
                evidence, references = evaluate_world(candidate, observations, layouts, room, config, epsilon)
                evidence['translation_m'] = translation
                witness = crosses_budget(baseline, evidence, manifest['delta'], manifest['witness_margin'])
                evidence['budget_crossing_witness'] = witness
                save_world(folder / f'candidate_{number:03d}', candidate, evidence, references)
                candidates.append(evidence)
                if witness:
                    witnesses.append(number)
            result = selected | {'index': index, 'baseline_reconstruction_residual': residual,
                'tested_candidates': len(candidates), 'admissible_reference_candidates': sum(c.get('reference_defined', False) for c in candidates),
                'witness_candidate_indices': witnesses,
                'maximum_alternate_group_loss': max((c['max_group_net_loss'] for c in candidates if c.get('reference_defined')), default=None)}
            write_json(folder / 'result.json', result)
            write_json(folder / 'receipt.json', {name: digest(folder / name) for name in ('shared.npz', 'result.json')})
            results.append(result)
            atomic_json(study / 'status.json', {'phase': 'running', 'completed_pairs': len(results),
                'selected_pairs': len(manifest['selected_pairs']), 'witness_pairs': sum(bool(r['witness_candidate_indices']) for r in results)})
    summary = {'evidence': manifest['evidence'], 'new_independent_samples': 0, 'new_motion_executions': 0,
        'selected_pairs': len(results), 'tested_candidates': sum(r['tested_candidates'] for r in results),
        'admissible_reference_candidates': sum(r['admissible_reference_candidates'] for r in results),
        'witness_pairs': sum(bool(r['witness_candidate_indices']) for r in results),
        'witness_candidates': sum(len(r['witness_candidate_indices']) for r in results),
        'delta': manifest['delta'], 'witness_margin': manifest['witness_margin'],
        'selection': manifest['selection'], 'same_all_four_observations_and_fixed_layouts': True,
        'controller_certificate_implemented': False, 'finite_search_failure_is_certificate': False,
        'scope': 'float64 terminal-service model witness; not interval proof, motion/recovery validation or independent confirmation'}
    write_json(study / 'results.json', results)
    write_json(study / 'summary.json', summary)
    atomic_json(study / 'status.json', {'phase': 'completed', **summary})
    return summary
