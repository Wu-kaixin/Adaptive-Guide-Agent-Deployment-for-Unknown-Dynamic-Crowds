"""Explicit common-translation model-bound pilot with old-reference bridge metrics."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
import itertools
import json
from pathlib import Path
import shutil
import traceback

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import StaticTruth, BoundedMeasurement, measure
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.scenarios import make_truth, initial_guides
from udc.multigroup.allocation import allocate, build_catalog
from udc.multigroup.phase_retention import GREEDY, phase_pool, choose_groups
from udc.multigroup.evaluation import reference_groups, evaluate
from udc.multigroup.translation_service_bound import anchored_reference, reference_service, retention_certificate
from udc.multigroup import execution as execution_module
from udc.multigroup.accelerated_execution import install_lazy
from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
    environment_record, verify, _blank_execution, _save_task)
from udc.experiments.static_phase_retention import layout_movement
from udc.experiments.static_mechanism import continuous_summary
from udc.experiments.frozen_resume import atomic_json
from udc.experiments.worker_resources import initialize_worker

STOP = {'EXPERIMENT_ERROR', 'SAFETY_VIOLATION', 'MODEL_BOUND_VIOLATION'}


def motion_task(study, task, guides, targets, target_groups, groups, domain, obs, truth, base, planning):
    result = _blank_execution(guides, 'NOT_EXECUTED')
    metrics = dict(task)
    try:
        result = execution_module.execute(guides, targets, domain, base['room_m'], base['model'])
        metrics.update(evaluate(result, targets, target_groups, groups, obs, truth, base['room_m'], base['model']))
        if not metrics['motion']['initial_safety_ok'] or metrics['motion']['interval_safety_ok'] is False:
            metrics['status'] = 'SAFETY_VIOLATION'
        elif result.status != 'ARRIVED':
            metrics['status'] = result.status
        else:
            metrics['status'] = 'FULL_SUCCESS' if metrics['full_task_success'] else 'PARTIAL_SERVICE'
        metrics['continuous_actual_service'] = continuous_summary(reference_groups(truth, base['room_m'], base['model']),
            truth, result.positions[-1][result.assignment >= 0], base['model']) if len(result.velocities) else None
        if metrics.get('deployment_completed'):
            active = result.assignment >= 0
            tracking = float(np.linalg.norm(result.positions[-1][active] - targets[result.assignment[active]], axis=1).max()) if np.any(active) else 0.
            metrics['endpoint_tracking_error_m'] = tracking
            if tracking > planning.get('tracking_bound_m', 1e-10):
                metrics.update(status='EXPERIMENT_ERROR', failure_message='Delivered endpoint exceeds declared tracking contract')
    except ScientificFailure as error:
        metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence)
    except Exception:
        metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc())
    _save_task(study / 'results' / task['task_id'], task, base['model'], result, targets, target_groups, planning, metrics)
    return result, metrics


def prepare(protocol, study):
    pilot = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    base = yaml.safe_load((ROOT / pilot['base_protocol']).read_text(encoding='utf-8'))
    if pilot.get('independent_segment_gate'):
        base['model']['independent_segment_gate'] = True
    expected_count = 20 if pilot['evidence'] == 'prospective_confirmation_conditional_reference' else 3
    if pilot['seeds']['count'] != expected_count:
        raise ValueError('Frozen development/confirmation seed design required')
    if set(range(pilot['seeds']['start'], pilot['seeds']['start'] + expected_count)) & set(range(5000, 5030)):
        raise ValueError('Validation seeds 5000-5029 are reserved')
    if pilot['base_actual_error_m'] + max(pilot['perturbations_m']) > pilot['declared_error_m']:
        raise ValueError('Generated error exceeds the declared model bound')
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study / 'protocol.yaml')
    sequences, units, tasks = [], [], []
    for k, shape, seed in itertools.product(pilot['group_counts'], pilot['shapes'],
            range(pilot['seeds']['start'], pilot['seeds']['start'] + pilot['seeds']['count'])):
        truth = make_truth(k, shape, seed, pilot['namespace'], base['group_size_multipliers'], base['group_hotspot_multipliers'])
        physical = f'K{k}_{shape}_s{seed}'
        folder = study / 'inputs' / physical
        folder.mkdir(parents=True)
        path = folder / 'truth.npz'
        np.savez_compressed(path, positions=truth.positions, radii=truth.radii, demand=truth.demand,
                            group_ids=truth.group_ids, guides=initial_guides(k * pilot['budget_per_group'], base['room_m']))
        for model in pilot['models']:
            nominal = measure(truth, pilot['base_actual_error_m'], np.random.SeedSequence([pilot['namespace'], seed, k]),
                              'common_bias' if model == 'pure_common_translation' else 'bounded_disk')
            rng = np.random.default_rng(np.random.SeedSequence([pilot['namespace'], seed, k, 913]))
            angles = rng.uniform(0, 2 * np.pi, 1 if model == 'pure_common_translation' else len(truth.positions))
            direction = np.c_[np.cos(angles), np.sin(angles)]
            frames = []
            sequence_id = physical + '_' + model
            for eta in pilot['perturbations_m']:
                observation_path = folder / f'{model}_{eta}.npz'
                np.savez_compressed(observation_path, positions=nominal.observation.positions + eta * direction,
                                    radii=truth.radii, demand=truth.demand)
                frame = {'perturbation_m': eta, 'observation': observation_path.relative_to(study).as_posix(),
                         'observation_sha256': digest(observation_path)}
                frames.append(frame)
                units.append(frame | {'unit_id': sequence_id + f'_p{eta}', 'truth': path.relative_to(study).as_posix(),
                                       'truth_sha256': digest(path)})
            sequence = {'sequence_id': sequence_id, 'physical_id': physical, 'group_count': k, 'shape': shape,
                        'seed': seed, 'model': model, 'truth': path.relative_to(study).as_posix(),
                        'truth_sha256': digest(path), 'frames': frames, 'budget': k * pilot['budget_per_group']}
            sequences.append(sequence)
            metadata = {key: sequence[key] for key in ('sequence_id', 'group_count', 'shape', 'seed', 'model', 'budget')}
            tasks.append(metadata | {'task_id': sequence_id + '_nominal', 'kind': 'nominal'})
            for frame, method in itertools.product(frames, pilot['methods']):
                detail = metadata | {'method': method, 'perturbation_m': frame['perturbation_m']}
                tasks.append(detail | {'task_id': sequence_id + f'_p{frame["perturbation_m"]}_{method}_plan',
                              'kind': 'planning'})
                if frame['perturbation_m'] in pilot.get('motion_perturbations_m', [pilot['motion_perturbation_m']]):
                    tasks.append(detail | {'task_id': sequence_id + f'_p{frame["perturbation_m"]}_{method}_motion',
                                  'kind': 'motion'})
    sources = scientific_sources()
    for relative in sources:
        target = study / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    manifest = {'protocol': pilot['protocol'], 'evidence': pilot['evidence'], 'spec': base, 'pilot_spec': pilot,
                'sequences': sequences, 'units': units, 'tasks': tasks, 'sources': sources,
                'environment': environment_record(), 'protocol_sha256': digest(study / 'protocol.yaml'),
                'prepared_before_method_evaluation': True}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    return {'physical_blocks': len({s['physical_id'] for s in sequences}), 'sequences': len(sequences), 'tasks': len(tasks),
            'manifest_sha256': digest(study / 'manifest.json')}


def observation(study, frame, pilot):
    with np.load(study / frame['observation'], allow_pickle=False) as data:
        return BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                                  pilot['declared_error_m'], 'declared_bounded_reobservation')


def _sequence_impl(argument):
    study, sequence, base, pilot = argument
    study, config = Path(study), base['model']
    if not getattr(execution_module._graph, '_udc_prepared_backend', False):
        install_lazy(execution_module)
    rows = []
    with threadpool_limits(limits=1):
        with np.load(study / sequence['truth'], allow_pickle=False) as data:
            truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            guides = data['guides'].copy()
        nominal = observation(study, sequence['frames'][0], pilot)
        nominal_groups, nominal_domain = construct_groups(nominal, base['room_m'], config)
        catalogs = build_catalog(nominal_groups, nominal, nominal_domain, sequence['budget'], 'projected_mass', config)
        allocation = allocate(catalogs, nominal_groups, sequence['budget'], 'minimax')
        counts = tuple(int(n) for n in allocation.counts)
        initial_pools = [phase_pool(g, nominal, nominal_domain, n, config) for g, n in zip(nominal_groups, counts)]
        nominal_layouts = {method: tuple(option.positions for option in choose_groups(initial_pools, method)[2]) for method in GREEDY}
        nominal_targets, nominal_ids, _, _ = choose_groups(initial_pools, 'continuous_euclidean_greedy')
        base_task = {key: sequence[key] for key in ('group_count', 'shape', 'seed', 'budget', 'model', 'sequence_id')}
        nominal_task = base_task | {'task_id': sequence['sequence_id'] + '_nominal', 'kind': 'nominal'}
        nominal_execution, nominal_metrics = motion_task(study, nominal_task, guides, nominal_targets, nominal_ids,
            nominal_groups, nominal_domain, nominal, truth, base, {'counts': counts, 'targets': nominal_targets})
        rows.append(nominal_metrics)
        if nominal_metrics['status'] in STOP:
            return rows
        true_obs = Observation(truth.positions, truth.radii, truth.demand)
        true_anchor = anchored_reference(true_obs, base['room_m'], config,
                                        pilot['reference_quantum_m'], pilot['coordinate_rounding_bound_m'])
        original_reference = reference_groups(truth, base['room_m'], config)
        for frame in sequence['frames']:
            obs = observation(study, frame, pilot)
            groups, domain = construct_groups(obs, base['room_m'], config)
            if tuple(g.rows for g in groups) != tuple(g.rows for g in nominal_groups):
                raise ScientificFailure('PARTITION_CHANGED', 'Fixed-count group identity changed')
            pools = [phase_pool(g, obs, domain, count, config, {key: nominal_layouts[key][index] for key in GREEDY})
                     for index, (g, count) in enumerate(zip(groups, counts))]
            greedy, target_ids, _, _ = choose_groups(pools, 'continuous_euclidean_greedy')
            retained, _, _, _ = choose_groups(pools, 'euclidean_retain_0.002')
            reference = anchored_reference(obs.observation, base['room_m'], config,
                                           pilot['reference_quantum_m'], pilot['coordinate_rounding_bound_m'])
            certificate = retention_certificate(reference, retained, greedy, sequence['model'],
                pilot['declared_error_m'], config['max_gap_m'] / 2, pilot['true_model_loss_delta'],
                pilot.get('tracking_bound_m', 0.))
            anchored_greedy = reference_service(true_anchor, greedy, config['max_gap_m'] / 2)
            anchored_retained = reference_service(true_anchor, retained, config['max_gap_m'] / 2)
            losses = np.asarray(anchored_retained['groups']) - anchored_greedy['groups']
            correspondence = (len(reference.groups) == len(true_anchor.groups) and all(
                a.rows == b.rows and a.boundary.deployment.tobytes() == b.boundary.deployment.tobytes()
                and ar.tobytes() == br.tobytes() for a, b, ar, br in
                zip(reference.groups, true_anchor.groups, reference.densities, true_anchor.densities)))
            audited = (certificate['status'] != 'MODEL_BOUND_NUMERICAL_AUDIT' or
                       (correspondence and all(loss <= bound['upper_loss'] for loss, bound in zip(losses, certificate['groups']))))
            choices = {'greedy': greedy, 'observed_retain': retained,
                       'translation_guard': retained if certificate['accept_retained'] else greedy}
            for method in pilot['methods']:
                points = choices[method]
                task = base_task | {'task_id': sequence['sequence_id'] + f'_p{frame["perturbation_m"]}_{method}_plan',
                                   'kind': 'planning', 'method': method, 'perturbation_m': frame['perturbation_m']}
                planning = {'counts': counts, 'targets': points, 'target_groups': target_ids,
                            'certificate': certificate, 'reference_contract': pilot['reference'],
                            'tracking_bound_m': pilot.get('tracking_bound_m', 0.)}
                metrics = task | {'status': 'PLANNED' if audited else 'MODEL_BOUND_VIOLATION',
                    'anchored_true_service': reference_service(true_anchor, points, config['max_gap_m'] / 2),
                    'original_true_service': continuous_summary(original_reference, truth, points, config),
                    'certificate': certificate, 'candidate_true_losses': losses.tolist(),
                    'reference_correspondence_bitwise': correspondence, 'bound_audit_passed': audited,
                    'nontrivial_retention': bool(certificate['accept_retained'] and not np.array_equal(retained, greedy)),
                    **layout_movement(points, nominal_targets),
                    'maximum_true_position_error_m': float(np.linalg.norm(obs.observation.positions - truth.positions, axis=1).max())}
                _save_task(study / 'results' / task['task_id'], task, config, _blank_execution(guides, 'PLANNING_ONLY'),
                           points, target_ids, planning, metrics)
                rows.append(metrics)
                if not audited:
                    return rows
                if frame['perturbation_m'] in pilot.get('motion_perturbations_m', [pilot['motion_perturbation_m']]):
                    move_task = task | {'task_id': task['task_id'].replace('_plan', '_motion'), 'kind': 'motion'}
                    if nominal_metrics.get('deployment_completed'):
                        _, actual = motion_task(study, move_task, nominal_execution.positions[-1], points, target_ids,
                            groups, domain, obs, truth, base, planning)
                    else:
                        actual = move_task | {'status': 'NOT_EXECUTED_NOMINAL_FAILURE'}
                        _save_task(study / 'results' / move_task['task_id'], move_task, config,
                            _blank_execution(guides, 'NOT_EXECUTED'), points, target_ids, planning, actual)
                    rows.append(actual)
                    if actual['status'] in STOP:
                        return rows
    return rows


def sequence_worker(argument):
    study, sequence, base, pilot = argument
    study = Path(study)
    try:
        return _sequence_impl(argument)
    except Exception as error:
        scientific = isinstance(error, ScientificFailure)
        manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
        expected = [task for task in manifest['tasks'] if task['sequence_id'] == sequence['sequence_id']]
        with np.load(study / sequence['truth'], allow_pickle=False) as data:
            guides = data['guides'].copy()
        rows, first = [], True
        for task in expected:
            folder = study / 'results' / task['task_id']
            if (folder / 'receipt.json').is_file():
                rows.append(json.loads((folder / 'metrics.json').read_text(encoding='utf-8')))
                continue
            status = error.status if scientific and first else 'NOT_EXECUTED_SEQUENCE_FAILURE' if scientific else 'EXPERIMENT_ERROR'
            metrics = task | {'status': status, 'failure_message': str(error),
                              'failure_evidence': error.evidence if scientific else {}}
            if not scientific:
                metrics['traceback'] = traceback.format_exc()
            _save_task(folder, task, base['model'], _blank_execution(guides, 'NOT_EXECUTED'),
                       np.empty((0, 2)), np.empty(0, int), {}, metrics)
            rows.append(metrics)
            first = False
            if not scientific:
                break
        return rows


def run(study, workers):
    manifest = verify(study)
    pilot, base = manifest['pilot_spec'], manifest['spec']
    if not 1 <= workers <= pilot['runtime']['maximum_workers']:
        raise ValueError('Pilot resource budget exceeded')
    iterator, pending, rows, stopped = iter(manifest['sequences']), {}, [], False
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize_worker) as pool:
        def submit():
            sequence = next(iterator, None)
            if sequence:
                pending[pool.submit(sequence_worker, (str(study), sequence, base, pilot))] = sequence['sequence_id']
        for _ in range(workers):
            submit()
        while pending:
            done, _ = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                new = future.result()
                rows.extend(new)
                stopped |= any(row['status'] in STOP for row in new)
            if not stopped:
                for _ in done:
                    submit()
            count = sum((study / 'results' / task['task_id'] / 'receipt.json').is_file() for task in manifest['tasks'])
            atomic_json(study / 'status.json', {'updated_at': datetime.now().astimezone().isoformat(),
                'recorded_rows': count, 'scheduled_rows': len(manifest['tasks']), 'in_flight_sequences': len(pending), 'stopped': stopped})
    summary = {'recorded_rows': len(rows), 'scheduled_rows': len(manifest['tasks']), 'stopped': stopped,
               'not_recorded_rows': len(manifest['tasks']) - len(rows),
               'statuses': dict(Counter(row['status'] for row in rows)),
               'independent_physical_blocks': len({s['physical_id'] for s in manifest['sequences']}),
               'reference_scope': pilot['reference'], 'original_world_reference_also_saved': True}
    write_json(study / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--protocol', default='configs/step1/translation_service_pilot_v1.yaml')
    parser.add_argument('--study', required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(prepare(args.protocol, Path(args.study).resolve()) if args.action == 'prepare'
                     else run(Path(args.study).resolve(), args.workers), ensure_ascii=False, indent=2), flush=True)
