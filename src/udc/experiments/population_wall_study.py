"""Freeze and execute controlled population/wall API development cells."""
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import itertools
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

from .static_multigroup import ROOT, digest, write_json, scientific_sources, environment_record, _load_inputs
from .frozen_resume import atomic_json
from .worker_resources import initialize_worker
from udc.multigroup.controlled_scenarios import grid_truth, observed_safe_guides
from udc.multigroup.observation import measure
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.session import StaticDeploymentSession
from udc.analysis.session_audit import audit_journal


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def prepare(protocol, study):
    study = Path(study).resolve()
    design = yaml.safe_load(Path(protocol).read_text(encoding='utf-8'))
    if design['protocol'] != 'foundation_population_wall_development_v1' or design['evidence'] != 'controlled_development_not_confirmation':
        raise ValueError('Explicit controlled development protocol required')
    base = yaml.safe_load((ROOT / design['base_protocol']).read_text(encoding='utf-8'))
    config = base['model'] | design['model']
    study.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(protocol, study / 'protocol.yaml')
    units, cases = [], []
    for k, side, seed in itertools.product(design['group_counts'], design['grid_sides'],
            range(design['seeds']['start'], design['seeds']['start'] + design['seeds']['count'])):
        for wall in design['left_wall_clearance_m']:
            truth = grid_truth(k, side, seed, design['namespace'], wall)
            observation = measure(truth, design['actual_error_m'],
                np.random.SeedSequence([design['namespace'], seed, k, side, 81]))
            # Actual generator error is smaller than the declared controller bound.
            from udc.multigroup.observation import BoundedMeasurement
            observation = BoundedMeasurement(observation.observation, design['declared_error_m'], 'bounded_disk')
            guides = observed_safe_guides(observation, k * max(base['resources'].values()), base['room_m'], config)
            physical_id = f'K{k}_P{side * side}_s{seed}'
            name = physical_id + ('_interior' if wall is None else '_wall_' + str(wall))
            folder = study / 'inputs' / name
            folder.mkdir(parents=True)
            np.savez_compressed(folder / 'truth.npz', positions=truth.positions, radii=truth.radii,
                demand=truth.demand, group_ids=truth.group_ids, guides=guides)
            np.savez_compressed(folder / 'observation.npz', positions=observation.observation.positions,
                radii=truth.radii, demand=truth.demand)
            unit = {'unit_id': name, 'physical_id': physical_id, 'group_count': k,
                'people_per_group': side * side, 'total_people': len(truth.positions), 'seed': seed,
                'requested_left_wall_clearance_m': wall,
                'actual_minimum_human_wall_clearance_m': float(np.minimum(truth.positions - truth.radii[:, None],
                    np.asarray(base['room_m']) - truth.positions - truth.radii[:, None]).min()),
                'error_bound_m': design['declared_error_m']}
            for field in ('truth', 'observation'):
                path = folder / (field + '.npz')
                unit[field], unit[field + '_sha256'] = path.relative_to(study).as_posix(), digest(path)
            units.append(unit)
            for resource in design['resources']:
                cases.append({'case_id': name + '_' + resource, 'unit_id': name, 'resource': resource})
    sources = scientific_sources()
    for name in sources:
        path = study / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    manifest = {'design': design, 'spec': base, 'model': config, 'units': units, 'cases': cases,
        'sources': sources, 'environment': environment_record(), 'protocol_sha256': digest(study / 'protocol.yaml'),
        'evidence': design['evidence'], 'independent_generator_blocks': len({u['physical_id'] for u in units}),
        'scene_conditions': len(units), 'scheduled_deployments': len(cases), 'new_independent_confirmation_samples': 0}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    return {k: manifest[k] for k in ('independent_generator_blocks', 'scene_conditions', 'scheduled_deployments')}


def case_worker(argument):
    study, case = argument
    study = Path(study)
    manifest = read(study / 'manifest.json')
    unit = next(u for u in manifest['units'] if u['unit_id'] == case['unit_id'])
    truth, guides, observation = _load_inputs(study, unit)
    budget = unit['group_count'] * manifest['spec']['resources'][case['resource']]
    with threadpool_limits(limits=1):
        controller = StaticDeploymentSession(guides[:budget], manifest['spec']['room_m'], manifest['model'],
            study / 'results' / case['case_id'], method='recompute_all',
            provenance={'manifest_sha256': digest(study / 'manifest.json')})
        try:
            start = time.perf_counter()
            record = controller.step(ObservationFrame('nominal', 0., tuple(range(len(truth.positions))), observation))
            wall_time = time.perf_counter() - start
        finally:
            controller.close()
        audit = audit_journal(study / 'results' / case['case_id'], truth, tuple(range(len(truth.positions))))
    row = case | {k: unit[k] for k in ('physical_id', 'group_count', 'people_per_group',
        'total_people', 'requested_left_wall_clearance_m', 'actual_minimum_human_wall_clearance_m')}
    row.update(controller_status=record.metrics['status'], delivered=audit['delivered'], full_success=audit['full_success'],
        physical_audit_all_match=audit['all_match'], audit_mismatches=audit['mismatches'],
        controller_step_wall_time_s=wall_time, timing_scope='one controller step incl planning/execution/journal, excluding independent evaluator',
        worker_lifetime_peak_rss_bytes=getattr(psutil.Process().memory_info(), 'peak_wset', None))
    if audit['rows']:
        row['evaluation'] = audit['rows'][0]['evaluation']
        row['original_service'] = audit['rows'][0]['original_service']
    write_json(study / 'case_audits' / (case['case_id'] + '.json'), {'summary': row, 'physical_audit': audit})
    return row


def execute(study):
    study = Path(study).resolve()
    manifest = read(study / 'manifest.json')
    if digest(study / 'manifest.json') != read(study / 'seal.json')['manifest_sha256']:
        raise ValueError('Controlled manifest changed')
    if scientific_sources() != manifest['sources'] or environment_record() != manifest['environment']:
        raise ValueError('Frozen source or environment changed')
    exposure = read(study / 'input_overlap.json')
    if not exposure['all_new_physical_inputs_unique'] or exposure['study_manifest_sha256'] != digest(study / 'manifest.json'):
        raise ValueError('Controlled input exposure gate failed')
    cases, pending, results = iter(manifest['cases']), set(), []
    pool = ProcessPoolExecutor(max_workers=manifest['design']['workers'], initializer=initialize_worker)
    def submit():
        case = next(cases, None)
        if case is not None:
            pending.add(pool.submit(case_worker, (str(study), case)))
    try:
        for _ in range(manifest['design']['workers']):
            submit()
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                row = future.result()
                results.append(row)
                atomic_json(study / 'status.json', {'phase': 'running', 'completed_cases': len(results),
                    'scheduled_cases': len(manifest['cases']), 'new_independent_confirmation_samples': 0})
                if row['controller_status'] == 'EXPERIMENT_ERROR' or not row['physical_audit_all_match']:
                    write_json(study / 'scientific_stop.json', row)
                    raise RuntimeError('Controlled case program/safety/model/audit failure; preserve and inspect')
            for _ in completed:
                submit()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    summary = {'cases': len(results), 'scheduled_cases': len(manifest['cases']),
        'independent_generator_blocks': manifest['independent_generator_blocks'], 'scene_conditions': manifest['scene_conditions'],
        'delivered': sum(r['delivered'] for r in results), 'full_success': sum(r['full_success'] for r in results),
        'all_physical_audits_match': all(r['physical_audit_all_match'] for r in results),
        'new_independent_confirmation_samples': 0, 'scope': 'controlled one-deployment development; no dynamic crowd or long-horizon claim'}
    write_json(study / 'results.json', results)
    write_json(study / 'completion.json', summary)
    atomic_json(study / 'status.json', {'phase': 'completed', **summary})
    return summary


def run(study):
    study = Path(study).resolve()
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys;from pathlib import Path;p=Path(sys.argv[1]);sys.path.insert(0,str(p/'source_snapshot/src'));"
        "from udc.experiments.population_wall_study import execute;execute(p)")
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'execution.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=environment, stdout=log, stderr=subprocess.STDOUT)
        worker = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': child.pid, 'create_time': worker.create_time(),
            'exe': worker.exe(), 'cmdline': worker.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Frozen controlled study stopped; inspect preserved execution.log')
    return read(study / 'completion.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--study', required=True)
    parser.add_argument('--protocol')
    args = parser.parse_args()
    print(json.dumps(prepare(args.protocol, args.study) if args.action == 'prepare' else run(args.study), ensure_ascii=False), flush=True)
