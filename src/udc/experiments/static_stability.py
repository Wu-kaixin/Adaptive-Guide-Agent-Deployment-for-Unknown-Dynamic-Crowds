"""Static, bounded observation perturbations with saved planning-only evidence."""
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
from udc.multigroup.observation import StaticTruth, BoundedMeasurement, measure, static_groups
from udc.multigroup.partition_stability import partition_stability_certificate
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.scenarios import make_truth, initial_guides
from udc.multigroup.allocation import allocate, build_catalog, selected_targets
from udc.multigroup.evaluation import reference_groups
from udc.experiments.static_mechanism import continuous_summary
from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
    environment_record, _blank_execution, _save_task)


def prepare(protocol, output):
    if output.exists():
        raise FileExistsError('Use a fresh perturbation study directory')
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    base = yaml.safe_load((ROOT / spec['base_protocol']).read_text(encoding='utf-8'))
    output.mkdir(parents=True)
    units = []
    for k, seed in itertools.product(spec['group_counts'], range(spec['seeds']['start'], spec['seeds']['start'] + spec['seeds']['count'])):
        truth = make_truth(k, spec['shape'], seed, spec['namespace'], base['group_size_multipliers'], base['group_hotspot_multipliers'])
        truth_path = output / 'inputs' / f'K{k}_s{seed}' / 'truth.npz'
        truth_path.parent.mkdir(parents=True)
        np.savez_compressed(truth_path, positions=truth.positions, radii=truth.radii, demand=truth.demand, group_ids=truth.group_ids)
        rng = np.random.default_rng(np.random.SeedSequence([spec['namespace'], seed, k, 913]))
        angles = rng.uniform(0, 2 * np.pi, len(truth.positions))
        magnitude = np.sqrt(rng.uniform(0, 1, len(truth.positions)))
        directions = magnitude[:, None] * np.c_[np.cos(angles), np.sin(angles)]
        for mode in spec['measurement_models']:
            original = measure(truth, spec['base_actual_error_bound_m'], np.random.SeedSequence([spec['namespace'], seed, k]), mode)
            for eta in spec['additional_perturbation_m']:
                measurement = original.observation.positions + eta * directions
                observation_path = truth_path.parent / f'{mode}_p{eta}.npz'
                np.savez_compressed(observation_path, positions=measurement, radii=truth.radii, demand=truth.demand)
                units.append({'unit_id': f'K{k}_s{seed}_{mode}_p{eta}', 'group_count': k, 'seed': seed,
                              'shape': spec['shape'], 'measurement_model': mode, 'perturbation_m': eta,
                              'truth': str(truth_path.relative_to(output)), 'truth_sha256': digest(truth_path),
                              'observation': str(observation_path.relative_to(output)),
                              'observation_sha256': digest(observation_path)})
    sources = scientific_sources()
    for relative in sources:
        target = output / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    manifest = {'spec': spec, 'base_spec': base, 'units': units, 'sources': sources,
                'environment': environment_record(), 'protocol_sha256': digest(protocol),
                'evidence': 'development_planning_only', 'prepared_before_method_evaluation': True}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'seal.json', {'manifest_sha256': digest(output / 'manifest.json')})
    return manifest


def run(protocol, output):
    output = Path(output).resolve()
    manifest = prepare(protocol, output)
    spec, base = manifest['spec'], manifest['base_spec']
    model, baselines, records = base['model'], {}, []
    with threadpool_limits(limits=1):
        for index, unit in enumerate(manifest['units']):
            with np.load(output / unit['truth'], allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            with np.load(output / unit['observation'], allow_pickle=False) as data:
                measurement = BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                                                 spec['declared_error_bound_m'], unit['measurement_model'])
            budget = spec['budget_per_group'] * unit['group_count']
            task = {**unit, 'task_id': unit['unit_id'], 'layout': spec['layout'], 'allocator': spec['allocator'],
                    'resource': '5', 'budget': budget, 'error_bound_m': spec['declared_error_bound_m']}
            execution = _blank_execution(initial_guides(budget, base['room_m']), 'NOT_EXECUTED')
            targets, target_groups, planning = np.empty((0, 2)), np.empty(0, int), {}
            metrics = {**task, 'status': 'NOT_EXECUTED', 'motion_evidence': None,
                       'max_actual_observation_error_m': float(np.linalg.norm(measurement.observation.positions - truth.positions, axis=1).max())}
            key = (unit['group_count'], unit['seed'], unit['measurement_model'])
            try:
                groups, domain = construct_groups(measurement, base['room_m'], model)
                catalogs = build_catalog(groups, measurement, domain, budget, spec['layout'], model)
                allocation = allocate(catalogs, groups, budget, spec['allocator'])
                targets, target_groups = selected_targets(allocation, model)
                partition = tuple(g.rows for g in groups)
                reference = reference_groups(truth, base['room_m'], model)
                service = continuous_summary(reference, truth, targets, model)
                planning = {'counts': allocation.counts, 'objective': allocation.objective,
                            'targets': targets, 'target_groups': target_groups, 'group_rows': partition}
                metrics['continuous_common_service'] = service
                if unit['perturbation_m'] == 0:
                    baselines[key] = (measurement.observation, partition, allocation.counts, targets.copy(), service)
                if key in baselines:
                    old_obs, old_partition, old_counts, old_targets, old_service = baselines[key]
                    distances = np.linalg.norm(targets[:, None] - old_targets[None], axis=2)
                    rms = None
                    if len(targets) == len(old_targets) and len(targets):
                        first, second = linear_sum_assignment(distances ** 2)
                        rms = float(np.sqrt(np.mean(distances[first, second] ** 2)))
                    unchanged = partition == old_partition
                    metrics.update(partition_unchanged=unchanged,
                        stability_certificate=partition_stability_certificate(old_obs, old_partition, model['group_link_m'], unit['perturbation_m']),
                        counts_l1=int(np.abs(np.asarray(allocation.counts) - old_counts).sum()) if unchanged else None,
                        target_hausdorff_m=float(max(distances.min(axis=0).max(), distances.min(axis=1).max())) if distances.size else None,
                        matched_target_rms_m=rms,
                        continuous_worst_delta=service['worst_group_uncovered'] - old_service['worst_group_uncovered'],
                        continuous_weighted_delta=service['weighted_uncovered'] - old_service['weighted_uncovered'])
            except ScientificFailure as error:
                metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence)
            except Exception:
                metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc())
            _save_task(output / 'results' / task['task_id'], task, model, execution, targets, target_groups, planning, metrics)
            records.append(metrics)
            if (index + 1) % 20 == 0:
                print(json.dumps({'rows': index + 1, 'scheduled': len(manifest['units'])}), flush=True)
            if metrics['status'] == 'EXPERIMENT_ERROR':
                break
    case = spec['threshold_counterexample']
    positions = np.asarray(case['positions'], float)
    original = Observation(positions, np.full(4, .2), np.ones(4))
    partition = static_groups(original, model['group_link_m'])
    shifts = np.repeat(np.asarray(case['group_shifts_m'], float), 2, axis=0)
    moved = Observation(positions + shifts, original.radii, original.demand)
    counterexample = {'original_positions': positions, 'perturbed_positions': moved.positions,
                      'group_counts': [len(partition), len(static_groups(moved, model['group_link_m']))],
                      'certificate': partition_stability_certificate(original, partition, model['group_link_m'], .01),
                      'purpose': 'declared synthetic boundary-of-assumptions case, not a sampled success-rate estimate'}
    write_json(output / 'threshold_counterexample.json', counterexample)
    summary = {'scheduled_rows': len(manifest['units']), 'recorded_rows': len(records),
               'physical_inputs': len(spec['group_counts']) * spec['seeds']['count'],
               'statuses': dict(Counter(r['status'] for r in records)), 'evidence': 'development_planning_only',
               'partition_changes': sum(r.get('partition_unchanged') is False for r in records),
               'certified_partition_changes': sum(r.get('partition_unchanged') is False and r.get('stability_certificate', {}).get('certified') is True for r in records),
               'count_changes': sum((r.get('counts_l1') or 0) > 0 for r in records),
               'maximum_true_error_m': max(r['max_actual_observation_error_m'] for r in records),
               'counterexample_group_counts': counterexample['group_counts']}
    write_json(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', default='configs/step1/static_stability_v1.yaml')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.protocol, args.output), indent=2))
