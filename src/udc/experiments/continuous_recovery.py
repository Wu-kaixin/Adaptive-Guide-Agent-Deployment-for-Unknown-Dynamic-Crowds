"""Freeze failed *continuous-state* actions, test and replay new coordinators.

The old experiment is read-only. Exported observations and robot initial states
are the exact saved failure inputs, not a nominal-state restart or new sample.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import traceback

import numpy as np
import psutil
import yaml
from threadpoolctl import threadpool_limits

from .static_multigroup import (ROOT, digest, write_json, scientific_sources,
    environment_record, verify, _load_inputs, _blank_execution, _save_task)
from .recovery_study import run as dispatch, STOP
from .static_mechanism import continuous_summary
from udc.multigroup.geometry import construct_groups
from udc.multigroup.evaluation import evaluate, reference_groups
from udc.multigroup.execution import Execution
from udc.multigroup.joint_reconfiguration import execute
from udc.multigroup.joint_reconfiguration import execute_geodesic, execute_reassigned, execute_hybrid
from udc.multigroup.safe_routing import execute as serial_execute


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checked(path, expected):
    if digest(path) != expected:
        raise ValueError('Frozen artifact changed: ' + str(path))


def prepare(protocol, study):
    design = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    old = ROOT / design['original_study']
    checked(old / 'manifest.json', read(old / 'seal.json')['manifest_sha256'])
    original = read(old / 'manifest.json')
    selected = None
    if design.get('previous_study'):
        previous = ROOT / design['previous_study']
        checked(previous / 'manifest.json', read(previous / 'seal.json')['manifest_sha256'])
        selected = set()
        for path in sorted((previous / 'results').glob('*/metrics.json')):
            for filename, expected in read(path.parent / 'receipt.json').items():
                checked(path.parent / filename, expected)
            metric = read(path)
            if not metric.get('deployment_completed'):
                selected.add(metric['original_unit_id'] + '|' + metric['method'])
    units = {u['unit_id']: u for u in original['units']}
    failures = []
    # The complete denominator is scanned before evaluating the new method.
    for path in sorted((old / 'results').glob('*/completion.json')):
        completion = read(path)
        if completion['summary']['statuses'].get('SERIAL_ASSEMBLY_UNRESOLVED'):
            task = read(path.parent / 'config.json')['task']
            if selected is not None and task['unit_id'] + '|' + task['method'] not in selected:
                continue
            failures.append((path, completion))
    if len(failures) != design['expected_actions']:
        raise ValueError('Failure denominator differs from frozen protocol')
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study / 'protocol.yaml')
    exported, tasks, census = [], [], []
    for path, completion in failures:
        episode = path.parent
        checked(episode / 'config.json', completion['config_sha256'])
        config = read(episode / 'config.json')
        task = config['task']
        old_unit = units[task['unit_id']]
        series = old_unit['series'][task['pattern']]
        checked(old / series['path'], series['sha256'])
        checked(old / old_unit['truth'], old_unit['truth_sha256'])
        found = 0
        for relative, checksum in sorted(completion['chunk_receipt_sha256'].items()):
            receipt_path = episode / relative
            checked(receipt_path, checksum)
            chunk = receipt_path.parent
            for filename, expected in read(receipt_path).items():
                checked(chunk / filename, expected)
            metrics, states = read(chunk / 'metrics.json'), read(chunk / 'state.json')
            for index, metric in enumerate(metrics):
                if metric['status'] != 'SERIAL_ASSEMBLY_UNRESOLVED':
                    continue
                found += 1
                name = task['task_id'] + f"_step{metric['step']:02d}"
                folder = study / 'inputs' / name
                folder.mkdir(parents=True)
                with np.load(chunk / 'trajectory.npz', allow_pickle=False) as data:
                    a, b = data['position_offsets'][index:index + 2]
                    if b - a != 1:
                        raise ValueError('Expected no motion before failed reverse preflight')
                    guides = data['positions'][a].copy()
                    n = int(data['target_counts'][index])
                    targets = data['targets'][index, :n].copy()
                    labels = data['target_groups'][index, :n].copy()
                with np.load(old / old_unit['truth'], allow_pickle=False) as data:
                    arrays = {k: data[k].copy() for k in data.files}
                arrays['guides'] = guides
                np.savez_compressed(folder / 'truth.npz', **arrays)
                with np.load(old / series['path'], allow_pickle=False) as data:
                    positions = data['positions'][metric['step']].copy()
                np.savez_compressed(folder / 'observation.npz', positions=positions,
                                    radii=arrays['radii'], demand=arrays['demand'])
                np.savez_compressed(folder / 'action.npz', targets=targets, target_groups=labels)
                unit = {**task, 'unit_id': name, 'original_unit_id': task['unit_id'],
                        'original_step': metric['step'], 'error_bound_m': original['design']['declared_error_m'],
                        'model': config['model'], 'original_completion_sha256': digest(path),
                        'original_chunk_receipt': receipt_path.relative_to(ROOT).as_posix(),
                        'original_chunk_receipt_sha256': checksum}
                for field in ('truth', 'observation', 'action'):
                    exported_path = folder / (field + '.npz')
                    unit[field] = exported_path.relative_to(study).as_posix()
                    unit[field + '_sha256'] = digest(exported_path)
                exported.append(unit)
                for policy in design['policies']:
                    tasks.append(unit | {'policy': policy, 'task_id': name + '__' + policy})
                queries = states[index]['evidence']['queries']
                census.append({'action_id': name, 'original_unit_id': task['unit_id'],
                    'original_method': task['method'], 'resource': task['resource'], 'pattern': task['pattern'],
                    'step': metric['step'], 'pending_guides': states[index]['evidence']['pending_guides'],
                    'attempt_results': dict(Counter(a.get('result', 'accepted_search') for q in queries for a in q['attempts']))})
        if found != 1:
            raise ValueError('Expected exactly one terminal failure per episode')
    sources = scientific_sources()
    for relative in sources:
        destination = study / 'source_snapshot' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    manifest = {'protocol': design['protocol'], 'evidence': design['evidence'],
        'spec': original['spec'], 'design': design, 'units': exported, 'tasks': tasks,
        'sources': sources, 'environment': environment_record(), 'protocol_sha256': digest(study / 'protocol.yaml'),
        'new_independent_samples': 0, 'original_manifest_sha256': digest(old / 'manifest.json'),
        'prepared_before_method_evaluation': True}
    write_json(study / 'census.json', {'actions': census, 'physical_blocks': len({r['original_unit_id'] for r in census}),
        'by_method': dict(Counter(r['original_method'] for r in census)), 'new_independent_samples': 0})
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    return {'frozen_actions': len(exported), 'new_independent_samples': 0}


def worker(argument):
    study, unit, manifest = argument
    study = Path(study)
    config, base = unit['model'], manifest['spec']
    truth, guides, observation = _load_inputs(study, unit)
    checked(study / unit['action'], unit['action_sha256'])
    with np.load(study / unit['action'], allow_pickle=False) as data:
        targets, labels = data['targets'].copy(), data['target_groups'].copy()
    rows = []
    with threadpool_limits(limits=1):
        for task in (t for t in manifest['tasks'] if t['unit_id'] == unit['unit_id']):
            result, metrics = _blank_execution(guides, 'NOT_EXECUTED'), dict(task)
            try:
                groups, domain = construct_groups(observation, base['room_m'], config)
                reference = reference_groups(truth, base['room_m'], config)
                if task['policy'] == 'verified_synchronous_linear_v1':
                    result = execute(guides, targets, domain, base['room_m'], config, observation)
                elif task['policy'] == 'verified_synchronous_geodesic_v2':
                    result = execute_geodesic(guides, targets, domain, base['room_m'], config, observation)
                elif task['policy'] == 'verified_synchronous_reassigned_v3':
                    result = execute_reassigned(guides, targets, domain, base['room_m'], config, observation)
                elif task['policy'] == 'joint_reassigned_then_reverse_v1':
                    result = execute_hybrid(guides, targets, domain, base['room_m'], config, observation)
                elif task['policy'] == 'adaptive_forward':
                    result = serial_execute(guides, targets, domain, base['room_m'], config, observation, 'adaptive_forward')
                else:
                    raise ValueError('Unknown continuous recovery policy')
                metrics.update(evaluate(result, targets, labels, groups, observation, truth, base['room_m'], config))
                metrics['continuous_actual_service'] = continuous_summary(reference, truth,
                    result.positions[-1][result.assignment >= 0], config) if len(result.velocities) else None
                if not metrics['motion']['initial_safety_ok'] or metrics['motion']['interval_safety_ok'] is False:
                    metrics['status'] = 'SAFETY_VIOLATION'
                elif result.status != 'ARRIVED':
                    metrics['status'] = result.status
                else:
                    metrics['status'] = 'FULL_SUCCESS' if metrics['full_task_success'] else 'PARTIAL_SERVICE'
            except Exception:
                metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc())
            _save_task(study / 'results' / task['task_id'], task, config, result, targets, labels,
                       {'frozen_failed_continuous_action': True}, metrics)
            rows.append(metrics)
            if metrics['status'] in STOP:
                break
    return rows


def run(study, workers):
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline(), 'purpose': 'fixed_continuous_failure_development'})
    return dispatch(study, workers, worker)


def replay(study):
    manifest = verify(study)
    mismatches, checked_count = [], 0
    for task in manifest['tasks']:
        folder = study / 'results' / task['task_id']
        if not (folder / 'receipt.json').exists():
            continue
        for name, checksum in read(folder / 'receipt.json').items():
            checked(folder / name, checksum)
        truth, guides, observation = _load_inputs(study, task)
        config = task['model']
        groups, _ = construct_groups(observation, manifest['spec']['room_m'], config)
        state, saved = read(folder / 'state.json'), read(folder / 'metrics.json')
        checked(study / task['action'], task['action_sha256'])
        with np.load(study / task['action'], allow_pickle=False) as action, np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
            if not (np.array_equal(guides, data['positions'][0]) and np.array_equal(action['targets'], data['targets'])
                    and np.array_equal(action['target_groups'], data['target_groups'])):
                mismatches.append(task['task_id'] + ':FIXED_INPUT')
            result = Execution(data['positions'], data['velocities'], data['assignment'], state['execution_status'], (), state['evidence'])
            fresh = evaluate(result, data['targets'], data['target_groups'], groups, observation, truth, manifest['spec']['room_m'], config)
            reference = reference_groups(truth, manifest['spec']['room_m'], config)
            service = continuous_summary(reference, truth, result.positions[-1][result.assignment >= 0], config) if len(result.velocities) else None
        if any(fresh[k] != saved[k] for k in fresh) or service != saved['continuous_actual_service']:
            mismatches.append(task['task_id'])
        checked_count += 1
    result = {'checked': checked_count, 'scheduled': len(manifest['tasks']), 'mismatches': mismatches,
              'all_match': not mismatches and checked_count == len(manifest['tasks']), 'new_independent_samples': 0}
    write_json(study / 'offline_replay.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run', 'replay'])
    parser.add_argument('--study', required=True)
    parser.add_argument('--protocol')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    study = Path(args.study).resolve()
    result = (prepare(args.protocol, study) if args.action == 'prepare' else
              run(study, args.workers) if args.action == 'run' else replay(study))
    print(json.dumps(result, ensure_ascii=False))
