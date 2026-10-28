"""Independent development inputs for geometry/demand/resource mechanisms."""
import argparse
from collections import Counter, defaultdict
import itertools
import json
from pathlib import Path
import shutil
import traceback

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from udc.deployment.planners import demand_profile
from udc.multigroup.observation import StaticTruth, BoundedMeasurement, measure
from udc.multigroup.scenarios import make_truth, initial_guides
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.allocation import allocate, build_catalog, selected_targets
from udc.multigroup.total_allocation import total_allocation
from udc.multigroup.evaluation import reference_groups, partition_matches, common_service
from udc.multigroup.service import continuous_euclidean_service
from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
    environment_record, _blank_execution, _save_task)
from udc.interfaces import Observation


def continuous_summary(groups, truth, targets, model):
    values = []
    for group in groups:
        index = np.asarray(group.rows)
        density = demand_profile(group.boundary, truth.positions[index], truth.demand[index], model['bandwidth_m'])
        values.append(continuous_euclidean_service(group.boundary, density, targets, model['max_gap_m'] / 2))
    weights = np.asarray([g.demand_total for g in groups])
    weights /= weights.sum()
    return {'worst_group_uncovered': max(v['uncovered'] for v in values),
            'weighted_uncovered': float(sum(w * v['uncovered'] for w, v in zip(weights, values))),
            'all_curves_covered': all(v['full_curve_covered'] for v in values), 'groups': values}


def prepare(protocol, output):
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    base = yaml.safe_load((ROOT / spec['base_protocol']).read_text(encoding='utf-8'))
    if output.exists():
        raise FileExistsError('A new development study directory is required')
    output.mkdir(parents=True)
    units = []
    for k, shape, seed, size_name, demand_name in itertools.product(
            spec['group_counts'], spec['shapes'], range(spec['seeds']['start'], spec['seeds']['start'] + spec['seeds']['count']),
            spec['size_patterns'], spec['demand_patterns']):
        base_truth = make_truth(k, shape, seed, spec['namespace'], spec['size_patterns'][size_name], [1.] * 5)
        demand = np.asarray(spec['demand_patterns'][demand_name], dtype=float)[base_truth.group_ids]
        if spec.get('demand_fields', {}).get(demand_name) == 'upper_x_quartile_12x_normalized_group_mean':
            for group_id in range(k):
                index = base_truth.group_ids == group_id
                x = base_truth.positions[index, 0]
                raw = np.where(x > np.quantile(x, .75), 12., 1.)
                demand[index] *= raw / raw.mean()
        truth = StaticTruth(base_truth.positions, base_truth.radii, demand, base_truth.group_ids)
        observation = measure(truth, spec['error_bound_m'], np.random.SeedSequence([spec['namespace'], seed, k]))
        unit_id = f'K{k}_{shape}_s{seed}_{size_name}_{demand_name}'
        folder = output / 'inputs' / unit_id
        folder.mkdir(parents=True)
        np.savez_compressed(folder / 'truth.npz', positions=truth.positions, radii=truth.radii,
                            demand=truth.demand, group_ids=truth.group_ids)
        np.savez_compressed(folder / 'observation.npz', positions=observation.observation.positions,
                            radii=observation.observation.radii, demand=observation.observation.demand)
        units.append({'unit_id': unit_id, 'group_count': k, 'shape': shape, 'seed': seed,
                      'size_pattern': size_name, 'demand_pattern': demand_name,
                      'truth': str((folder / 'truth.npz').relative_to(output)),
                      'truth_sha256': digest(folder / 'truth.npz'),
                      'observation': str((folder / 'observation.npz').relative_to(output)),
                      'observation_sha256': digest(folder / 'observation.npz')})
    sources = scientific_sources()
    for relative in sources:
        target = output / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    manifest = {'evidence': 'development', 'spec': spec, 'base_spec': base, 'units': units,
                'sources': sources, 'environment': environment_record(), 'protocol_sha256': digest(protocol),
                'prepared_before_method_evaluation': True, 'scheduled_rows': len(units) * 12}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'seal.json', {'manifest_sha256': digest(output / 'manifest.json')})
    return manifest


def run(protocol, output):
    output = Path(output).resolve()
    manifest = prepare(protocol, output)
    spec, base = manifest['spec'], manifest['base_spec']
    model = base['model']
    records = []
    stop = False
    with threadpool_limits(limits=1):
        for index, unit in enumerate(manifest['units']):
            with np.load(output / unit['truth'], allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            with np.load(output / unit['observation'], allow_pickle=False) as data:
                measurement = BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                                                 spec['error_bound_m'], spec['measurement_model'])
            setup_failure = None
            try:
                groups, domain = construct_groups(measurement, base['room_m'], model)
                catalogs = build_catalog(groups, measurement, domain, max(spec['budget_per_group']) * unit['group_count'],
                                         spec['layout'], model)
                reference = reference_groups(truth, base['room_m'], model)
            except ScientificFailure as error:
                setup_failure = (error.status, str(error), error.evidence)
            except Exception:
                setup_failure = ('EXPERIMENT_ERROR', traceback.format_exc(), {})
            for supply, policy in itertools.product(spec['budget_per_group'], spec['allocators']):
                budget = supply * unit['group_count']
                task = {**unit, 'budget': budget, 'resource': str(supply), 'layout': spec['layout'],
                        'allocator': policy, 'error_bound_m': spec['error_bound_m'],
                        'task_id': unit['unit_id'] + f'_b{supply}_{policy}'}
                execution = _blank_execution(initial_guides(budget, base['room_m']), 'NOT_EXECUTED')
                targets, target_groups, planning = np.empty((0, 2)), np.empty(0, int), {}
                metrics = {**task, 'status': 'NOT_EXECUTED', 'motion_evidence': None}
                try:
                    if setup_failure:
                        status, message, evidence = setup_failure
                        if status == 'EXPERIMENT_ERROR':
                            raise RuntimeError(message)
                        raise ScientificFailure(status, message, **evidence)
                    allocation = (total_allocation(catalogs, groups, budget) if policy == 'total' else
                                  allocate(catalogs, groups, budget, policy))
                    targets, target_groups = selected_targets(allocation, model)
                    planning = {'counts': allocation.counts, 'active': allocation.active,
                                'objective': allocation.objective, 'targets': targets, 'target_groups': target_groups}
                    metrics.update(planning_objective=allocation.objective, active=allocation.active,
                                   group_partition_matches_truth=partition_matches(groups, truth.group_ids),
                                   continuous_common_service=continuous_summary(reference, truth, targets, model),
                                   sampled_common_service=common_service(reference, truth, targets, model))
                except ScientificFailure as error:
                    metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence)
                except Exception:
                    metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc())
                _save_task(output / 'results' / task['task_id'], task, model, execution, targets, target_groups, planning, metrics)
                records.append(metrics)
                if metrics['status'] == 'EXPERIMENT_ERROR':
                    stop = True
                    break
            print(json.dumps({'units': index + 1, 'total_units': len(manifest['units']), 'rows': len(records)}), flush=True)
            if stop:
                break
    paired = defaultdict(dict)
    for row in records:
        paired[(row['unit_id'], row['resource'])][row['allocator']] = row
    effects = []
    for pair in paired.values():
        if 'minimax' not in pair:
            continue
        own = pair['minimax']
        for comparator in ('perimeter', 'demand', 'total'):
            other = pair.get(comparator)
            if not other or not all('continuous_common_service' in r for r in (own, other)):
                continue
            effects.append({key: own[key] for key in ('group_count', 'shape', 'seed', 'size_pattern', 'demand_pattern', 'resource')} |
                           {'baseline': comparator,
                            'worst_delta': own['continuous_common_service']['worst_group_uncovered'] - other['continuous_common_service']['worst_group_uncovered'],
                            'weighted_delta': own['continuous_common_service']['weighted_uncovered'] - other['continuous_common_service']['weighted_uncovered']})
    summary = {'evidence': 'development_planning_only', 'scheduled_rows': manifest['scheduled_rows'],
               'recorded_rows': len(records), 'statuses': dict(Counter(r['status'] for r in records)),
               'paired_effects': effects, 'stopped_on_program_error': stop,
               'physical_position_inputs': len(manifest['units']) // len(spec['demand_patterns']),
               'partition_mismatches': sum(r.get('group_partition_matches_truth') is False for r in records),
               'missing_physical_pairs_not_dropped_from_manifest': True}
    write_json(output / 'summary.json', summary)
    return {k: v for k, v in summary.items() if k != 'paired_effects'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', default='configs/step1/static_mechanism_v1.yaml')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.protocol, args.output), indent=2))
