"""Prepare, lock, execute and independently replay opt-in static multi-group work.

Run ``.venv/Scripts/python.exe -m udc.experiments.static_multigroup --help``.
Preparation writes inputs and source locks BEFORE any method is evaluated.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import itertools
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import traceback

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement, StaticTruth, measure
from udc.multigroup.geometry import ScientificFailure, construct_groups
from udc.multigroup.allocation import allocate, build_catalog, selected_targets
from udc.multigroup.execution import Execution, execute
from udc.multigroup.evaluation import evaluate, reference_groups, common_service
from udc.multigroup.scenarios import make_truth, initial_guides


ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'Immutable artifact already exists: {path}')
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=_jsonable) + '\n', encoding='utf-8')


def scientific_sources():
    files = sorted((ROOT / 'src/udc').rglob('*.py'))
    files += [ROOT / 'pyproject.toml', ROOT / 'requirements/locks/windows-py312.txt']
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in files}


def environment_record():
    return {'python': platform.python_version(), 'implementation': platform.python_implementation(),
            'dependencies': {name: importlib.metadata.version(name)
                             for name in ('numpy', 'scipy', 'shapely', 'PyYAML', 'threadpoolctl')}}


def load_protocol(path):
    spec = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if spec['protocol'] not in ('static_multigroup_bounded_observation_v1', 'static_multigroup_bounded_observation_v2') or spec['schema_version'] != 1:
        raise ValueError('Unsupported multi-group protocol')
    if spec['group_counts'] != [1, 2, 3, 5] or spec['shapes'] != ['circle', 'ellipse', 'concave', 'irregular']:
        raise ValueError('v1 freezes the group-count and shape factors')
    if spec['layouts'] != ['equal_arc', 'projected_mass'] or spec['allocators'] != ['perimeter', 'demand', 'minimax']:
        raise ValueError('v1 freezes two layouts and three allocation policies')
    model = spec['model']
    positive = ['group_link_m', 'closing_radius_m', 'construction_margin_m', 'offset_m', 'sample_spacing_m', 'guide_radius_m',
                'clearance_m', 'guide_distance_m', 'wall_distance_m', 'planned_gap_m', 'max_gap_m',
                'bandwidth_m', 'dt_s', 'max_speed_m_s', 'deadline_s', 'position_tolerance_m',
                'curve_tolerance_m', 'speed_tolerance_m_s', 'numeric_tolerance_m']
    if any(not np.isfinite(model[key]) or model[key] <= 0 for key in positive):
        raise ValueError('Model lengths, times and tolerances must be positive and finite')
    for key in ('phase_bins', 'lloyd_iterations', 'planning_samples', 'evaluation_samples', 'hold_steps'):
        if type(model[key]) is not int or model[key] < 1:
            raise ValueError(f'{key} must be a positive integer')
    if (model['planned_gap_m'] >= model['max_gap_m']
            or model['guide_distance_m'] != 2 * model['guide_radius_m'] + model['clearance_m']
            or model['wall_distance_m'] != model['guide_radius_m'] + model['clearance_m']):
        raise ValueError('Inconsistent safety distances or planning/acceptance gap')
    errors = spec['error_bounds_m']
    if not errors or any(not np.isfinite(x) or x < 0 for x in errors) or len(set(errors)) != len(errors):
        raise ValueError('Unique finite nonnegative error bounds are required')
    if spec['measurement_model'] not in ('bounded_disk', 'common_bias'):
        raise ValueError('Unsupported bounded measurement model')
    if any(type(n) is not int or n < 1 for n in spec['resources'].values()):
        raise ValueError('Fixed per-requested-group resource budgets must be positive integers')
    if not 20 <= spec['confirmation_seeds']['count'] <= 30:
        raise ValueError('Each confirmation configuration requires 20-30 seeds')
    if spec['runtime']['maximum_workers'] > 2 or spec['runtime']['numerical_threads'] != 1:
        raise ValueError('At most two episode processes, one numerical thread each')
    for name in ('group_size_multipliers', 'group_hotspot_multipliers'):
        if name in spec and (len(spec[name]) != 5 or any(not np.isfinite(v) or v < 1 for v in spec[name])):
            raise ValueError(f'{name} needs five finite multipliers >= 1')
    return spec


def prepare(protocol_path, output, development=False, seeds=None, selection=None):
    spec = load_protocol(protocol_path)
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Use a new output directory; frozen studies are never overwritten')
    output.mkdir(parents=True, exist_ok=True)
    if seeds is None:
        start, count = spec['confirmation_seeds']['start'], spec['confirmation_seeds']['count']
        seeds = spec['development_seeds'] if development else list(range(start, start + count))
    seeds = [int(s) for s in seeds]
    if len(set(seeds)) != len(seeds) or any(s < 0 or 5000 <= s <= 5029 for s in seeds):
        raise ValueError('Seeds must be unique, nonnegative; 5000-5029 are reserved')
    if not development:
        expected = list(range(spec['confirmation_seeds']['start'], spec['confirmation_seeds']['start'] + spec['confirmation_seeds']['count']))
        if seeds != expected or selection:
            raise ValueError('Confirmation uses the whole frozen design and seed interval')
    selection = selection or {}
    counts = selection.get('group_counts', spec['group_counts'])
    shapes = selection.get('shapes', spec['shapes'])
    errors = selection.get('error_bounds_m', spec['error_bounds_m'])
    resources = selection.get('resources', list(spec['resources']))
    layouts = selection.get('layouts', spec['layouts'])
    allocators = selection.get('allocators', spec['allocators'])
    for values, allowed in [(counts, spec['group_counts']), (shapes, spec['shapes']),
                            (errors, spec['error_bounds_m']), (resources, spec['resources']),
                            (layouts, spec['layouts']), (allocators, spec['allocators'])]:
        if not values or any(value not in allowed for value in values):
            raise ValueError('Selection is outside the frozen protocol')
    sources = scientific_sources()
    for name in sources:
        target = output / 'source_snapshot' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    (output / 'protocol.yaml').write_bytes(Path(protocol_path).read_bytes())
    units, tasks = [], []
    for group_count, shape, seed in itertools.product(counts, shapes, seeds):
        cell = f'K{group_count}_{shape}_s{seed}'
        folder = output / 'inputs' / cell
        folder.mkdir(parents=True)
        truth = make_truth(group_count, shape, seed, spec['namespace'],
                           spec.get('group_size_multipliers'), spec.get('group_hotspot_multipliers'))
        max_count = max(spec['resources'].values()) * group_count
        guides = initial_guides(max_count, spec['room_m'])
        truth_path = folder / 'truth.npz'
        np.savez_compressed(truth_path, positions=truth.positions, radii=truth.radii,
                            demand=truth.demand, group_ids=truth.group_ids, guides=guides)
        shape_id = spec['shapes'].index(shape)
        for error in errors:
            observation = measure(truth, error, np.random.SeedSequence([spec['namespace'], 1, seed, group_count, shape_id]), spec['measurement_model'])
            observation_path = folder / f'observation_e{error:.2f}.npz'
            np.savez_compressed(observation_path, positions=observation.observation.positions,
                                radii=observation.observation.radii, demand=observation.observation.demand)
            unit = {'unit_id': f'{cell}_e{error:.2f}', 'group_count': group_count, 'shape': shape,
                    'seed': seed, 'error_bound_m': error,
                    'truth': truth_path.relative_to(output).as_posix(), 'truth_sha256': digest(truth_path),
                    'observation': observation_path.relative_to(output).as_posix(),
                    'observation_sha256': digest(observation_path)}
            units.append(unit)
            for resource, layout, allocator in itertools.product(resources, layouts, allocators):
                tasks.append({**unit, 'resource': resource, 'layout': layout, 'allocator': allocator,
                              'budget': spec['resources'][resource] * group_count,
                              'task_id': f'{unit["unit_id"]}_{resource}_{layout}_{allocator}'})
    # Seal after all inputs and sources exist, before constructing any boundary
    # or evaluating any deployment method.
    manifest = {'protocol': spec['protocol'], 'evidence': 'development' if development else 'prospective_confirmation',
                'namespace': spec['namespace'], 'seeds': seeds, 'spec': spec, 'environment': environment_record(),
                'protocol_sha256': digest(output / 'protocol.yaml'), 'sources': sources,
                'units': units, 'tasks': tasks, 'prepared_without_method_evaluation': True}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'seal.json', {'manifest_sha256': digest(output / 'manifest.json'),
                                      'units': len(units), 'tasks': len(tasks)})
    return {'output': str(output), 'units': len(units), 'tasks': len(tasks),
            'manifest_sha256': digest(output / 'manifest.json'), 'evidence': manifest['evidence']}


def verify(study):
    study = Path(study).resolve()
    seal = json.loads((study / 'seal.json').read_text(encoding='utf-8'))
    if digest(study / 'manifest.json') != seal['manifest_sha256']:
        raise ValueError('FROZEN_MANIFEST_CHANGED')
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    if scientific_sources() != manifest['sources']:
        raise ValueError('FROZEN_SOURCE_CHANGED: prepare a new study instead of updating old locks')
    if environment_record() != manifest['environment']:
        raise ValueError('FROZEN_ENVIRONMENT_CHANGED')
    if digest(study / 'protocol.yaml') != manifest['protocol_sha256']:
        raise ValueError('FROZEN_PROTOCOL_CHANGED')
    checked = {}
    for unit in manifest['units']:
        for field in ('truth', 'observation'):
            relative = unit[field]
            if relative not in checked:
                path = (study / relative).resolve()
                if not path.is_relative_to(study):
                    raise ValueError('Frozen input path escapes the study')
                checked[relative] = digest(path)
            if checked[relative] != unit[field + '_sha256']:
                raise ValueError(f'FROZEN_INPUT_CHANGED: {relative}')
    for name, sha in manifest['sources'].items():
        if digest(study / 'source_snapshot' / name) != sha:
            raise ValueError(f'FROZEN_SNAPSHOT_CHANGED: {name}')
    return manifest


def _load_inputs(study, task):
    with np.load(study / task['truth'], allow_pickle=False) as data:
        truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
        guides = data['guides'].copy()
    with np.load(study / task['observation'], allow_pickle=False) as data:
        measurement = BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                                         task['error_bound_m'], 'frozen_bounded_measurement')
    return truth, guides, measurement


def _blank_execution(guides, status):
    return Execution(np.asarray([guides]), np.empty((0, len(guides), 2)), np.full(len(guides), -1, dtype=int),
                     status, (), {})


def _save_task(folder, task, config, execution, targets, target_groups, planning, metrics):
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / 'config.json', {'task': task, 'model': config})
    write_json(folder / 'planning.json', planning)
    write_json(folder / 'metrics.json', metrics)
    write_json(folder / 'state.json', {'status': metrics['status'], 'execution_status': execution.status,
                                      'evidence': execution.evidence, 'paths': execution.paths})
    if 'traceback' in metrics:
        (folder / 'runner_error.txt').write_text(metrics['traceback'], encoding='utf-8')
    np.savez_compressed(folder / 'trajectory.npz', positions=execution.positions,
                        velocities=execution.velocities, assignment=execution.assignment,
                        targets=targets, target_groups=target_groups)
    write_json(folder / 'receipt.json', {name: digest(folder / name) for name in
                                        ('config.json', 'planning.json', 'metrics.json', 'state.json', 'trajectory.npz')})


def _unit_worker(arguments):
    study_string, tasks, spec, motion = arguments
    study, config = Path(study_string), spec['model']
    records = []
    with threadpool_limits(limits=1):
        truth, all_guides, measurement = _load_inputs(study, tasks[0])
        groups, domain, catalogs, setup_error = (), None, {}, None
        try:
            groups, domain = construct_groups(measurement, spec['room_m'], config)
            for layout in {task['layout'] for task in tasks}:
                catalogs[layout] = build_catalog(groups, measurement, domain, max(t['budget'] for t in tasks), layout, config)
        except ScientificFailure as error:
            setup_error = (error.status, str(error), error.evidence)
        except Exception:
            setup_error = ('EXPERIMENT_ERROR', traceback.format_exc(), {})
        for task in tasks:
            folder = study / 'results' / task['task_id']
            if folder.exists():
                if not (folder / 'receipt.json').is_file():
                    raise ValueError(f'Incomplete task requires artifact inspection, not scientific retry: {folder}')
                receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
                if any(digest(folder / name) != sha for name, sha in receipt.items()):
                    raise ValueError(f'Saved task artifact changed: {folder}')
                records.append(json.loads((folder / 'metrics.json').read_text(encoding='utf-8')))
                continue
            guides = all_guides[:task['budget']]
            targets, target_groups = np.empty((0, 2)), np.empty(0, dtype=int)
            execution = _blank_execution(guides, 'NOT_EXECUTED')
            planning, metrics = {}, {key: task[key] for key in ('task_id', 'unit_id', 'group_count', 'shape', 'seed',
                                                              'error_bound_m', 'resource', 'layout', 'allocator', 'budget')}
            try:
                if setup_error:
                    status, message, evidence = setup_error
                    if status == 'EXPERIMENT_ERROR':
                        raise RuntimeError(message)
                    raise ScientificFailure(status, message, **evidence)
                allocation = allocate(catalogs[task['layout']], groups, task['budget'], task['allocator'])
                targets, target_groups = selected_targets(allocation, config)
                lower = [int(np.ceil(group.boundary.length / config['planned_gap_m'])) for group in groups]
                planning = {'observed_group_count': len(groups), 'observed_group_rows': [g.rows for g in groups],
                            'observed_lengths_m': [g.boundary.length for g in groups],
                            'counts': allocation.counts, 'active': allocation.active,
                            'idle': task['budget'] - allocation.active, 'objective': allocation.objective,
                            'full_coverage_count_lower_bound': sum(lower),
                            'certified_planning_capacity_shortfall': task['budget'] < sum(lower),
                            'certificate_scope': 'observed_fixed_curves_planned_arc_gap_only',
                            'per_group_count_lower_bounds': lower,
                            'targets': targets, 'target_groups': target_groups,
                            'selected_sites': [c.sites for c in allocation.candidates],
                            'catalog': [{str(n): {'uncovered': c.uncovered, 'normalized_cost': c.normalized_cost,
                                                  'max_gap_m': c.max_gap_m, 'phase': c.phase}
                                         for n, c in catalog.items()} for catalog in catalogs[task['layout']]],
                            'allocation_optimum_scope': ('exact_lexicographic_on_finite_independent_catalogs'
                                                         if task['allocator'] == 'minimax' else 'proportional_baseline'),
                            'navigation_domain_sha256': domain.geometry_sha256,
                            'geometry_wkb_hex': [g.occupied.wkb_hex for g in groups],
                            'deployment_curves': [g.boundary.deployment for g in groups]}
                if motion:
                    execution = execute(guides, targets, domain, spec['room_m'], config)
                metrics.update(evaluate(execution, targets, target_groups, groups, measurement, truth, spec['room_m'], config))
                metrics['planning_objective'] = allocation.objective
                metrics['active'] = allocation.active
                metrics['certified_planning_capacity_shortfall'] = planning['certified_planning_capacity_shortfall']
                if metrics['motion']['interval_safety_ok'] is False or not metrics['motion']['initial_safety_ok']:
                    metrics['status'] = 'SAFETY_VIOLATION'
                elif not motion:
                    metrics['status'] = 'NOT_EXECUTED'
                elif execution.status != 'ARRIVED':
                    metrics['status'] = execution.status
                elif metrics['full_task_success']:
                    metrics['status'] = 'FULL_SUCCESS'
                elif metrics['deployment_completed']:
                    metrics['status'] = 'PARTIAL_SERVICE'
                else:
                    metrics['status'] = 'ACCEPTANCE_FAILED'
            except ScientificFailure as error:
                metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence,
                               full_task_success=False, deployment_completed=False)
            except Exception:
                metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc(),
                               full_task_success=False, deployment_completed=False)
            _save_task(folder, task, config, execution, targets, target_groups, planning, metrics)
            records.append(metrics)
            if metrics['status'] in ('SAFETY_VIOLATION', 'EXPERIMENT_ERROR'):
                # The parent receives this failure row and stops scheduling further work.
                break
    return records


def summarize(manifest, records):
    statuses = Counter(row['status'] for row in records)
    completed = sum(bool(row.get('deployment_completed')) for row in records)
    safety = [row['motion'] for row in records if row.get('motion', {}).get('executed_intervals', 0)]
    mismatch = [row['task_id'] for row in records if row.get('group_partition_matches_truth') is False]
    errors = [row['task_id'] for row in records if row.get('error_bound_respected') is False]
    paired = defaultdict(dict)
    for row in records:
        paired[(row['unit_id'], row['resource'], row['layout'])][row['allocator']] = row
    dominance_failures, common_differences = [], []
    for key, rows in paired.items():
        if 'minimax' not in rows:
            continue
        own = rows['minimax']
        for baseline in ('perimeter', 'demand'):
            other = rows.get(baseline)
            if other is None or 'planning_objective' not in own or 'planning_objective' not in other:
                continue
            a, b = own['planning_objective'], other['planning_objective']
            first_difference = next((x - y for x, y in zip(a, b) if abs(x - y) > 1e-12), 0.)
            if first_difference > 0:
                dominance_failures.append({'pair': key, 'baseline': baseline, 'minimax': a, 'other': b})
            common_differences.append({'group_count': own['group_count'], 'shape': own['shape'],
                                       'seed': own['seed'], 'error_bound_m': own['error_bound_m'],
                                       'resource': own['resource'], 'layout': own['layout'], 'baseline': baseline,
                                       'worst_uncovered_delta': own['planned_common_service']['worst_group_uncovered']
                                           - other['planned_common_service']['worst_group_uncovered']})
    return {'evidence': manifest['evidence'], 'scheduled_tasks': len(manifest['tasks']), 'recorded_tasks': len(records),
            'not_yet_recorded': len(manifest['tasks']) - len(records), 'statuses': dict(statuses),
            'full_successes': statuses.get('FULL_SUCCESS', 0), 'deployment_completed': completed,
            'motion_tasks': len(safety), 'executed_intervals': sum(row['executed_intervals'] for row in safety),
            'unsafe_intervals': sum(row['unsafe_interval_count'] for row in safety),
            'partition_mismatch_task_ids': mismatch, 'error_bound_violation_task_ids': errors,
            'finite_catalog_dominance_failures': dominance_failures,
            'paired_common_reference_differences': common_differences,
            'limits': ['development is not prospective confirmation', 'finite catalog, not continuous optimality',
                       'separated static components', 'serial fixed assignment is not complete MAPF',
                       'geometric service is not demonstrated crowd influence']}


def run(study, workers=2, motion=True):
    study = Path(study).resolve()
    manifest = verify(study)
    if workers < 1 or workers > manifest['spec']['runtime']['maximum_workers']:
        raise ValueError('Worker count is outside the frozen protocol')
    if manifest['evidence'] == 'prospective_confirmation' and not motion:
        raise ValueError('Confirmation requires all scheduled motion; planning-only is development')
    if (study / 'summary.json').exists():
        raise FileExistsError('This study already has a summary; use replay for verification')
    grouped = defaultdict(list)
    for task in manifest['tasks']:
        grouped[task['unit_id']].append(task)
    arguments = [(str(study), tasks, manifest['spec'], motion) for tasks in grouped.values()]
    records, stopped = [], False
    if workers == 1:
        for argument in arguments:
            rows = _unit_worker(argument)
            records.extend(rows)
            if any(row['status'] in ('SAFETY_VIOLATION', 'EXPERIMENT_ERROR') for row in rows):
                stopped = True
                break
    else:
        # Bounded dispatch: at most two units in flight, not the entire grid.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            pending, iterator = {}, iter(arguments)
            for _ in range(workers):
                argument = next(iterator, None)
                if argument is not None:
                    pending[pool.submit(_unit_worker, argument)] = argument[1][0]['unit_id']
            while pending:
                future = next(as_completed(pending))
                pending.pop(future)
                rows = future.result()
                records.extend(rows)
                stopped |= any(row['status'] in ('SAFETY_VIOLATION', 'EXPERIMENT_ERROR') for row in rows)
                if not stopped:
                    argument = next(iterator, None)
                    if argument is not None:
                        pending[pool.submit(_unit_worker, argument)] = argument[1][0]['unit_id']
    summary = summarize(manifest, records)
    from udc.analysis.static_multigroup import primary_analysis
    summary['primary_analysis'] = primary_analysis(manifest, records)
    summary['stopped_on_error_or_safety'] = stopped
    write_json(study / 'summary.json', summary)
    return {key: value for key, value in summary.items() if key != 'paired_common_reference_differences'}


def replay(study):
    study = Path(study).resolve()
    manifest = verify(study)
    mismatches, checked = [], 0
    for task in manifest['tasks']:
        folder = study / 'results' / task['task_id']
        if not (folder / 'receipt.json').exists():
            continue
        receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
        if any(digest(folder / name) != sha for name, sha in receipt.items()):
            raise ValueError(f'Saved task changed: {folder}')
        metrics = json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))
        if 'motion' not in metrics:
            continue
        truth, _, measurement = _load_inputs(study, task)
        groups, _ = construct_groups(measurement, manifest['spec']['room_m'], manifest['spec']['model'])
        state = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
        with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
            execution = Execution(data['positions'], data['velocities'], data['assignment'], state['execution_status'], (), state['evidence'])
            fresh = evaluate(execution, data['targets'], data['target_groups'], groups, measurement, truth,
                             manifest['spec']['room_m'], manifest['spec']['model'])
        checked += 1
        if any(fresh[key] != metrics[key] for key in fresh):
            mismatches.append(task['task_id'])
    return {'checked': checked, 'mismatches': mismatches, 'all_match': not mismatches}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--protocol', default='configs/step1/static_multigroup_v2.yaml')
    prep.add_argument('--output', required=True)
    prep.add_argument('--development', action='store_true')
    prep.add_argument('--seeds', nargs='+', type=int)
    prep.add_argument('--groups', nargs='+', type=int)
    prep.add_argument('--shapes', nargs='+')
    prep.add_argument('--errors', nargs='+', type=float)
    prep.add_argument('--resources', nargs='+')
    prep.add_argument('--layouts', nargs='+')
    prep.add_argument('--allocators', nargs='+')
    execute_command = commands.add_parser('run')
    execute_command.add_argument('--study', required=True)
    execute_command.add_argument('--workers', type=int, default=2)
    execute_command.add_argument('--planning-only', action='store_true')
    for name in ('verify', 'replay'):
        command = commands.add_parser(name)
        command.add_argument('--study', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        selection = {key: value for key, value in {'group_counts': args.groups, 'shapes': args.shapes,
                     'error_bounds_m': args.errors, 'resources': args.resources, 'layouts': args.layouts,
                     'allocators': args.allocators}.items() if value is not None}
        result = prepare(args.protocol, args.output, args.development, args.seeds, selection)
    elif args.command == 'run':
        result = run(args.study, args.workers, not args.planning_only)
    elif args.command == 'replay':
        result = replay(args.study)
    else:
        manifest = verify(args.study)
        result = {'verified': True, 'units': len(manifest['units']), 'tasks': len(manifest['tasks'])}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
