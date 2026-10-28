"""Static near-wall and close-group admission/execution pressure tests."""
import argparse
from collections import Counter
import itertools
import json
from pathlib import Path
import shutil
import traceback

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from udc.multigroup.observation import StaticTruth, measure
from udc.multigroup.scenarios import make_truth, initial_guides
from udc.multigroup.geometry import construct_groups, ScientificFailure
from udc.multigroup.allocation import allocate, build_catalog, selected_targets
from udc.multigroup.execution import execute
from udc.multigroup.evaluation import evaluate
from udc.experiments.static_multigroup import (ROOT, digest, write_json, scientific_sources,
    environment_record, _blank_execution, _save_task, _load_inputs)


def run(protocol, output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Use a fresh pressure-test directory')
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    base = yaml.safe_load((ROOT / spec['base_protocol']).read_text(encoding='utf-8'))
    output.mkdir(parents=True)
    units = []
    for seed, case in itertools.product(range(spec['seeds']['start'], spec['seeds']['start'] + spec['seeds']['count']), spec['cases']):
        source = make_truth(spec['group_count'], spec['shape'], seed, spec['namespace'],
                            base['group_size_multipliers'], base['group_hotspot_multipliers'])
        positions = source.positions + np.asarray(spec['translations_m'][case])[source.group_ids]
        truth = StaticTruth(positions, source.radii, source.demand, source.group_ids)
        obs = measure(truth, spec['error_bound_m'], np.random.SeedSequence([spec['namespace'], seed]))
        unit_id = f'K2_s{seed}_{case}'
        folder = output / 'inputs' / unit_id
        folder.mkdir(parents=True)
        guides = initial_guides(spec['budget'], base['room_m'])
        if spec.get('guide_initialization') == 'common_bottom_wall_even_spacing':
            guides = np.c_[np.linspace(1.1, base['room_m'][0] - 1.1, spec['budget']),
                           np.full(spec['budget'], 1.1)]
        np.savez_compressed(folder / 'truth.npz', positions=truth.positions, radii=truth.radii,
                            demand=truth.demand, group_ids=truth.group_ids, guides=guides)
        np.savez_compressed(folder / 'observation.npz', positions=obs.observation.positions,
                            radii=obs.observation.radii, demand=obs.observation.demand)
        units.append({'unit_id': unit_id, 'task_id': unit_id, 'seed': seed, 'case': case,
                      'group_count': 2, 'shape': spec['shape'], 'error_bound_m': spec['error_bound_m'],
                      'budget': spec['budget'], 'resource': '5', 'layout': spec['layout'], 'allocator': spec['allocator'],
                      'truth': str((folder / 'truth.npz').relative_to(output)), 'truth_sha256': digest(folder / 'truth.npz'),
                      'observation': str((folder / 'observation.npz').relative_to(output)),
                      'observation_sha256': digest(folder / 'observation.npz')})
    sources = scientific_sources()
    for relative in sources:
        target = output / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    manifest = {'spec': spec, 'base_spec': base, 'units': units, 'sources': sources,
                'environment': environment_record(), 'protocol_sha256': digest(protocol),
                'evidence': 'development', 'prepared_before_method_evaluation': True}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'seal.json', {'manifest_sha256': digest(output / 'manifest.json')})
    rows = []
    with threadpool_limits(limits=1):
        for index, task in enumerate(units):
            truth, guides, obs = _load_inputs(output, task)
            execution = _blank_execution(guides, 'NOT_EXECUTED')
            targets, target_groups, planning = np.empty((0, 2)), np.empty(0, int), {}
            metrics = {**task, 'full_task_success': False, 'deployment_completed': False}
            try:
                groups, domain = construct_groups(obs, base['room_m'], base['model'])
                catalogs = build_catalog(groups, obs, domain, spec['budget'], spec['layout'], base['model'])
                allocation = allocate(catalogs, groups, spec['budget'], spec['allocator'])
                targets, target_groups = selected_targets(allocation, base['model'])
                planning = {'counts': allocation.counts, 'objective': allocation.objective,
                            'targets': targets, 'target_groups': target_groups}
                execution = execute(guides, targets, domain, base['room_m'], base['model'])
                metrics.update(evaluate(execution, targets, target_groups, groups, obs, truth, base['room_m'], base['model']))
                if not metrics['motion']['initial_safety_ok'] or metrics['motion']['interval_safety_ok'] is False:
                    metrics['status'] = 'SAFETY_VIOLATION'
                elif execution.status != 'ARRIVED':
                    metrics['status'] = execution.status
                elif metrics['full_task_success']:
                    metrics['status'] = 'FULL_SUCCESS'
                elif metrics['deployment_completed']:
                    metrics['status'] = 'PARTIAL_SERVICE'
                else:
                    metrics['status'] = 'ACCEPTANCE_FAILED'
            except ScientificFailure as error:
                metrics.update(status=error.status, failure_message=str(error), failure_evidence=error.evidence)
            except Exception:
                metrics.update(status='EXPERIMENT_ERROR', traceback=traceback.format_exc())
            _save_task(output / 'results' / task['task_id'], task, base['model'], execution,
                       targets, target_groups, planning, metrics)
            rows.append(metrics)
            print(json.dumps({'pressure_rows': index + 1, 'scheduled': len(units), 'status': metrics['status']}), flush=True)
            if metrics['status'] in spec['stop_on']:
                break
    summary = {'evidence': 'development_pressure', 'scheduled': len(units), 'recorded': len(rows),
               'statuses': dict(Counter(r['status'] for r in rows)),
               'by_case': {case: dict(Counter(r['status'] for r in rows if r['case'] == case)) for case in spec['cases']},
               'executed_intervals': sum(r.get('motion', {}).get('executed_intervals', 0) for r in rows),
               'unsafe_intervals': sum(r.get('motion', {}).get('unsafe_interval_count') or 0 for r in rows),
               'scientific_retries': 0}
    write_json(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', default='configs/step1/static_pressure_v1.yaml')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.protocol, args.output), indent=2))
