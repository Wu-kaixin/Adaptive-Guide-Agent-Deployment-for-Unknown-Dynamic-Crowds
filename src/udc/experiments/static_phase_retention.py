"""Frozen-truth paired re-observation study of phase scoring and retention."""
import argparse
from collections import Counter
import itertools
import json
from pathlib import Path
import shutil
import traceback

import numpy as np
import yaml
from scipy.optimize import linear_sum_assignment
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import StaticTruth, BoundedMeasurement, measure
from udc.multigroup.scenarios import make_truth, initial_guides
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.allocation import allocate, build_catalog
from udc.multigroup.phase_retention import METHODS, GREEDY, phase_pool, choose_groups, admissible
from udc.multigroup.evaluation import reference_groups, evaluate, partition_matches
from udc.multigroup.execution import execute
from udc.experiments.static_mechanism import continuous_summary
from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
    environment_record, _blank_execution, _save_task)


def prepare(protocol, output):
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    base = yaml.safe_load((ROOT / spec['base_protocol']).read_text(encoding='utf-8'))
    if output.exists():
        raise FileExistsError('Use a new phase-selection study directory')
    if spec['methods'] != list(METHODS) or spec['seeds']['count'] != 20:
        raise ValueError('The method cohort and 20-seed repetitions are frozen')
    output.mkdir(parents=True)
    shutil.copyfile(protocol, output / 'protocol.yaml')
    sequences = []
    for k, shape, seed in itertools.product(spec['group_counts'], spec['shapes'],
            range(spec['seeds']['start'], spec['seeds']['start'] + spec['seeds']['count'])):
        truth = make_truth(k, shape, seed, spec['namespace'], base['group_size_multipliers'], base['group_hotspot_multipliers'])
        physical_id = f'K{k}_{shape}_s{seed}'
        folder = output / 'inputs' / physical_id
        folder.mkdir(parents=True)
        np.savez_compressed(folder / 'truth.npz', positions=truth.positions, radii=truth.radii, demand=truth.demand,
                            group_ids=truth.group_ids, guides=initial_guides(k * spec['budget_per_group'], base['room_m']))
        shape_id = spec['shapes'].index(shape)
        rng = np.random.default_rng(np.random.SeedSequence([spec['namespace'], seed, k, shape_id, 913]))
        angle = rng.uniform(0, 2 * np.pi, len(truth.positions))
        radius = np.sqrt(rng.uniform(0, 1, len(truth.positions)))
        direction = radius[:, None] * np.c_[np.cos(angle), np.sin(angle)]
        for mode in spec['base_measurement_models']:
            nominal = measure(truth, spec['base_actual_error_bound_m'],
                              np.random.SeedSequence([spec['namespace'], seed, k, shape_id]), mode)
            frames = []
            for eta in spec['additional_perturbation_m']:
                path = folder / f'{mode}_p{eta}.npz'
                np.savez_compressed(path, positions=nominal.observation.positions + eta * direction,
                                    radii=truth.radii, demand=truth.demand)
                frames.append({'perturbation_m': eta, 'observation': str(path.relative_to(output)),
                               'observation_sha256': digest(path)})
            sequences.append({'sequence_id': physical_id + '_' + mode, 'physical_id': physical_id,
                              'group_count': k, 'shape': shape, 'seed': seed, 'base_measurement_model': mode,
                              'budget': k * spec['budget_per_group'],
                              'truth': str((folder / 'truth.npz').relative_to(output)),
                              'truth_sha256': digest(folder / 'truth.npz'), 'frames': frames})
    sources = scientific_sources()
    for relative in sources:
        path = output / 'source_snapshot' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, path)
    manifest = {'evidence': 'development_new_method', 'spec': spec, 'base_spec': base, 'sequences': sequences,
                'sources': sources, 'environment': environment_record(), 'protocol_sha256': digest(output / 'protocol.yaml'),
                'base_protocol_sha256': digest(ROOT / spec['base_protocol']),
                'scheduled_planning_rows': len(sequences) * len(spec['additional_perturbation_m']) * len(METHODS),
                'scheduled_nominal_deployments': sum(s['base_measurement_model'] == spec['motion_subset']['base_measurement_model'] for s in sequences),
                'scheduled_redeployments': sum(s['base_measurement_model'] == spec['motion_subset']['base_measurement_model'] for s in sequences) * len(spec['motion_subset']['methods']),
                'prepared_before_method_evaluation': True}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'seal.json', {'manifest_sha256': digest(output / 'manifest.json')})
    return manifest


def load_measurement(study, frame, spec):
    with np.load(study / frame['observation'], allow_pickle=False) as data:
        return BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                                  spec['declared_error_bound_m'], 'bounded_static_reobservation')


def layout_movement(targets, old):
    if len(targets) != len(old):
        raise ValueError('Fixed-count experiment changed active robot count')
    if not len(targets):
        return {'matched_target_rms_m': 0., 'target_hausdorff_m': 0.}
    distances = np.linalg.norm(targets[:, None] - old[None], axis=2)
    first, second = linear_sum_assignment(distances ** 2)
    return {'matched_target_rms_m': float(np.sqrt(np.mean(distances[first, second] ** 2))),
            'target_hausdorff_m': float(max(distances.min(axis=0).max(), distances.min(axis=1).max()))}


def motion_task(study, task, guides, targets, target_groups, groups, domain, obs, truth, base, planning):
    execution = _blank_execution(guides, 'NOT_EXECUTED')
    metrics = dict(task)
    try:
        execution = execute(guides, targets, domain, base['room_m'], base['model'])
        metrics.update(evaluate(execution, targets, target_groups, groups, obs, truth, base['room_m'], base['model']))
        if not metrics['motion']['initial_safety_ok'] or metrics['motion']['interval_safety_ok'] is False:
            metrics['status'] = 'SAFETY_VIOLATION'
        elif execution.status != 'ARRIVED':
            metrics['status'] = execution.status
        else:
            metrics['status'] = 'FULL_SUCCESS' if metrics['full_task_success'] else 'PARTIAL_SERVICE'
        metrics['continuous_actual_service'] = continuous_summary(reference_groups(truth, base['room_m'], base['model']),
                                        truth, execution.positions[-1][execution.assignment >= 0], base['model']) if len(execution.velocities) else None
    except ScientificFailure as error:
        metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence)
    except Exception:
        metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc())
    _save_task(study / 'results' / task['task_id'], task, base['model'], execution, targets, target_groups, planning, metrics)
    return execution, metrics


def run(protocol, output):
    output = Path(output).resolve()
    manifest = prepare(protocol, output)
    spec, base = manifest['spec'], manifest['base_spec']
    config, records, stopped = base['model'], [], False
    for sequence_index, sequence in enumerate(manifest['sequences']):
        with threadpool_limits(limits=1):
            with np.load(output / sequence['truth'], allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
                guides = data['guides'].copy()
            nominal_layouts, counts, partition, setup_error = {}, None, None, None
            baseline_execution, baseline_metrics = None, None
            try:
                nominal_obs = load_measurement(output, sequence['frames'][0], spec)
                nominal_groups, nominal_domain = construct_groups(nominal_obs, base['room_m'], config)
                catalogs = build_catalog(nominal_groups, nominal_obs, nominal_domain, sequence['budget'], 'projected_mass', config)
                allocation = allocate(catalogs, nominal_groups, sequence['budget'], 'minimax')
                counts = tuple(int(n) for n in allocation.counts)
                partition = tuple(g.rows for g in nominal_groups)
                initial_pools = [phase_pool(g, nominal_obs, nominal_domain, n, config) for g, n in zip(nominal_groups, counts)]
                for method in GREEDY:
                    _, _, options, _ = choose_groups(initial_pools, method)
                    nominal_layouts[method] = tuple(option.positions for option in options)
                references = reference_groups(truth, base['room_m'], config)
            except ScientificFailure as error:
                setup_error = (error.status, str(error), error.evidence)
            except Exception:
                setup_error = ('EXPERIMENT_ERROR', traceback.format_exc(), {})
            for frame in sequence['frames']:
                obs = load_measurement(output, frame, spec)
                frame_error = setup_error
                frame_plans = {}
                try:
                    if frame_error:
                        if frame_error[0] == 'EXPERIMENT_ERROR':
                            raise RuntimeError(frame_error[1])
                        raise ScientificFailure(*frame_error[:2], **frame_error[2])
                    groups, domain = construct_groups(obs, base['room_m'], config)
                    if tuple(g.rows for g in groups) != partition:
                        raise ScientificFailure('STATIC_PARTITION_CHANGED', 'Fixed-count phase comparison requires the same observed partition')
                    pools = [phase_pool(g, obs, domain, n, config,
                                None if frame['perturbation_m'] == 0 else {m: nominal_layouts[m][i] for m in GREEDY})
                             for i, (g, n) in enumerate(zip(groups, counts))]
                except ScientificFailure as error:
                    frame_error = (error.status, str(error), error.evidence)
                except Exception:
                    frame_error = ('EXPERIMENT_ERROR', traceback.format_exc(), {})
                for method in METHODS:
                    task = {**{k: v for k, v in sequence.items() if k != 'frames'}, **frame,
                            'task_id': sequence['sequence_id'] + f"_p{frame['perturbation_m']}_{method}",
                            'unit_id': sequence['sequence_id'], 'error_bound_m': spec['declared_error_bound_m'],
                            'resource': '5', 'layout': method, 'allocator': 'fixed_nominal_counts', 'kind': 'planning'}
                    planning, targets, target_groups = {}, np.empty((0, 2)), np.empty(0, int)
                    metrics = {**task, 'status': 'NOT_EXECUTED', 'motion_evidence': None}
                    try:
                        if frame_error:
                            if frame_error[0] == 'EXPERIMENT_ERROR':
                                raise RuntimeError(frame_error[1])
                            raise ScientificFailure(*frame_error[:2], **frame_error[2])
                        targets, target_groups, selected, evidence = choose_groups(pools, method)
                        if not admissible(targets, domain, config['guide_distance_m']):
                            raise ScientificFailure('TARGET_SEPARATION_INVALID', 'Selected global targets fail unchanged safety admission')
                        original_method = method if method in GREEDY else 'continuous_euclidean_greedy'
                        original = np.vstack(nominal_layouts[original_method])
                        service = continuous_summary(references, truth, targets, config)
                        weights = np.asarray([g.demand_total for g in groups])
                        weights /= weights.sum()
                        planning = {'counts': counts, 'sources': [c.source for c in selected],
                                    'targets': targets, 'target_groups': target_groups,
                                    'shared_pool_sources': [[c.source for c in pool] for pool in pools],
                                    'selected_assigned_observed_u': [c.continuous_euclidean_u for c in selected],
                                    'selected_sampled_arc_u': [c.sampled_arc_u for c in selected],
                                    'selected_continuous_arc_u': [c.continuous_arc_u for c in selected],
                                    'pool_best_continuous_arc_u': [min(c.continuous_arc_u for c in pool) for pool in pools],
                                    'pool_best_assigned_observed_u': [min(c.continuous_euclidean_u for c in pool) for pool in pools],
                                    'selection_evidence': evidence,
                                    'observed_worst_u': max(c.continuous_euclidean_u for c in selected),
                                    'observed_weighted_u': float(np.dot(weights, [c.continuous_euclidean_u for c in selected]))}
                        metrics.update(continuous_common_service=service, **layout_movement(targets, original),
                            retained_groups=sum(e['retained'] for e in evidence),
                            max_assigned_observed_slack=max(e['observed_euclidean_slack'] for e in evidence),
                            observed_worst_u=planning['observed_worst_u'], observed_weighted_u=planning['observed_weighted_u'],
                            observed_continuous_arc_worst_u=max(c.continuous_arc_u for c in selected),
                            group_partition_matches_truth=partition_matches(groups, truth.group_ids),
                            max_actual_observation_error_m=float(np.linalg.norm(obs.observation.positions - truth.positions, axis=1).max()))
                        if method.startswith('euclidean_retain_'):
                            tolerance = float(method.rsplit('_', 1)[1])
                            if metrics['max_assigned_observed_slack'] > tolerance + 1e-12:
                                raise ValueError('Retention score bound failed')
                        frame_plans[method] = (targets.copy(), target_groups.copy(), planning)
                    except ScientificFailure as error:
                        metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence)
                    except Exception:
                        metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc())
                    _save_task(output / 'results' / task['task_id'], task, config, _blank_execution(guides, 'NOT_EXECUTED'),
                               targets, target_groups, planning, metrics)
                    records.append(metrics)
                    if metrics['status'] == 'EXPERIMENT_ERROR':
                        stopped = True
                        break
                if stopped:
                    break
                motion_sequence = sequence['base_measurement_model'] == spec['motion_subset']['base_measurement_model']
                if motion_sequence and frame['perturbation_m'] == 0:
                    method = 'continuous_euclidean_greedy'
                    task = {**{k: v for k, v in sequence.items() if k != 'frames'}, **frame,
                            'task_id': sequence['sequence_id'] + '_nominal_deployment', 'unit_id': sequence['sequence_id'],
                            'error_bound_m': spec['declared_error_bound_m'], 'resource': '5',
                            'layout': method, 'allocator': 'fixed_nominal_counts', 'kind': 'nominal_deployment'}
                    if method in frame_plans:
                        targets, target_groups, planning = frame_plans[method]
                        baseline_execution, baseline_metrics = motion_task(output, task, guides, targets, target_groups,
                                                groups, domain, obs, truth, base, planning)
                        records.append(baseline_metrics)
                        stopped = baseline_metrics['status'] in spec['runtime']['stop_on']
                    else:
                        baseline_metrics = {**task, 'status': 'NOMINAL_LAYOUT_UNAVAILABLE', 'deployment_completed': False,
                                            'full_task_success': False, 'failure_scope': 'nominal planning failure, no motion'}
                        _save_task(output / 'results' / task['task_id'], task, config, _blank_execution(guides, 'NOT_EXECUTED'),
                                   np.empty((0, 2)), np.empty(0, int), {}, baseline_metrics)
                        records.append(baseline_metrics)
                if motion_sequence and frame['perturbation_m'] == spec['motion_subset']['perturbation_m']:
                    for method in spec['motion_subset']['methods']:
                        task = {**{k: v for k, v in sequence.items() if k != 'frames'}, **frame,
                                'task_id': sequence['sequence_id'] + '_redeploy_' + method,
                                'unit_id': sequence['sequence_id'], 'error_bound_m': spec['declared_error_bound_m'],
                                'resource': '5', 'layout': method, 'allocator': 'fixed_nominal_counts', 'kind': 'redeployment',
                                'starting_state_from_task_id': sequence['sequence_id'] + '_nominal_deployment'}
                        if method in frame_plans and baseline_execution is not None and baseline_execution.status == 'ARRIVED':
                            targets, target_groups, planning = frame_plans[method]
                            _, metrics = motion_task(output, task, baseline_execution.positions[-1], targets, target_groups,
                                                     groups, domain, obs, truth, base, planning)
                        else:
                            targets, target_groups = np.empty((0, 2)), np.empty(0, int)
                            metrics = {**task, 'status': 'BASE_OR_LAYOUT_UNAVAILABLE', 'deployment_completed': False,
                                       'full_task_success': False, 'failure_scope': 'same shared nominal failure retained, no redraw'}
                            initial = baseline_execution.positions[-1] if baseline_execution is not None else guides
                            _save_task(output / 'results' / task['task_id'], task, config, _blank_execution(initial, 'NOT_EXECUTED'),
                                       targets, target_groups, {}, metrics)
                        records.append(metrics)
                        if metrics['status'] in spec['runtime']['stop_on']:
                            stopped = True
                            break
                if stopped:
                    break
        print(json.dumps({'sequences': sequence_index + 1, 'scheduled_sequences': len(manifest['sequences']),
                          'records': len(records), 'stopped': stopped}), flush=True)
        if stopped:
            break
    summary = {'evidence': manifest['evidence'], 'scheduled_planning_rows': manifest['scheduled_planning_rows'],
        'scheduled_nominal_deployments': manifest['scheduled_nominal_deployments'],
        'scheduled_redeployments': manifest['scheduled_redeployments'], 'recorded_rows': len(records),
        'statuses': dict(Counter(r['status'] for r in records)),
        'recorded_by_kind': dict(Counter(r['kind'] for r in records)),
        'stopped_on_program_or_safety': stopped,
        'independent_physical_blocks': len(spec['group_counts']) * len(spec['shapes']) * spec['seeds']['count']}
    write_json(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', default='configs/step1/static_phase_retention_v1.yaml')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.protocol, args.output), indent=2))
