"""Read-only decision reconstruction and saved-trajectory audit for new Step 1 studies.

Loaded by scripts/audit_step1_new_study.py after selecting the study's frozen
source tree. No execution is rerun and no scientific outcome is overwritten.
"""
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from udc.experiments.static_multigroup import verify, digest, write_json, _load_inputs
from udc.experiments.static_mechanism import continuous_summary
from udc.multigroup.execution import Execution
from udc.multigroup.evaluation import evaluate, reference_groups
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.allocation import allocate, build_catalog, selected_targets
from udc.multigroup.representation_gate import serial_target_admissible


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def saved_execution(folder):
    state = read(folder / 'state.json')
    with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
        arrays = {name: data[name].copy() for name in data.files}
    execution = Execution(arrays['positions'], arrays['velocities'], arrays['assignment'],
                          state['execution_status'], (), state['evidence'])
    return execution, arrays


def motion_check(folder, truth, observation, groups, base):
    saved = read(folder / 'metrics.json')
    execution, arrays = saved_execution(folder)
    if 'motion' not in saved:
        return execution, saved, True
    fresh = evaluate(execution, arrays['targets'], arrays['target_groups'], groups,
                     observation, truth, base['room_m'], base['model'])
    actual = (continuous_summary(reference_groups(truth, base['room_m'], base['model']), truth,
              execution.positions[-1][execution.assignment >= 0], base['model'])
              if len(execution.velocities) else None)
    same = all(fresh[key] == saved[key] for key in fresh) and actual == saved['continuous_actual_service']
    if 'endpoint_tracking_error_m' in saved:
        active = execution.assignment >= 0
        tracking = float(np.linalg.norm(execution.positions[-1][active] - arrays['targets'][execution.assignment[active]], axis=1).max()) if np.any(active) else 0.
        same &= tracking == saved['endpoint_tracking_error_m']
    if base['model'].get('independent_segment_gate'):
        from shapely.ops import unary_union
        from udc.multigroup.geometry import outer_disk
        from udc.multigroup.path_gate import segment_gate
        _, domain = construct_groups(observation, base['room_m'], base['model'])
        state = read(folder / 'state.json')
        def free_space(current, moving):
            return domain.free.difference(unary_union([outer_disk(point, base['model']['guide_distance_m'], quad_segs=4)
                         for i, point in enumerate(current) if i != moving]))
        cursor = 0
        for path in state['paths']:
            guide, points = path['guide'], np.asarray(path['points'])
            intervals = np.flatnonzero(np.any(np.diff(execution.positions[:, guide], axis=0) != 0, axis=1))
            current = execution.positions[int(intervals[0]) if len(intervals) else cursor]
            free = free_space(current, guide)
            same &= all(segment_gate(a, b, current, guide, free, base['room_m'], base['model']) is None for a, b in zip(points[:-1], points[1:]))
            if len(intervals):
                cursor = int(intervals[-1]) + 1
        if state['execution_status'] == 'PATH_SAFETY_REJECTED':
            evidence = state['evidence']
            current, guide = execution.positions[-1], evidence['guide']
            fresh = segment_gate(evidence['start'], evidence['end'], current, guide,
                                 free_space(current, guide), base['room_m'], base['model'])
            same &= fresh == {k: v for k, v in evidence.items() if k != 'guide'}
    return execution, saved, same


def representation_worker(argument):
    study, tasks, base = argument
    study, config = Path(study), base['model']
    mismatches, checked = [], []
    with threadpool_limits(limits=1):
        truth, _, observation = _load_inputs(study, tasks[0])
        try:
            groups, domain = construct_groups(observation, base['room_m'], config)
            catalogs = {}
            for admission in ('euclidean_original', 'serial_polygon_consistent'):
                gate = (lambda points: serial_target_admissible(points, config['guide_distance_m'])) if admission == 'serial_polygon_consistent' else None
                catalogs[admission] = build_catalog(groups, observation, domain, max(t['budget'] for t in tasks),
                                                    'projected_mass', config, admission=gate)
            references = reference_groups(truth, base['room_m'], config)
        except ScientificFailure as error:
            for task in tasks:
                saved = read(study / 'results' / task['task_id'] / 'metrics.json')
                checked.append(task['task_id'])
                if saved['status'] != error.status:
                    mismatches.append(task['task_id'])
            return {'checked': checked, 'mismatches': mismatches}
        for task in tasks:
            folder = study / 'results' / task['task_id']
            if not (folder / 'receipt.json').is_file():
                continue
            saved = read(folder / 'metrics.json')
            try:
                allocation = allocate(catalogs[task['admission']], groups, task['budget'], task['allocator'])
                targets, ids = selected_targets(allocation, config)
                planning = read(folder / 'planning.json')
                _, arrays = saved_execution(folder)
                same = (np.array_equal(targets, arrays['targets']) and np.array_equal(ids, arrays['target_groups'])
                        and list(allocation.counts) == planning['counts'])
                if 'continuous_planned_service' in saved:
                    same &= continuous_summary(references, truth, targets, config) == saved['continuous_planned_service']
                _, _, motion_same = motion_check(folder, truth, observation, groups, base)
                same &= motion_same
            except ScientificFailure as error:
                same = saved['status'] == error.status
            checked.append(task['task_id'])
            if not same:
                mismatches.append(task['task_id'])
    return {'checked': checked, 'mismatches': mismatches}


def translation_worker(argument):
    from udc.experiments import translation_service_pilot as module
    study, sequence, base, pilot = argument
    study = Path(study)
    checked, mismatches = [], []

    def compare_save(folder, task, config, execution, targets, ids, planning, metrics):
        saved = read(folder / 'metrics.json')
        _, arrays = saved_execution(folder)
        # JSON-normalize numpy values using the frozen writer's conventions.
        def normalize(value):
            if isinstance(value, np.ndarray):
                return value.tolist()
            if isinstance(value, np.generic):
                return value.item()
            if isinstance(value, dict):
                return {str(k): normalize(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [normalize(v) for v in value]
            return value
        same = (np.array_equal(targets, arrays['targets']) and np.array_equal(ids, arrays['target_groups'])
                and normalize(planning) == read(folder / 'planning.json') and normalize(metrics) == saved)
        checked.append(task['task_id'])
        if not same:
            mismatches.append(task['task_id'])

    def replay_motion(study, task, guides, targets, ids, groups, domain, obs, truth, base, planning):
        folder = study / 'results' / task['task_id']
        execution, saved, same = motion_check(folder, truth, obs, groups, base)
        _, arrays = saved_execution(folder)
        same &= (np.array_equal(guides, execution.positions[0]) and np.array_equal(targets, arrays['targets'])
                 and np.array_equal(ids, arrays['target_groups']) and planning['counts'] == tuple(read(folder / 'planning.json')['counts']))
        checked.append(task['task_id'])
        if not same:
            mismatches.append(task['task_id'])
        return execution, saved

    module._save_task = compare_save
    module.motion_task = replay_motion
    # Reconstruct every observation-only decision and certificate; use saved
    # trajectories solely to audit the evaluator and the next initial state.
    with threadpool_limits(limits=1):
        try:
            module._sequence_impl((str(study), sequence, base, pilot))
        except ScientificFailure as error:
            expected = [t for t in read(study / 'manifest.json')['tasks'] if t['sequence_id'] == sequence['sequence_id']]
            remaining = [t for t in expected if t['task_id'] not in checked]
            for index, task in enumerate(remaining):
                saved = read(study / 'results' / task['task_id'] / 'metrics.json')
                expected_status = error.status if index == 0 else 'NOT_EXECUTED_SEQUENCE_FAILURE'
                checked.append(task['task_id'])
                if saved['status'] != expected_status:
                    mismatches.append(task['task_id'])
    from udc.interfaces import Observation
    from udc.multigroup.observation import StaticTruth
    from udc.multigroup.translation_service_bound import anchored_reference, reference_service
    with np.load(study / sequence['truth'], allow_pickle=False) as data:
        exact = Observation(data['positions'], data['radii'], data['demand'])
    reference = anchored_reference(exact, base['room_m'], base['model'], pilot['reference_quantum_m'], pilot['coordinate_rounding_bound_m'])
    delivered_checks = 0
    for frame in sequence['frames']:
        prefix = sequence['sequence_id'] + f'_p{frame["perturbation_m"]}'
        a, b = study / 'results' / (prefix + '_observed_retain_motion'), study / 'results' / (prefix + '_greedy_motion')
        if not (a / 'receipt.json').exists() or not (b / 'receipt.json').exists():
            continue
        cert = read(study / 'results' / (prefix + '_translation_guard_plan') / 'metrics.json').get('certificate', {})
        if cert.get('status') != 'MODEL_BOUND_NUMERICAL_AUDIT':
            continue
        if not read(a / 'metrics.json').get('deployment_completed') or not read(b / 'metrics.json').get('deployment_completed'):
            continue
        ea, _ = saved_execution(a)
        eb, _ = saved_execution(b)
        ua = reference_service(reference, ea.positions[-1][ea.assignment >= 0], base['model']['max_gap_m'] / 2)['groups']
        ub = reference_service(reference, eb.positions[-1][eb.assignment >= 0], base['model']['max_gap_m'] / 2)['groups']
        delivered_checks += 1
        if any(x - y > bound['upper_loss'] for x, y, bound in zip(ua, ub, cert['groups'])):
            mismatches.append(prefix + ':DELIVERED_MODEL_BOUND_VIOLATION')
    return {'checked': checked, 'mismatches': mismatches, 'delivered_model_bound_checks': delivered_checks}


def bootstrap(values, seed, repetitions):
    strata = defaultdict(list)
    for (k, shape, _), value in values.items():
        strata[k, shape].append(value)
    if len(strata) != 4:
        return {'estimate': None, 'percentile_95_ci': None, 'physical_blocks': len(values),
                'strata': len(strata), 'reason': 'Four prespecified strata are required'}
    rng = np.random.default_rng(seed)
    draws = np.zeros(repetitions)
    for numbers in strata.values():
        array = np.asarray(numbers, float)
        draws += rng.choice(array, (repetitions, len(array)), replace=True).mean(axis=1) / len(strata)
    return {'estimate': float(np.mean([np.mean(v) for v in strata.values()])),
            'percentile_95_ci': np.quantile(draws, [.025, .975]).tolist(),
            'physical_blocks': len(values), 'strata': len(strata)}


def analyze(study, workers=2):
    study = Path(study).resolve()
    manifest = verify(study)
    rows, missing = [], []
    for task in manifest['tasks']:
        folder = study / 'results' / task['task_id']
        if not (folder / 'receipt.json').is_file():
            missing.append(task['task_id'])
            continue
        receipt = read(folder / 'receipt.json')
        if any(digest(folder / name) != sha for name, sha in receipt.items()):
            raise ValueError('ARTIFACT_HASH_MISMATCH: ' + task['task_id'])
        rows.append(read(folder / 'metrics.json'))
    if missing:
        raise ValueError('Incomplete study; preserve missing ledger: ' + str(len(missing)))
    if any(row['status'] in ('EXPERIMENT_ERROR', 'SAFETY_VIOLATION', 'MODEL_BOUND_VIOLATION') for row in rows):
        raise ValueError('Technical stop: inspect complete ledger before any confirmation')
    translation = 'translation_service' in manifest['protocol']
    if translation:
        arguments = [(str(study), s, manifest['spec'], manifest['pilot_spec']) for s in manifest['sequences']]
        worker = translation_worker
    else:
        grouped = defaultdict(list)
        for task in manifest['tasks']:
            grouped[task['unit_id']].append(task)
        arguments = [(str(study), ts, manifest['spec']) for ts in grouped.values()]
        worker = representation_worker
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for index, result in enumerate(pool.map(worker, arguments)):
            results.append(result)
            print(json.dumps({'audited_units': index + 1, 'total': len(arguments)}), flush=True)
    write_json(study / 'offline_reconstruction_details.json', {'results': results,
               'manifest_sha256': digest(study / 'manifest.json')})
    checked = [name for r in results for name in r['checked']]
    mismatches = [name for r in results for name in r['mismatches']]
    unverified = sorted({t['task_id'] for t in manifest['tasks']} - set(checked))
    report = {'manifest_sha256': digest(study / 'manifest.json'), 'scheduled': len(manifest['tasks']),
              'recorded': len(rows), 'reconstructed_and_replayed': len(checked), 'missing': missing,
              'mismatches': mismatches, 'unverified': unverified,
              'all_match': not mismatches and not unverified, 'statuses': dict(Counter(r['status'] for r in rows)),
              'evidence': manifest['evidence'], 'scientific_retries': 0}
    stat = manifest['pilot_spec'].get('statistics')
    stat = stat if isinstance(stat, dict) else None
    if translation:
        plans = [r for r in rows if r.get('kind') == 'planning' and r.get('method') == 'translation_guard' and 'certificate' in r]
        report['reference_scope'] = manifest['pilot_spec']['reference']
        report['delivered_model_bound_checks'] = sum(r.get('delivered_model_bound_checks', 0) for r in results)
        report['maximum_anchored_original_worst_gap_difference'] = max((abs(r['anchored_true_service']['worst_group_uncovered'] - r['original_true_service']['worst_group_uncovered']) for r in plans), default=None)
        bounds = [max(g['upper_loss'] for g in r['certificate']['groups']) for r in plans if r['certificate']['status'] == 'MODEL_BOUND_NUMERICAL_AUDIT']
        report['model_upper_loss_bound_mean_max'] = [float(np.mean(bounds)), float(max(bounds))] if bounds else None
        report['certificate_counts'] = dict(Counter((r['model'] + ':' + r['certificate']['status']) for r in plans))
        report['nontrivial_retention_by_model_perturbation'] = {f'{model}:{eta}': sum(r['nontrivial_retention'] for r in plans if r['model'] == model and r['perturbation_m'] == eta)
            for model in manifest['pilot_spec']['models'] for eta in manifest['pilot_spec']['perturbations_m']}
        if stat:
            pairs = defaultdict(dict)
            for r in rows:
                if r.get('kind') == 'motion' and r.get('model') == stat['primary_model'] and r.get('perturbation_m') == stat['primary_perturbation_m']:
                    pairs[r['group_count'], r['shape'], r['seed']][r['method']] = r
            values = {}
            for key, pair in pairs.items():
                a, b = pair['translation_guard'], pair['greedy']
                if a.get('deployment_completed') and b.get('deployment_completed'):
                    values[key] = a['motion']['travel_m'] - b['motion']['travel_m']
            report['joint_completed_pairs'] = len(values)
            report['all_primary_pairs'] = len(pairs)
            if values:
                report['conditional_joint_completion_travel'] = bootstrap(values, stat['bootstrap_seed'], stat['bootstrap_repetitions'])
            bound = stat['missing_travel_difference_bound_m']
            lower = {key: values.get(key, bound[0]) for key in pairs}
            upper = {key: values.get(key, bound[1]) for key in pairs}
            report['full_ledger_identification_range_m'] = [bootstrap(v, 0, 1)['estimate'] for v in (lower, upper)]
    else:
        report['by_admission'] = {name: dict(Counter(r['status'] for r in rows if r['admission'] == name)) for name in manifest['pilot_spec']['admissions']}
        if stat:
            grouped = defaultdict(dict)
            for r in rows:
                grouped[r['group_count'], r['shape'], r['seed'], r['resource'], r['allocator']][r['admission']] = r
            blocks = defaultdict(list)
            for key, pair in grouped.items():
                blocks[key[:3]].append(int(bool(pair['serial_polygon_consistent'].get('deployment_completed'))) - int(bool(pair['euclidean_original'].get('deployment_completed'))))
            values = {key: np.mean(value) for key, value in blocks.items()}
            report['primary_safely_completed_difference'] = bootstrap(values, stat['bootstrap_seed'], stat['bootstrap_repetitions'])
    write_json(study / 'offline_audit.json', report)
    lines = ['# Step 1 离线复核', '', f"记录：{report['recorded']}/{report['scheduled']}；重建与轨迹复判：{len(checked)}；不一致：{len(mismatches)}。", '',
             f"证据层级：`{manifest['evidence']}`。", '', '```json', json.dumps(report, ensure_ascii=False, indent=2), '```', '',
             '共同平移服务界仅适用于新声明的锚定量化参考；原世界坐标参考仍是独立指标。失败与回退保留，失败短路程不作为效率优势。']
    (study / 'REPORT.zh.md').write_text('\n'.join(lines), encoding='utf-8')
    if not report['all_match']:
        raise ValueError('OFFLINE_RECONSTRUCTION_MISMATCH')
    return report
