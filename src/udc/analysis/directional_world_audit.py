"""Read-only all-world reconstruction, including exact observation-ball tests.

The service cross-check uses complete coverage integrals, independently of the
search's signed exchange integration. Geometry remains the declared float64
model; this audit does not establish a universal geometric enclosure theorem.
"""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from .directional_world_witness import directional_translations, exact_history_feasible
from .feasible_world_witness import read, checked, admissibility, translation_grid
from udc.deployment.planners import demand_profile
from udc.multigroup.evaluation import reference_groups
from udc.multigroup.observation import StaticTruth
from udc.multigroup.service import continuous_euclidean_service
from udc.experiments.static_multigroup import digest, write_json
from udc.experiments.worker_resources import initialize_worker


def _pair(argument):
    study, output, index = argument
    study, output = Path(study), Path(output)
    manifest = read(study / 'manifest.json')
    selected, spec = manifest['selected_pairs'][index], manifest['design']
    original = Path(manifest['original'])
    old = read(original / 'manifest.json')
    sequence = next(s for s in old['sequences'] if s['sequence_id'] == selected['sequence_id'])
    config, room, epsilon = old['base_spec']['model'], old['base_spec']['room_m'], old['spec']['declared_error_bound_m']
    checked(original / sequence['truth'], sequence['truth_sha256'])
    with np.load(original / sequence['truth'], allow_pickle=False) as data:
        base = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
    pair = study / 'pairs' / f'{index:03d}'
    for name, checksum in read(pair / 'receipt.json').items():
        checked(pair / name, checksum)
    with np.load(pair / 'shared.npz', allow_pickle=False) as data:
        observations = data['observations'].copy()
        layouts = (data['greedy'].copy(), data['retained'].copy())
    if len(observations) != len(sequence['frames']):
        raise ValueError('Observation history length changed')
    for k, frame in enumerate(sequence['frames']):
        checked(original / frame['observation'], frame['observation_sha256'])
        with np.load(original / frame['observation'], allow_pickle=False) as data:
            if not (np.array_equal(data['positions'], observations[k])
                    and np.array_equal(data['radii'], base.radii) and np.array_equal(data['demand'], base.demand)):
                raise ValueError('Saved exact observation attributes changed')
    prefix = sequence['sequence_id'] + f'_p{selected["perturbation_m"]}_'
    for layout, method in zip(layouts, ('continuous_euclidean_greedy', 'euclidean_retain_0.002')):
        source = original / 'results' / (prefix + method)
        for name, checksum in read(source / 'receipt.json').items():
            checked(source / name, checksum)
        with np.load(source / 'trajectory.npz', allow_pickle=False) as data:
            if not np.array_equal(layout, data['targets']):
                raise ValueError('Original fixed layout changed')
    movements = [{'translation_m': v, 'group': None, 'mode': 'previous_triangle_subset'}
                 for v in translation_grid(base.positions, observations, epsilon)]
    for group in [None] + [int(g) for g in np.unique(base.group_ids)]:
        rows = np.arange(len(base.positions)) if group is None else np.flatnonzero(base.group_ids == group)
        movements.extend(v | {'group': group, 'mode': 'directional_disk_intersection'}
            for v in directional_translations(base.positions, observations, epsilon, rows, spec['directions'], tuple(spec['scales'])))
    saved_candidates = read(pair / 'candidates.json')
    expected_names = ['baseline'] + [f'candidate_{i:04d}' for i in range(len(movements))]
    actual_names = sorted(p.name for p in pair.iterdir() if p.is_dir())
    if sorted(expected_names) != actual_names or len(saved_candidates) != len(movements):
        raise ValueError('All-world denominator or directory inventory changed')
    results, max_residual, mismatches = [], 0., []
    with threadpool_limits(limits=1):
        for number, name in enumerate(expected_names):
            folder = pair / name
            for artifact, checksum in read(folder / 'receipt.json').items():
                checked(folder / artifact, checksum)
            movement = None if number == 0 else movements[number - 1]
            target = base.positions.copy()
            if movement is not None:
                active = np.ones(len(target), bool) if movement['group'] is None else base.group_ids == movement['group']
                target[active] += movement['translation_m']
            with np.load(folder / 'truth.npz', allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            if not (np.array_equal(truth.positions, target) and np.array_equal(truth.radii, base.radii)
                    and np.array_equal(truth.demand, base.demand) and np.array_equal(truth.group_ids, base.group_ids)):
                raise ValueError(f'{index}:{name}: altered world construction or exact attributes')
            if not exact_history_feasible(truth.positions, observations, epsilon):
                raise ValueError(f'{index}:{name}: exact error-ball violation')
            evidence = read(folder / 'evidence.json')
            if movement is not None and evidence['movement'] != movement:
                raise ValueError('Movement provenance differs from sealed construction')
            physical = admissibility(truth, observations, layouts, room, config, epsilon)
            if any(evidence[k] != value for k, value in physical.items()):
                mismatches.append(f'{name}:PHYSICAL')
            if not physical['admissible_before_reference'] or not evidence.get('reference_defined'):
                raise ValueError('This completed study claims every candidate admissible')
            groups = reference_groups(truth, room, config)
            if {frozenset(g.rows) for g in groups} != {frozenset(np.flatnonzero(truth.group_ids == g)) for g in np.unique(truth.group_ids)}:
                raise ValueError('Physical group partition changed')
            losses = []
            with np.load(folder / 'references.npz', allow_pickle=False) as data:
                if len(data.files) != 3 * len(groups):
                    raise ValueError('Saved reference denominator changed')
                for k, group in enumerate(groups):
                    rows = list(group.rows)
                    density = demand_profile(group.boundary, truth.positions[rows], truth.demand[rows], config['bandwidth_m'])
                    if not (np.array_equal(data[f'points_{k}'], group.boundary.deployment)
                            and np.array_equal(data[f'density_{k}'], density) and np.array_equal(data[f'rows_{k}'], rows)):
                        mismatches.append(f'{name}:{k}:REFERENCE')
                    greedy, retained = [continuous_euclidean_service(group.boundary, density, q, config['max_gap_m'] / 2)
                                        for q in layouts]
                    loss = retained['uncovered'] - greedy['uncovered']
                    residual = abs(loss - evidence['group_exchange'][k]['net_uncovered_difference'])
                    max_residual = max(max_residual, residual)
                    # Numerical cross-integrator diagnostic only; never a service acceptance tolerance.
                    if residual > 1e-12:
                        mismatches.append(f'{name}:{k}:MASS_BALANCE')
                    losses.append(loss)
            net = max(losses)
            witness = bool(number and net > spec['delta'] + spec['witness_margin'])
            if number:
                saved = saved_candidates[number - 1]
                if saved['name'] != name or saved['witness'] != witness or evidence['budget_crossing_witness'] != witness:
                    mismatches.append(f'{name}:WITNESS_CLASSIFICATION')
            elif net > spec['delta']:
                mismatches.append('BASELINE_OVER_BUDGET')
            results.append({'name': name, 'net_loss': net, 'witness': witness})
    result = {'index': index, 'worlds_reconstructed': len(results), 'exact_history_feasible_worlds': len(results),
        'witness_indices': [i for i, r in enumerate(results[1:]) if r['witness']],
        'max_cross_integrator_mass_balance_residual': max_residual, 'mismatches': mismatches, 'all_match': not mismatches}
    saved = read(pair / 'result.json')
    if len(result['witness_indices']) != saved['witness_candidates']:
        result['mismatches'].append('PAIR_SUMMARY')
        result['all_match'] = False
    write_json(output / f'pair_{index:03d}.json', result)
    if not result['all_match']:
        raise ValueError('Directional all-world audit mismatch; inspect saved pair audit')
    return result


def audit(study, output):
    study, output = Path(study).resolve(), Path(output).resolve()
    manifest = read(study / 'manifest.json')
    checked(study / 'manifest.json', read(study / 'seal.json')['manifest_sha256'])
    for name, checksum in manifest['sources'].items():
        checked(study / 'source_snapshot' / name, checksum)
    checked(Path(manifest['original']) / 'manifest.json', manifest['original_manifest_sha256'])
    output.mkdir(parents=True, exist_ok=False)
    with ProcessPoolExecutor(max_workers=manifest['design']['workers'], initializer=initialize_worker) as pool:
        results = list(pool.map(_pair, [(str(study), str(output), i) for i in range(len(manifest['selected_pairs']))]))
    summary = read(study / 'summary.json')
    witnesses = sum(len(r['witness_indices']) for r in results)
    pairs = sum(bool(r['witness_indices']) for r in results)
    parent = Path(manifest['design']['parent'])
    checked(parent / 'manifest.json', manifest['parent_manifest_sha256'])
    parent_indices = [i for i in range(len(results)) if read(parent / 'pairs' / f'{i:03d}' / 'result.json')['witness_candidate_indices']]
    retained = all(set(read(parent / 'pairs' / f'{i:03d}' / 'result.json')['witness_candidate_indices'])
                   <= set(results[i]['witness_indices']) for i in parent_indices)
    result = {'study_manifest_sha256': digest(study / 'manifest.json'),
        'worlds_reconstructed': sum(r['worlds_reconstructed'] for r in results),
        'exact_history_feasible_worlds': sum(r['exact_history_feasible_worlds'] for r in results),
        'witness_pairs': pairs, 'witness_candidates': witnesses,
        'parent_witness_pair_indices': parent_indices, 'all_parent_candidate_witnesses_preserved': retained,
        'witness_pair_indices': [r['index'] for r in results if r['witness_indices']],
        'max_cross_integrator_mass_balance_residual': max(r['max_cross_integrator_mass_balance_residual'] for r in results),
        'all_match': retained and witnesses == summary['witness_candidates'] and pairs == summary['verified_witness_pairs'],
        'scope': 'all saved worlds reconstructed; exact binary observation balls; independent float64 service integrator, not formal geometry proof or motion audit',
        'new_independent_samples': 0, 'new_motion_executions': 0}
    write_json(output / 'audit.json', result)
    if not result['all_match']:
        raise ValueError('Directional summary or parent witness reconstruction failed')
    return result
