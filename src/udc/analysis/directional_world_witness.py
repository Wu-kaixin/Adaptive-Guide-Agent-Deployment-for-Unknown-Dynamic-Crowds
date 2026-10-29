"""Stronger evaluator-only worlds inside the same bounded observation history."""
from fractions import Fraction as F
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from .feasible_world_witness import read, checked, translation_grid, evaluate_world, save_world
from udc.multigroup.observation import StaticTruth
from udc.multigroup.evaluation import reference_groups
from udc.multigroup.service import continuous_euclidean_service
from udc.deployment.planners import demand_profile
from udc.experiments.static_multigroup import digest, write_json


def directional_translations(positions, observations, epsilon, rows, directions=16, scales=(.5, .9)):
    """Intersect every selected person's observation disk with a common ray.

    This computes candidate worlds, never a certificate. A positive margin
    factor keeps candidates away from the disk boundaries; actual coordinates
    are separately checked, including exact binary-rational checks for witnesses.
    """
    positions, observations = np.asarray(positions, float), np.asarray(observations, float)
    rows = np.asarray(rows, int)
    relative = positions[rows][None] - observations[:, rows]
    offset = np.sum(relative * relative, axis=-1) - epsilon * epsilon
    if np.any(offset > 0) or not np.isfinite(relative).all() or epsilon <= 0:
        raise ValueError('Ray search needs an initially feasible bounded-error world')
    result = []
    for k in range(directions):
        u = np.array([np.cos(2 * np.pi * k / directions), np.sin(2 * np.pi * k / directions)])
        norm2 = float(u @ u)
        projection = relative @ u
        roots = (-projection + np.sqrt(projection * projection - norm2 * offset)) / norm2
        limit = float(roots.min())
        for scale in scales:
            result.append({'translation_m': (u * limit * scale).tolist(), 'ray_limit_m': limit,
                'scale': scale, 'direction_index': k})
    return result


def exact_history_feasible(positions, observations, epsilon):
    limit = F.from_float(float(epsilon)) ** 2
    for frame in observations:
        for point, measured in zip(positions, frame):
            square = sum((F.from_float(float(a)) - F.from_float(float(b))) ** 2 for a, b in zip(point, measured))
            if square > limit:
                return False
    return True


def verify_world(folder, observations, layouts, room, config, epsilon, original_truth):
    for name, checksum in read(folder / 'receipt.json').items():
        checked(folder / name, checksum)
    with np.load(folder / 'truth.npz', allow_pickle=False) as data:
        truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
    if not (np.array_equal(truth.radii, original_truth.radii) and np.array_equal(truth.demand, original_truth.demand)
            and np.array_equal(truth.group_ids, original_truth.group_ids)):
        raise ValueError('Witness changed exact observable attributes or static identities')
    evidence, _ = evaluate_world(truth, observations, layouts, room, config, epsilon)
    if not evidence.get('reference_defined') or not exact_history_feasible(truth.positions, observations, epsilon):
        raise ValueError('Saved witness is not an admissible world')
    saved = read(folder / 'evidence.json')
    groups = reference_groups(truth, room, config)
    losses, residuals = [], []
    with np.load(folder / 'references.npz', allow_pickle=False) as data:
        for index, group in enumerate(groups):
            rows = list(group.rows)
            density = demand_profile(group.boundary, truth.positions[rows], truth.demand[rows], config['bandwidth_m'])
            if not (np.array_equal(data[f'points_{index}'], group.boundary.deployment)
                    and np.array_equal(data[f'density_{index}'], density)):
                raise ValueError('Witness reference reconstruction changed')
            greedy, retained = [continuous_euclidean_service(group.boundary, density, points, config['max_gap_m'] / 2)
                                for points in layouts]
            loss = retained['uncovered'] - greedy['uncovered']
            losses.append(loss)
            residuals.append(abs(loss - saved['group_exchange'][index]['net_uncovered_difference']))
    return {'exact_history_error_balls_respected': True, 'max_group_net_loss': max(losses),
        'max_cross_integrator_residual': max(residuals),
        'scope': 'exact binary error-ball test, reconstructed reference and separate float64 service integrator; not whole-kernel interval proof'}


def worker(argument):
    study, index, selected = argument
    study = Path(study)
    manifest = read(study / 'manifest.json')
    original = Path(manifest['original'])
    old = read(original / 'manifest.json')
    sequence = next(s for s in old['sequences'] if s['sequence_id'] == selected['sequence_id'])
    checked(original / sequence['truth'], sequence['truth_sha256'])
    with np.load(original / sequence['truth'], allow_pickle=False) as data:
        truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
    observations = []
    for frame in sequence['frames']:
        checked(original / frame['observation'], frame['observation_sha256'])
        with np.load(original / frame['observation'], allow_pickle=False) as data:
            if not np.array_equal(data['radii'], truth.radii) or not np.array_equal(data['demand'], truth.demand):
                raise ValueError('Original exact observable attributes changed')
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
    config, room = old['base_spec']['model'], old['base_spec']['room_m']
    epsilon = old['spec']['declared_error_bound_m']
    spec = manifest['design']
    with threadpool_limits(limits=1):
        baseline, references = evaluate_world(truth, observations, layouts, room, config, epsilon)
        if not baseline.get('reference_defined') or baseline['max_group_net_loss'] > spec['delta']:
            raise ValueError('Selected baseline world is not eligible')
        save_world(folder / 'baseline', truth, baseline, references)
        movements = [{'translation_m': v, 'group': None, 'mode': 'previous_triangle_subset'}
                     for v in translation_grid(truth.positions, observations, epsilon)]
        for group in [None] + [int(label) for label in np.unique(truth.group_ids)]:
            rows = np.arange(len(truth.positions)) if group is None else np.flatnonzero(truth.group_ids == group)
            movements.extend(v | {'group': group, 'mode': 'directional_disk_intersection'}
                for v in directional_translations(truth.positions, observations, epsilon, rows, spec['directions'], tuple(spec['scales'])))
        records, best = [], None
        for number, movement in enumerate(movements):
            positions = truth.positions.copy()
            active = np.ones(len(positions), bool) if movement['group'] is None else truth.group_ids == movement['group']
            positions[active] += movement['translation_m']
            candidate = StaticTruth(positions, truth.radii, truth.demand, truth.group_ids)
            evidence, references = evaluate_world(candidate, observations, layouts, room, config, epsilon)
            evidence['movement'] = movement
            witness = bool(evidence.get('reference_defined') and evidence['max_group_net_loss'] > spec['delta'] + spec['witness_margin'])
            evidence['budget_crossing_witness'] = witness
            name = f'candidate_{number:04d}'
            save_world(folder / name, candidate, evidence, references)
            records.append({'name': name, 'admissible_reference': bool(evidence.get('reference_defined')),
                'witness': witness, 'mode': movement['mode'], 'group': movement['group'],
                'loss': evidence.get('max_group_net_loss')})
            if witness and (best is None or evidence['max_group_net_loss'] > best['loss']):
                best = {'name': name, 'loss': evidence['max_group_net_loss']}
        with np.load(folder / 'shared.npz', allow_pickle=False) as data:
            saved_observations = data['observations'].copy()
            saved_layouts = (data['greedy'].copy(), data['retained'].copy())
        if not (np.array_equal(saved_observations, observations)
                and all(np.array_equal(a, b) for a, b in zip(saved_layouts, layouts))):
            raise ValueError('Saved shared observations or layouts changed')
        baseline_audit = verify_world(folder / 'baseline', saved_observations, saved_layouts, room, config, epsilon, truth)
        witness_audit = verify_world(folder / best['name'], saved_observations, saved_layouts, room, config, epsilon, truth) if best else None
        if baseline_audit['max_group_net_loss'] > spec['delta'] or (witness_audit is not None
                and witness_audit['max_group_net_loss'] <= spec['delta'] + spec['witness_margin']):
            raise ValueError('Separate service integrator did not verify a claimed witness')
    result = selected | {'index': index, 'tested_candidates': len(records),
        'admissible_reference_candidates': sum(r['admissible_reference'] for r in records),
        'witness_candidates': sum(r['witness'] for r in records), 'best': best,
        'baseline_audit': baseline_audit, 'best_witness_audit': witness_audit,
        'no_witness_is_certificate': False, 'new_independent_samples': 0}
    write_json(folder / 'candidates.json', records)
    write_json(folder / 'result.json', result)
    write_json(folder / 'receipt.json', {n: digest(folder / n) for n in ('shared.npz', 'candidates.json', 'result.json')})
    return result
