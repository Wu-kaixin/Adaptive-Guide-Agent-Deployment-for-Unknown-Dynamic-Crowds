"""Frozen fixed-action diagnosis under an explicitly NEW exact-sensing condition.

The old bounded-error observation/results are never relabelled or overwritten.
This supplements exact Step 1; the nonzero-error certificate remains unsolved.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np
import psutil
import yaml
from threadpoolctl import threadpool_limits

from .static_multigroup import ROOT, digest, scientific_sources, environment_record, write_json
from .frozen_resume import atomic_json
from .worker_resources import initialize_worker
from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.geometry import construct_groups
from udc.multigroup.exact_reference_certificate import exact_observation_certificate
from udc.multigroup.service import continuous_euclidean_service
from udc.deployment.planners import demand_profile


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checked(path, checksum):
    if digest(path) != checksum:
        raise ValueError('Fixed exact-sensing source artifact changed: ' + str(path))


def prepare(protocol, study):
    study = Path(study).resolve()
    design = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if design['protocol'] != 'exact_observation_quantitative_development_v1' or design['declared_error_m'] != 0.:
        raise ValueError('Explicit new exact-sensing development condition required')
    original = ROOT / design['original']
    checked(original / 'manifest.json', read(original / 'seal.json')['manifest_sha256'])
    old = read(original / 'manifest.json')
    selected = [{'sequence_id': s['sequence_id'], 'perturbation_m': f['perturbation_m']}
                for s in old['sequences'][:2] for f in s['frames']]
    selected += [{'sequence_id': 'K2_concave_s2210_common_bias', 'perturbation_m': .03},
                 {'sequence_id': 'K5_concave_s2210_bounded_disk', 'perturbation_m': .03}]
    sequences = {s['sequence_id']: s for s in old['sequences']}
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study / 'protocol.yaml')
    cases = []
    for index, selected_pair in enumerate(selected):
        sequence = sequences[selected_pair['sequence_id']]
        checked(original / sequence['truth'], sequence['truth_sha256'])
        folder = study / 'inputs' / f'{index:03d}'
        folder.mkdir(parents=True)
        with np.load(original / sequence['truth'], allow_pickle=False) as data:
            np.savez_compressed(folder / 'exact_observation.npz', positions=data['positions'], radii=data['radii'], demand=data['demand'])
        layouts = {}
        prefix = sequence['sequence_id'] + f'_p{selected_pair["perturbation_m"]}_'
        for name, method in [('greedy', 'continuous_euclidean_greedy'), ('retained', 'euclidean_retain_0.002')]:
            source = original / 'results' / (prefix + method)
            for filename, checksum in read(source / 'receipt.json').items():
                checked(source / filename, checksum)
            with np.load(source / 'trajectory.npz', allow_pickle=False) as data:
                layouts[name] = data['targets'].copy()
        np.savez_compressed(folder / 'layouts.npz', **layouts)
        cases.append(selected_pair | {'case_id': f'{index:03d}', 'old_truth_sha256': sequence['truth_sha256'],
            'exact_observation': (folder / 'exact_observation.npz').relative_to(study).as_posix(),
            'exact_observation_sha256': digest(folder / 'exact_observation.npz'),
            'layouts': (folder / 'layouts.npz').relative_to(study).as_posix(), 'layouts_sha256': digest(folder / 'layouts.npz')})
    sources = scientific_sources()
    for name in sources:
        target = study / 'source_snapshot' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    manifest = {'design': design, 'cases': cases, 'sources': sources, 'environment': environment_record(),
        'model': old['base_spec']['model'], 'room_m': old['base_spec']['room_m'],
        'source_manifest_sha256': digest(original / 'manifest.json'), 'new_independent_samples': 0,
        'new_motion_executions': 0, 'old_bounded_error_results_relabelled': False,
        'scope': 'new exact-observation input condition on old fixed layouts; no general nonzero-error claim'}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})


def worker(argument):
    study, case = argument
    study = Path(study)
    manifest = read(study / 'manifest.json')
    design, model = manifest['design'], manifest['model']
    for field in ('exact_observation', 'layouts'):
        checked(study / case[field], case[field + '_sha256'])
    with np.load(study / case['exact_observation'], allow_pickle=False) as data:
        observation = Observation(data['positions'], data['radii'], data['demand'])
    measurement = BoundedMeasurement(observation, 0., 'explicit_new_perfect_sensing_fixture')
    with np.load(study / case['layouts'], allow_pickle=False) as data:
        retained, greedy = data['retained'].copy(), data['greedy'].copy()
    with threadpool_limits(limits=1):
        started = time.perf_counter()
        cert = exact_observation_certificate(measurement, manifest['room_m'], model, retained, greedy,
            design['delta'], design['tracking_bound_m'], design['max_cells_per_group'], design['max_depth'])
        elapsed = time.perf_counter() - started
        original_error_guard = exact_observation_certificate(BoundedMeasurement(observation, .12, 'nonzero_guard'),
            manifest['room_m'], model, retained, greedy)
        groups, _ = construct_groups(measurement, manifest['room_m'], model)
        floating_losses = []
        for group in groups:
            index = list(group.rows)
            density = demand_profile(group.boundary, observation.positions[index], observation.demand[index], model['bandwidth_m'])
            first = continuous_euclidean_service(group.boundary, density, greedy, model['max_gap_m'] / 2)
            second = continuous_euclidean_service(group.boundary, density, retained, model['max_gap_m'] / 2 - design['tracking_bound_m'])
            floating_losses.append(second['uncovered'] - first['uncovered'])
    violations = [i for i, (bound, actual) in enumerate(zip(cert['groups'], floating_losses)) if actual > bound['upper_loss']]
    result = case | {'certificate': cert, 'float64_shrunken_metric_group_losses': floating_losses,
        'numeric_crosscheck_violations': violations, 'nonzero_error_guard': original_error_guard,
        'nonidentical_layout': not np.array_equal(retained, greedy), 'certificate_wall_time_s': elapsed,
        'condition': 'exact_sensing_only_new_condition_not_old_noisy_result', 'new_independent_samples': 0}
    write_json(study / 'results' / (case['case_id'] + '.json'), result)
    return result


def execute(study):
    study = Path(study).resolve()
    checked(study / 'manifest.json', read(study / 'seal.json')['manifest_sha256'])
    manifest = read(study / 'manifest.json')
    if scientific_sources() != manifest['sources'] or environment_record() != manifest['environment']:
        raise ValueError('Exact-sensing frozen sources or environment changed')
    rows = []
    with ProcessPoolExecutor(max_workers=manifest['design']['workers'], initializer=initialize_worker) as pool:
        for row in pool.map(worker, [(str(study), case) for case in manifest['cases']]):
            rows.append(row)
            atomic_json(study / 'status.json', {'phase': 'running', 'completed_pairs': len(rows), 'scheduled_pairs': len(manifest['cases'])})
    summary = {'pairs': len(rows), 'accepted_exact_sensing': sum(r['certificate']['accept_retained'] for r in rows),
        'nonidentical_pairs': sum(r['nonidentical_layout'] for r in rows),
        'accepted_nonidentical_pairs': sum(r['nonidentical_layout'] and r['certificate']['accept_retained'] for r in rows),
        'numeric_crosscheck_violations': sum(len(r['numeric_crosscheck_violations']) for r in rows),
        'nonzero_error_accepted': sum(r['nonzero_error_guard']['accept_retained'] for r in rows),
        'new_independent_samples': 0, 'new_motion_executions': 0,
        'general_nonzero_error_goal_complete': False,
        'scope': manifest['scope']}
    write_json(study / 'summary.json', summary)
    atomic_json(study / 'status.json', {'phase': 'completed', **summary})
    if summary['numeric_crosscheck_violations'] or summary['nonzero_error_accepted']:
        raise RuntimeError('Exact-sensing bound or nonzero-error guard refuted; preserve full precision')
    return summary


def run(protocol, study):
    study = Path(study).resolve()
    prepare(protocol, study)
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(), 'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.exact_certificate_study import execute;execute(p)")
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'execution.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=environment, stdout=log, stderr=subprocess.STDOUT)
        worker_process = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': child.pid, 'create_time': worker_process.create_time(),
            'exe': worker_process.exe(), 'cmdline': worker_process.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Exact-sensing development stopped; see frozen execution.log')
    return read(study / 'summary.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--protocol', required=True)
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.protocol, args.study), ensure_ascii=False), flush=True)
