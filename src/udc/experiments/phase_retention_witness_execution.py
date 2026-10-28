"""Execute the fixed actions of a declared posthoc coverage-cost witness."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import StaticTruth, BoundedMeasurement
from udc.multigroup.geometry import construct_groups
from udc.experiments.static_multigroup import ROOT, digest, write_json, scientific_sources, environment_record
from udc.experiments.static_phase_retention import motion_task


def run(protocol, output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Use a fresh execution-witness directory')
    spec = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    parent = ROOT / spec['parent_study']
    witness_path = ROOT / spec['case_selection']
    witness = json.loads(witness_path.read_text(encoding='utf-8'))
    manifest = json.loads((parent / 'manifest.json').read_text(encoding='utf-8'))
    sequence = next(s for s in manifest['sequences'] if s['sequence_id'] == witness['sequence_id'])
    frame = next(f for f in sequence['frames'] if f['perturbation_m'] == .03)
    baseline_folder = parent / 'results' / (sequence['sequence_id'] + '_nominal_deployment')
    baseline = json.loads((baseline_folder / 'metrics.json').read_text(encoding='utf-8'))
    if not baseline.get('deployment_completed'):
        raise ValueError('Witness requires a successful original nominal deployment')
    receipt = json.loads((baseline_folder / 'receipt.json').read_text(encoding='utf-8'))
    if any(digest(baseline_folder / name) != sha for name, sha in receipt.items()):
        raise ValueError('Changed nominal baseline')
    if digest(parent / sequence['truth']) != sequence['truth_sha256'] or digest(parent / frame['observation']) != frame['observation_sha256']:
        raise ValueError('Changed parent input')
    with np.load(baseline_folder / 'trajectory.npz', allow_pickle=False) as data:
        guides = data['positions'][-1].copy()
    output.mkdir(parents=True)
    (output / 'inputs').mkdir()
    with np.load(parent / sequence['truth'], allow_pickle=False) as data:
        truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
    np.savez_compressed(output / 'inputs/truth.npz', positions=truth.positions, radii=truth.radii,
                        demand=truth.demand, group_ids=truth.group_ids, guides=guides)
    shutil.copyfile(parent / frame['observation'], output / 'inputs/observation.npz')
    sources = scientific_sources()
    for relative in sources:
        target = output / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    tasks = []
    for method in spec['methods']:
        task_id = sequence['sequence_id'] + '_p0.03_' + method
        folder = parent / 'results' / task_id
        if digest(folder / 'receipt.json') != witness['source_receipts'][task_id]:
            raise ValueError('Changed parent action receipt')
        receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
        if any(digest(folder / name) != sha for name, sha in receipt.items()):
            raise ValueError('Changed parent action')
        shutil.copyfile(folder / 'trajectory.npz', output / 'inputs' / (method + '_actions.npz'))
        tasks.append({**{k: v for k, v in sequence.items() if k != 'frames'},
            'task_id': method, 'unit_id': sequence['sequence_id'], 'parent_task_id': task_id,
            'error_bound_m': manifest['spec']['declared_error_bound_m'], 'resource': '5', 'layout': method,
            'allocator': 'fixed_nominal_counts', 'kind': 'witness_redeployment', 'perturbation_m': .03,
            'truth': 'inputs/truth.npz', 'truth_sha256': digest(output / 'inputs/truth.npz'),
            'observation': 'inputs/observation.npz', 'observation_sha256': digest(output / 'inputs/observation.npz'),
            'actions': 'inputs/' + method + '_actions.npz',
            'actions_sha256': digest(output / 'inputs' / (method + '_actions.npz'))})
    sealed = {'spec': spec, 'base_spec': manifest['base_spec'], 'units': tasks, 'sources': sources,
              'environment': environment_record(), 'protocol_sha256': digest(protocol),
              'parent_manifest_sha256': digest(parent / 'manifest.json'), 'selected_witness_sha256': digest(witness_path),
              'baseline_receipt_sha256': digest(baseline_folder / 'receipt.json'),
              'evidence': spec['evidence'], 'prepared_before_execution': True}
    write_json(output / 'manifest.json', sealed)
    write_json(output / 'seal.json', {'manifest_sha256': digest(output / 'manifest.json')})
    with np.load(output / 'inputs/observation.npz', allow_pickle=False) as data:
        obs = BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                                 manifest['spec']['declared_error_bound_m'], 'frozen_witness_observation')
    records = []
    with threadpool_limits(limits=1):
        groups, domain = construct_groups(obs, manifest['base_spec']['room_m'], manifest['base_spec']['model'])
        for task in tasks:
            with np.load(output / task['actions'], allow_pickle=False) as data:
                targets, ids = data['targets'], data['target_groups']
            planning = {'actions_reused_without_reoptimization': True, 'parent_task_id': task['parent_task_id'],
                        'targets': targets, 'target_groups': ids}
            _, metrics = motion_task(output, task, guides, targets, ids, groups, domain, obs, truth, manifest['base_spec'], planning)
            records.append(metrics)
            if metrics['status'] in spec['stop_on']:
                break
    complete = len(records) == 2 and all(r.get('deployment_completed') for r in records)
    result = {'evidence': spec['evidence'], 'scheduled': 2, 'recorded': len(records),
              'statuses': dict(Counter(r['status'] for r in records)), 'both_safely_completed': complete,
              'unsafe_intervals': sum(r.get('motion', {}).get('unsafe_interval_count') or 0 for r in records),
              'executed_intervals': sum(r.get('motion', {}).get('executed_intervals', 0) for r in records),
              'actual_true_worst_delta': records[1]['continuous_actual_service']['worst_group_uncovered'] - records[0]['continuous_actual_service']['worst_group_uncovered'] if complete else None,
              'interpretation': 'selected descriptive fixed-action witness; not a population success-rate estimate'}
    write_json(output / 'summary.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', default='configs/step1/phase_retention_witness_execution_v1.yaml')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.protocol, args.output), indent=2))
