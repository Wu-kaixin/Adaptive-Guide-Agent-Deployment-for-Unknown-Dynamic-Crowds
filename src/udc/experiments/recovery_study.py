"""Frozen fixed-action development and new-input routing confirmation."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
import json
from pathlib import Path
import shutil
import traceback

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
    environment_record, verify, _load_inputs, _blank_execution, _save_task)
from udc.experiments.frozen_resume import atomic_json
from udc.experiments.worker_resources import initialize_worker
from udc.experiments.static_mechanism import continuous_summary
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.evaluation import evaluate, reference_groups
from udc.multigroup.safe_routing import execute

STOP = {'EXPERIMENT_ERROR', 'SAFETY_VIOLATION', 'MODEL_BOUND_VIOLATION'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def prepare(protocol, study):
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    original = ROOT / spec['original_study']
    old_manifest = read(original / 'manifest.json')
    if digest(original / 'manifest.json') != read(original / 'seal.json')['manifest_sha256']:
        raise ValueError('Original manifest changed')
    base = old_manifest['spec']
    cohort = read(ROOT / spec['failure_census'] / 'records.json')
    chosen = {row['task_id']: row['category'] for row in cohort}
    chosen[spec['unsafe_witness']] = 'known_safety_witness'
    if spec['selection'] == 'first_per_mechanism_plus_safety':
        categories = sorted(set(chosen.values()))
        chosen = {next(name for name in sorted(chosen) if chosen[name] == category): category for category in categories}
    elif spec['selection'] != 'all_recorded_path_failures_plus_safety':
        raise ValueError('Unknown development cohort rule')
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study / 'protocol.yaml')
    old_tasks = {t['task_id']: t for t in old_manifest['tasks']}
    units, tasks = [], []
    for name, category in sorted(chosen.items()):
        task, source = old_tasks[name], original / 'results' / name
        receipt = read(source / 'receipt.json')
        if any(digest(source / n) != sha for n, sha in receipt.items()):
            raise ValueError('Original action changed: ' + name)
        folder = study / 'inputs' / name
        folder.mkdir(parents=True)
        shutil.copyfile(original / task['truth'], folder / 'truth.npz')
        shutil.copyfile(original / task['observation'], folder / 'observation.npz')
        with np.load(source / 'trajectory.npz', allow_pickle=False) as data:
            np.savez_compressed(folder / 'action.npz', targets=data['targets'], target_groups=data['target_groups'])
        unit = task | {'unit_id': name, 'category': category, 'old_status': read(source / 'metrics.json')['status'],
                       'original_receipt_sha256': digest(source / 'receipt.json')}
        for field in ('truth', 'observation', 'action'):
            path = folder / (field + '.npz')
            unit[field] = path.relative_to(study).as_posix()
            unit[field + '_sha256'] = digest(path)
        units.append(unit)
        for policy in spec['policies']:
            tasks.append(unit | {'policy': policy, 'task_id': name + '__' + policy})
    sources = scientific_sources()
    for relative in sources:
        target = study / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    manifest = {'protocol': spec['protocol'], 'evidence': 'fixed_old_action_development_not_independent_confirmation',
                'spec': base, 'design': spec, 'units': units, 'tasks': tasks, 'sources': sources,
                'environment': environment_record(), 'protocol_sha256': digest(study / 'protocol.yaml'),
                'original_manifest_sha256': digest(original / 'manifest.json'), 'new_independent_samples': 0,
                'prepared_before_method_evaluation': True}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    return {'units': len(units), 'tasks': len(tasks), 'manifest_sha256': digest(study / 'manifest.json')}


def worker(argument):
    study, unit, manifest = argument
    study, base = Path(study), manifest['spec']
    config = base['model'] | manifest['design']['routing']
    tasks = [t for t in manifest['tasks'] if t['unit_id'] == unit['unit_id']]
    rows = []
    with threadpool_limits(limits=1):
        truth, guides, measurement = _load_inputs(study, unit)
        guides = guides[:unit['budget']]
        if digest(study / unit['action']) != unit['action_sha256']:
            raise ValueError('Frozen action changed')
        with np.load(study / unit['action'], allow_pickle=False) as data:
            targets, ids = data['targets'].copy(), data['target_groups'].copy()
        setup = None
        try:
            groups, domain = construct_groups(measurement, base['room_m'], config)
            reference = reference_groups(truth, base['room_m'], config)
        except Exception as error:
            setup = error
        for task in tasks:
            result = _blank_execution(guides, 'NOT_EXECUTED')
            metrics = dict(task)
            try:
                if setup is not None:
                    raise setup
                result = execute(guides, targets, domain, base['room_m'], config, measurement, task['policy'])
                metrics.update(evaluate(result, targets, ids, groups, measurement, truth, base['room_m'], config))
                if not metrics['motion']['initial_safety_ok'] or metrics['motion']['interval_safety_ok'] is False:
                    metrics['status'] = 'SAFETY_VIOLATION'
                elif result.status != 'ARRIVED':
                    metrics['status'] = result.status
                else:
                    metrics['status'] = 'FULL_SUCCESS' if metrics['full_task_success'] else 'PARTIAL_SERVICE'
                metrics['continuous_actual_service'] = continuous_summary(reference, truth, result.positions[-1][result.assignment >= 0], config) if len(result.velocities) else None
            except ScientificFailure as error:
                metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence)
            except Exception:
                metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc())
            _save_task(study / 'results' / task['task_id'], task, config, result, targets, ids,
                       {'old_action_fixed': True, 'original_receipt_sha256': unit['original_receipt_sha256']}, metrics)
            rows.append(metrics)
            if metrics['status'] in STOP:
                break
    return rows


def run(study, workers, worker_function=None):
    manifest = verify(study)
    if not 1 <= workers <= manifest['design']['maximum_workers']:
        raise ValueError('Worker budget exceeded')
    if (study / 'results').exists():
        raise FileExistsError('No automatic scientific retry or overwrite')
    pending, rows, stopped = {}, [], False
    iterator = iter(manifest['units'])
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize_worker) as pool:
        def submit():
            unit = next(iterator, None)
            if unit:
                pending[pool.submit(worker_function or worker, (str(study), unit, manifest))] = unit['unit_id']
        for _ in range(workers):
            submit()
        while pending:
            done, _ = wait(pending, timeout=10, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                fresh = future.result()
                rows.extend(fresh)
                stopped |= any(r['status'] in STOP for r in fresh)
            if not stopped:
                for _ in done:
                    submit()
            count = sum((study / 'results' / t['task_id'] / 'receipt.json').is_file() for t in manifest['tasks'])
            atomic_json(study / 'status.json', {'updated_at': datetime.now().astimezone().isoformat(),
                'recorded': count, 'scheduled': len(manifest['tasks']), 'in_flight': len(pending), 'stopped': stopped})
    summary = {'recorded': len(rows), 'scheduled': len(manifest['tasks']), 'stopped': stopped,
               'missing': len(manifest['tasks']) - len(rows), 'new_independent_samples': manifest.get('new_independent_samples',0),
               'statuses': dict(Counter(r['status'] for r in rows)),
               'by_policy': {p: dict(Counter(r['status'] for r in rows if r['policy'] == p)) for p in manifest['design']['policies']}}
    write_json(study / 'summary.json', summary)
    return summary


def _replay_unit(argument):
    from udc.multigroup.execution import Execution
    study,unit,tasks,base,design,generated=argument;study=Path(study)
    checked,mismatches=0,[];config=base['model']|design['routing'];plans={}
    with threadpool_limits(limits=1):
        truth, _, measurement = _load_inputs(study, unit)
        setup=None
        try:
            groups, domain = construct_groups(measurement, base['room_m'], config)
            reference = reference_groups(truth, base['room_m'], config)
            if generated:
                from udc.multigroup.allocation import build_catalog,allocate,selected_targets
                catalogs=build_catalog(groups,measurement,domain,max(t['budget'] for t in tasks),'projected_mass',config)
        except ScientificFailure as error:setup=error
        for task in tasks:
            folder = study / 'results' / task['task_id']
            if not (folder / 'receipt.json').is_file():
                continue
            receipt = read(folder / 'receipt.json')
            if any(digest(folder / n) != sha for n, sha in receipt.items()):
                raise ValueError('Artifact changed')
            state, saved = read(folder / 'state.json'), read(folder / 'metrics.json')
            if 'motion' not in saved:
                if setup is not None and saved['status']!=setup.status:mismatches.append(task['task_id'])
                checked += 1
                continue
            with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                execution = Execution(data['positions'], data['velocities'], data['assignment'], state['execution_status'], (), state['evidence'])
                fresh = evaluate(execution, data['targets'], data['target_groups'], groups, measurement, truth, base['room_m'], config)
                actual = continuous_summary(reference, truth, execution.positions[-1][execution.assignment >= 0], config) if len(execution.velocities) else None
                if generated:
                    key=(task['resource'],task['allocator'])
                    if key not in plans:
                        allocation=allocate(catalogs,groups,task['budget'],task['allocator']);q,ids=selected_targets(allocation,config);plans[key]=(allocation,q,ids)
                    allocation,q,ids=plans[key]
                    if not np.array_equal(q,data['targets']) or not np.array_equal(ids,data['target_groups']) or list(allocation.counts)!=read(folder/'planning.json')['counts']:
                        mismatches.append(task['task_id']+':DECISION')
                else:
                    if digest(study/unit['action'])!=unit['action_sha256']:raise ValueError('Original fixed action changed')
                    with np.load(study/unit['action'],allow_pickle=False) as source:
                        if not np.array_equal(source['targets'],data['targets']) or not np.array_equal(source['target_groups'],data['target_groups']):mismatches.append(task['task_id']+':FIXED_ACTION')
            if any(fresh[k] != saved[k] for k in fresh) or actual != saved['continuous_actual_service']:
                mismatches.append(task['task_id'])
            checked += 1
    return {'checked':checked,'mismatches':mismatches}


def replay(study, workers=1):
    manifest=verify(study);tasks=defaultdict(list)
    for task in manifest['tasks']:tasks[task['unit_id']].append(task)
    arguments=[(str(study),u,tasks[u['unit_id']],manifest['spec'],manifest['design'],manifest.get('allocation_shared_by_all_routing_policies',False)) for u in manifest['units']]
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize_worker) as pool:
        results=list(pool.map(_replay_unit,arguments))
    checked=sum(r['checked'] for r in results);mismatches=[v for r in results for v in r['mismatches']]
    result = {'checked': checked, 'scheduled': len(manifest['tasks']), 'mismatches': mismatches,
              'all_match': not mismatches and checked == len(manifest['tasks']), 'scientific_retries': 0}
    write_json(study / 'offline_replay.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','run','replay'])
    parser.add_argument('--study', required=True)
    parser.add_argument('--protocol')
    parser.add_argument('--workers', type=int, default=32)
    args = parser.parse_args()
    study = Path(args.study).resolve()
    result = prepare(args.protocol, study) if args.action == 'prepare' else run(study, args.workers) if args.action == 'run' else replay(study,args.workers)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
