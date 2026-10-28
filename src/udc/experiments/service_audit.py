"""Fixed-action development audit of static service metrics and quadrature."""
import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np
import yaml
from shapely.geometry import LineString, Point
from threadpoolctl import threadpool_limits

from udc.deployment.planners import demand_profile, quadrature_mass, periodic_cells, _interpolate
from udc.multigroup.geometry import construct_groups
from udc.multigroup.evaluation import reference_groups
from udc.multigroup.service import continuous_arc_service, continuous_euclidean_service
from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
                                              environment_record, _load_inputs)


STAGES = [
    'original_observed_arc_256',
    'observed_observed_arc_assigned_projected_continuous',
    'observed_observed_euclidean_assigned_continuous',
    'exact_reference_observed_euclidean_assigned_continuous',
    'exact_reference_exact_euclidean_assigned_continuous',
    'exact_reference_exact_euclidean_all_continuous',
    'exact_reference_exact_euclidean_all_2048',
]


def audit_task(study, task, spec):
    folder = study / 'results' / task['task_id']
    planning = json.loads((folder / 'planning.json').read_text(encoding='utf-8'))
    truth, _, measurement = _load_inputs(study, task)
    config = spec['model']
    observed, _ = construct_groups(measurement, spec['room_m'], config)
    exact = reference_groups(truth, spec['room_m'], config)
    if [g.rows for g in observed] != [g.rows for g in exact]:
        return {'task': task, 'status': 'PARTITION_MISMATCH', 'source': str(folder)}
    targets = np.asarray(planning['targets']).reshape(-1, 2)
    target_groups = np.asarray(planning['target_groups'])
    variants = {}
    full_mismatches = []
    max_projection = {}
    for geometry_name, groups in [('observed', observed), ('exact_reference', exact)]:
        for demand_name, people in [('observed', measurement.observation), ('exact', truth)]:
            values = defaultdict(list)
            for group in groups:
                index = np.asarray(group.rows)
                density = demand_profile(group.boundary, people.positions[index], people.demand[index], config['bandwidth_m'])
                own = targets[target_groups == group.group_id]
                line = LineString(np.vstack([group.boundary.deployment, group.boundary.deployment[0]]))
                sites = np.asarray([line.project(Point(p)) for p in own])
                if geometry_name == 'observed':
                    sites = np.asarray(planning['selected_sites'][group.group_id])
                max_projection[f'{geometry_name}_{group.group_id}'] = max(
                    (line.distance(Point(p)) for p in own), default=0.)
                for mode in ('arc_assigned_projected', 'euclidean_assigned', 'euclidean_all'):
                    robots = targets if mode == 'euclidean_all' else own
                    continuous = (continuous_arc_service(group.boundary, density, sites, config['max_gap_m'] / 2)
                                  if mode.startswith('arc') else
                                  continuous_euclidean_service(group.boundary, density, robots, config['max_gap_m'] / 2))
                    values[mode + '_continuous'].append(continuous['uncovered'])
                    for samples in (256, 2048, 8192):
                        arcs, mass, length = quadrature_mass(group.boundary, density, samples)
                        if not len(robots):
                            unmet = 1.
                        elif mode.startswith('arc'):
                            _, nearest, _ = periodic_cells(arcs, mass, sites, length)
                            unmet = float(mass[np.abs(nearest) > config['max_gap_m'] / 2].sum() / mass.sum())
                        else:
                            points = _interpolate(group.boundary, arcs)
                            nearest = np.linalg.norm(points[:, None] - robots[None], axis=2).min(axis=1)
                            unmet = float(mass[nearest > config['max_gap_m'] / 2].sum() / mass.sum())
                        values[mode + '_' + str(samples)].append(unmet)
                        if unmet == 0. and not continuous['full_curve_covered']:
                            full_mismatches.append({'geometry': geometry_name, 'demand': demand_name,
                                                    'group': group.group_id, 'mode': mode, 'samples': samples,
                                                    'continuous_uncovered_length_m': continuous['uncovered_length_m']})
            weights = np.asarray([g.demand_total for g in groups])
            weights = weights / weights.sum()
            for mode, uncovered in values.items():
                variants[f'{geometry_name}_{demand_name}_{mode}'] = {
                    'group_uncovered': uncovered, 'worst': max(uncovered),
                    'weighted': float(np.dot(weights, uncovered))}
    variants['original_observed_arc_256'] = {'worst': planning['objective'][0], 'weighted': planning['objective'][1]}
    path = [variants[key]['worst'] for key in STAGES]
    return {'task': task, 'status': 'AUDITED', 'source': str(folder), 'variants': variants,
            'projection_max_m': max_projection, 'decomposition_order': STAGES,
            'decomposition_values': path, 'telescoping_changes': np.diff(path).tolist(),
            'sampled_full_but_continuous_gap': full_mismatches,
            'planner_reconstruction_error': abs(variants['observed_observed_arc_assigned_projected_256']['worst'] - path[0])}


def run(protocol, output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Use a fresh audit directory')
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    selected, sources = {}, []
    for relative in spec['source_studies']:
        study = ROOT / relative
        manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
        seal = json.loads((study / 'seal.json').read_text(encoding='utf-8'))
        if digest(study / 'manifest.json') != seal['manifest_sha256']:
            raise ValueError('Changed source manifest')
        sources.append({'study': str(study), 'manifest_sha256': seal['manifest_sha256']})
        for task in manifest['tasks']:
            if not (task['resource'] == spec['selection']['resource']
                    and task['layout'] == spec['selection']['layout']
                    and task['allocator'] in spec['selection']['allocators']):
                continue
            folder = study / 'results' / task['task_id']
            receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
            if any(digest(folder / name) != sha for name, sha in receipt.items()):
                raise ValueError('Changed source result')
            for field in ('truth', 'observation'):
                if digest(study / task[field]) != task[field + '_sha256']:
                    raise ValueError('Changed source input')
            key = task['task_id']
            if key in selected and any(selected[key][1][field + '_sha256'] != task[field + '_sha256']
                                       for field in ('truth', 'observation')):
                raise ValueError('Duplicate task ID has different physical input')
            selected[key] = (study, task, manifest['spec'])
    output.mkdir(parents=True)
    write_json(output / 'manifest.json', {'protocol': spec, 'protocol_sha256': digest(protocol),
               'sources': scientific_sources(), 'environment': environment_record(), 'source_studies': sources,
               'tasks': [{'task': task, 'study': str(study),
                          'receipt_sha256': digest(study / 'results' / task['task_id'] / 'receipt.json')}
                         for study, task, _ in selected.values()]})
    rows = []
    with threadpool_limits(limits=1):
        for i, (study, task, source_spec) in enumerate(selected.values()):
            row = audit_task(study, task, source_spec)
            write_json(output / 'results' / (task['task_id'] + '.json'), row)
            rows.append(row)
            if (i + 1) % 20 == 0:
                print(json.dumps({'audited': i + 1, 'scheduled': len(selected)}), flush=True)
    pairs = defaultdict(dict)
    for row in rows:
        task = row['task']
        pairs[task['unit_id']][task['allocator']] = row
    effects = []
    for unit, pair in sorted(pairs.items()):
        if set(pair) != {'minimax', 'perimeter'} or any(row['status'] != 'AUDITED' for row in pair.values()):
            effects.append({'unit': unit, 'status': 'MISSING_PAIR'})
            continue
        task = pair['minimax']['task']
        deltas = [pair['minimax']['variants'][stage]['worst'] - pair['perimeter']['variants'][stage]['worst']
                  for stage in STAGES]
        effects.append({'unit': unit, 'group_count': task['group_count'], 'shape': task['shape'],
                        'seed': task['seed'], 'error_bound_m': task['error_bound_m'],
                        'stage_deltas': deltas, 'stage_delta_changes': np.diff(deltas).tolist()})
    summary = {'evidence': 'development_fixed_action_audit', 'rows': len(rows), 'pairs': len(pairs),
               'audited_rows': sum(r['status'] == 'AUDITED' for r in rows), 'stages': STAGES,
               'paired_effects': effects,
               'max_planner_reconstruction_error': max(r.get('planner_reconstruction_error', 0) for r in rows),
               'sampled_full_but_continuous_gap_count': sum(len(r.get('sampled_full_but_continuous_gap', [])) for r in rows),
               'interpretation': 'fixed targets; ordered decomposition has interactions and is not unique causal attribution'}
    write_json(output / 'summary.json', summary)
    write_json(output / 'receipt.json', {'manifest_sha256': digest(output / 'manifest.json'),
               'summary_sha256': digest(output / 'summary.json'),
               'results': {p.name: digest(p) for p in sorted((output / 'results').glob('*.json'))}})
    return {k: v for k, v in summary.items() if k != 'paired_effects'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', default='configs/step1/static_service_audit_v1.yaml')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.protocol, args.output), indent=2))
