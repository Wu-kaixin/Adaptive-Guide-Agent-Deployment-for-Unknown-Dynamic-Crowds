"""Frozen development comparison of original and serial-consistent admission."""
import argparse
from collections import Counter, defaultdict
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

from udc.multigroup import execution as execution_module
from udc.multigroup.accelerated_execution import install_lazy
from udc.multigroup.allocation import allocate, build_catalog, selected_targets
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.observation import measure
from udc.multigroup.representation_gate import serial_target_admissible
from udc.multigroup.scenarios import make_truth, initial_guides
from udc.multigroup.evaluation import evaluate, reference_groups
from udc.experiments.static_mechanism import continuous_summary
from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
    environment_record, verify, _load_inputs, _blank_execution, _save_task)
from udc.experiments.frozen_resume import atomic_json
from udc.experiments.worker_resources import initialize_worker


def prepare(protocol, study):
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    base = yaml.safe_load((ROOT / spec['base_protocol']).read_text(encoding='utf-8'))
    if spec.get('independent_segment_gate'):
        base['model']['independent_segment_gate'] = True
    expected_count = 20 if spec['evidence'] == 'prospective_confirmation' else 3
    if not spec['protocol'].startswith('representation_gate_') or spec['seeds']['count'] != expected_count:
        raise ValueError('Frozen development/confirmation seed design required')
    if set(range(spec['seeds']['start'], spec['seeds']['start'] + expected_count)) & set(range(5000, 5030)):
        raise ValueError('Validation seeds 5000-5029 are reserved')
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study / 'protocol.yaml')
    units, tasks = [], []
    for k, shape, seed in itertools.product(spec['group_counts'], spec['shapes'],
            range(spec['seeds']['start'], spec['seeds']['start'] + spec['seeds']['count'])):
        truth = make_truth(k, shape, seed, spec['namespace'], base['group_size_multipliers'], base['group_hotspot_multipliers'])
        observation = measure(truth, spec['error_bound_m'], np.random.SeedSequence([spec['namespace'], seed, k]), spec['measurement_model'])
        unit_id = f'K{k}_{shape}_s{seed}_e0.12'
        folder = study / 'inputs' / unit_id
        folder.mkdir(parents=True)
        np.savez_compressed(folder / 'truth.npz', positions=truth.positions, radii=truth.radii,
                            demand=truth.demand, group_ids=truth.group_ids,
                            guides=initial_guides(max(base['resources'][r] for r in spec['resources']) * k, base['room_m']))
        np.savez_compressed(folder / 'observation.npz', positions=observation.observation.positions,
                            radii=observation.observation.radii, demand=observation.observation.demand)
        unit = {'unit_id': unit_id, 'group_count': k, 'shape': shape, 'seed': seed,
                'error_bound_m': spec['error_bound_m'],
                'truth': (folder / 'truth.npz').relative_to(study).as_posix(),
                'truth_sha256': digest(folder / 'truth.npz'),
                'observation': (folder / 'observation.npz').relative_to(study).as_posix(),
                'observation_sha256': digest(folder / 'observation.npz')}
        units.append(unit)
        for resource, allocator, admission in itertools.product(spec['resources'], spec['allocators'], spec['admissions']):
            tasks.append(unit | {'resource': resource, 'allocator': allocator, 'admission': admission,
                         'layout': spec['layout'], 'budget': base['resources'][resource] * k,
                         'task_id': f'{unit_id}_{resource}_{allocator}_{admission}'})
    sources = scientific_sources()
    for relative in sources:
        target = study / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    manifest = {'protocol': spec['protocol'], 'evidence': spec['evidence'], 'spec': base,
                'pilot_spec': spec, 'units': units, 'tasks': tasks, 'sources': sources,
                'protocol_sha256': digest(study / 'protocol.yaml'), 'environment': environment_record(),
                'prepared_before_method_evaluation': True}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    return {'units': len(units), 'tasks': len(tasks), 'manifest_sha256': digest(study / 'manifest.json')}


def unit_worker(argument):
    study, tasks, base = argument
    study, config = Path(study), base['model']
    install_lazy(execution_module) if not getattr(execution_module._graph, '_udc_prepared_backend', False) else None
    rows = []
    with threadpool_limits(limits=1):
        truth, all_guides, observation = _load_inputs(study, tasks[0])
        setup_error, catalogs = None, {}
        try:
            groups, domain = construct_groups(observation, base['room_m'], config)
            maximum = max(task['budget'] for task in tasks)
            for admission in ('euclidean_original', 'serial_polygon_consistent'):
                gate = (lambda points: serial_target_admissible(points, config['guide_distance_m'])) if admission == 'serial_polygon_consistent' else None
                catalogs[admission] = build_catalog(groups, observation, domain, maximum, 'projected_mass', config, admission=gate)
            references = reference_groups(truth, base['room_m'], config)
        except ScientificFailure as error:
            setup_error = (error.status, str(error), error.evidence)
        except Exception:
            setup_error = ('EXPERIMENT_ERROR', traceback.format_exc(), {})
        for task in tasks:
            folder = study / 'results' / task['task_id']
            if folder.exists():
                raise FileExistsError('No scientific retry or overwrite')
            guides = all_guides[:task['budget']]
            targets, target_groups = np.empty((0, 2)), np.empty(0, int)
            result = _blank_execution(guides, 'NOT_EXECUTED')
            metrics, planning = dict(task), {}
            try:
                if setup_error:
                    if setup_error[0] == 'EXPERIMENT_ERROR':
                        raise RuntimeError(setup_error[1])
                    raise ScientificFailure(setup_error[0], setup_error[1], **setup_error[2])
                allocation = allocate(catalogs[task['admission']], groups, task['budget'], task['allocator'])
                targets, target_groups = selected_targets(allocation, config)
                planning = {'counts': allocation.counts, 'active': allocation.active,
                            'objective': allocation.objective, 'targets': targets,
                            'target_groups': target_groups, 'navigation_domain_sha256': domain.geometry_sha256,
                            'admission': task['admission']}
                if task['admission'] == 'serial_polygon_consistent' and not serial_target_admissible(targets, config['guide_distance_m']):
                    raise ScientificFailure('GLOBAL_TARGET_REPRESENTATION_INVALID', 'Cross-group target polygon admission failed')
                metrics['continuous_planned_service'] = continuous_summary(references, truth, targets, config)
                result = execution_module.execute(guides, targets, domain, base['room_m'], config)
                metrics.update(evaluate(result, targets, target_groups, groups, observation, truth, base['room_m'], config))
                if not metrics['motion']['initial_safety_ok'] or metrics['motion']['interval_safety_ok'] is False:
                    metrics['status'] = 'SAFETY_VIOLATION'
                elif result.status != 'ARRIVED':
                    metrics['status'] = result.status
                else:
                    metrics['status'] = 'FULL_SUCCESS' if metrics['full_task_success'] else 'PARTIAL_SERVICE'
                metrics['continuous_actual_service'] = (continuous_summary(references, truth,
                    result.positions[-1][result.assignment >= 0], config) if len(result.velocities) else None)
            except ScientificFailure as error:
                metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence,
                               deployment_completed=False, full_task_success=False)
            except Exception:
                metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc(),
                               deployment_completed=False, full_task_success=False)
            _save_task(folder, task, config, result, targets, target_groups, planning, metrics)
            rows.append(metrics)
            if metrics['status'] in ('EXPERIMENT_ERROR', 'SAFETY_VIOLATION'):
                break
    return rows


def run(study, workers):
    manifest = verify(study)
    if not 1 <= workers <= manifest['pilot_spec']['runtime']['maximum_workers']:
        raise ValueError('Pilot worker budget exceeded')
    grouped = defaultdict(list)
    for task in manifest['tasks']:
        grouped[task['unit_id']].append(task)
    iterator = iter(grouped.values())
    rows, stopped, pending = [], False, {}
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize_worker) as pool:
        def submit():
            tasks = next(iterator, None)
            if tasks:
                pending[pool.submit(unit_worker, (str(study), tasks, manifest['spec']))] = tasks[0]['unit_id']
        for _ in range(workers):
            submit()
        while pending:
            done, _ = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                fresh = future.result()
                rows.extend(fresh)
                stopped |= any(row['status'] in ('EXPERIMENT_ERROR', 'SAFETY_VIOLATION') for row in fresh)
            if not stopped:
                for _ in done:
                    submit()
            count = sum((study / 'results' / task['task_id'] / 'receipt.json').is_file() for task in manifest['tasks'])
            atomic_json(study / 'status.json', {'updated_at': datetime.now().astimezone().isoformat(),
                        'recorded_rows': count, 'scheduled_rows': len(manifest['tasks']),
                        'in_flight_units': len(pending), 'stopped': stopped})
    summary = {'recorded_rows': len(rows), 'scheduled_rows': len(manifest['tasks']), 'stopped': stopped,
               'statuses': dict(Counter(row['status'] for row in rows)),
               'by_admission': {name: dict(Counter(row['status'] for row in rows if row['admission'] == name))
                                for name in manifest['pilot_spec']['admissions']},
               'independent_physical_blocks': len(manifest['units']), 'inference': manifest['evidence']}
    write_json(study / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--protocol', default='configs/step1/representation_gate_v1.yaml')
    parser.add_argument('--study', required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    value = prepare(args.protocol, Path(args.study).resolve()) if args.action == 'prepare' else run(Path(args.study).resolve(), args.workers)
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)
