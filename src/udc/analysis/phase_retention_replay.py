"""Reconstruct saved observation-only decisions and re-evaluate actual motion."""
import argparse
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from udc.experiments.static_multigroup import ROOT, digest, write_json
from udc.experiments.static_phase_retention import load_measurement, layout_movement
from udc.experiments.static_mechanism import continuous_summary
from udc.multigroup.observation import StaticTruth
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.allocation import allocate, build_catalog
from udc.multigroup.phase_retention import GREEDY, METHODS, phase_pool, choose_groups
from udc.multigroup.evaluation import reference_groups, evaluate
from udc.multigroup.execution import Execution
from .phase_retention_analysis import verify_study, read


def replay(study):
    study = Path(study).resolve()
    verified = verify_study(study)
    manifest = read(study / 'manifest.json')
    spec, base = manifest['spec'], manifest['base_spec']
    critical = ['src/udc/multigroup/' + name + '.py' for name in
                ('phase_retention', 'allocation', 'service', 'geometry', 'observation', 'evaluation', 'execution')]
    critical += ['src/udc/deployment/planners.py']
    critical += ['src/udc/experiments/static_phase_retention.py', 'src/udc/experiments/static_mechanism.py']
    if any(digest(ROOT / name) != manifest['sources'][name] for name in critical):
        raise ValueError('Controller/evaluator source differs from frozen study; use saved source instead')
    checked_plans, checked_motion, mismatches = 0, 0, []
    with threadpool_limits(limits=1):
        for index, sequence in enumerate(manifest['sequences']):
            with np.load(study / sequence['truth'], allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            nominal = load_measurement(study, sequence['frames'][0], spec)
            nominal_layouts = {}
            try:
                groups, domain = construct_groups(nominal, base['room_m'], base['model'])
                catalogs = build_catalog(groups, nominal, domain, sequence['budget'], 'projected_mass', base['model'])
                counts = tuple(int(n) for n in allocate(catalogs, groups, sequence['budget'], 'minimax').counts)
                initial = [phase_pool(g, nominal, domain, n, base['model']) for g, n in zip(groups, counts)]
                for method in GREEDY:
                    _, _, selected, _ = choose_groups(initial, method)
                    nominal_layouts[method] = tuple(c.positions for c in selected)
                reference = reference_groups(truth, base['room_m'], base['model'])
            except ScientificFailure:
                continue  # Original failed rows stay verified in the complete ledger.
            for frame in sequence['frames']:
                obs = load_measurement(study, frame, spec)
                try:
                    groups, domain = construct_groups(obs, base['room_m'], base['model'])
                    pools = [phase_pool(g, obs, domain, n, base['model'], None if frame['perturbation_m'] == 0 else
                                 {m: nominal_layouts[m][i] for m in GREEDY}) for i, (g, n) in enumerate(zip(groups, counts))]
                except ScientificFailure:
                    continue
                for method in METHODS:
                    task_id = sequence['sequence_id'] + f"_p{frame['perturbation_m']}_{method}"
                    folder = study / 'results' / task_id
                    if not (folder / 'receipt.json').exists():
                        continue
                    saved = read(folder / 'metrics.json')
                    if 'continuous_common_service' not in saved:
                        continue
                    planning = read(folder / 'planning.json')
                    targets, ids, selected, evidence = choose_groups(pools, method)
                    original_method = method if method in GREEDY else 'continuous_euclidean_greedy'
                    movement = layout_movement(targets, np.vstack(nominal_layouts[original_method]))
                    fresh = continuous_summary(reference, truth, targets, base['model'])
                    with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                        same = np.array_equal(data['targets'], targets) and np.array_equal(data['target_groups'], ids)
                    same = (same and fresh == saved['continuous_common_service']
                            and all(movement[k] == saved[k] for k in movement)
                            and list(counts) == planning['counts']
                            and [c.source for c in selected] == planning['sources']
                            and list(evidence) == planning['selection_evidence'])
                    checked_plans += 1
                    if not same:
                        mismatches.append(task_id)
                for folder in (study / 'results').glob(sequence['sequence_id'] + '_*'):
                    saved = read(folder / 'metrics.json')
                    if saved['kind'] == 'planning' or saved['perturbation_m'] != frame['perturbation_m'] or 'motion' not in saved:
                        continue
                    state = read(folder / 'state.json')
                    with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                        execution = Execution(data['positions'], data['velocities'], data['assignment'], state['execution_status'], (), state['evidence'])
                        metrics = evaluate(execution, data['targets'], data['target_groups'], groups, obs, truth, base['room_m'], base['model'])
                        actual = continuous_summary(reference, truth, execution.positions[-1][execution.assignment >= 0], base['model']) if len(execution.velocities) else None
                    checked_motion += 1
                    if any(metrics[k] != saved[k] for k in metrics) or actual != saved['continuous_actual_service']:
                        mismatches.append(saved['task_id'])
            if (index + 1) % 20 == 0:
                print(json.dumps({'replayed_sequences': index + 1, 'plans': checked_plans, 'motion': checked_motion}), flush=True)
    result = verified | {'reconstructed_planning_rows': checked_plans, 'recomputed_motion_rows': checked_motion,
                         'mismatches': mismatches, 'all_match': not mismatches,
                         'mode': 'offline reconstruction and saved-trajectory replay; no scientific retry'}
    write_json(study / 'offline_replay.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.study), indent=2))
