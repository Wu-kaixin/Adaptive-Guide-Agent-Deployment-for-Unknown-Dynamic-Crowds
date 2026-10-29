"""Reintegrate the public stateful API on already audited development inputs.

All input/expected-artifact references and new sources freeze before execution.
This compares three update methods and all resource levels, and restarts each
session after its first actual update. No new independent input is claimed.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

import numpy as np
import psutil
from threadpoolctl import threadpool_limits

from .static_multigroup import ROOT, digest, write_json, scientific_sources, environment_record, _load_inputs, _jsonable
from .frozen_resume import atomic_json
from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.session import StaticDeploymentSession
from udc.multigroup.execution import Execution
from udc.analysis.session_audit import audit_journal


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checked(path, expected):
    if digest(path) != expected:
        raise ValueError('Frozen reintegration reference changed: ' + str(path))


def prepare(study, full=False):
    study = Path(study).resolve()
    old = ROOT / ('runs/step1_foundation_20261029/sequence_confirmation_v1' if full
                  else 'runs/step1_foundation_20261029/sequence_development_v1')
    checked(old / 'manifest.json', read(old / 'seal.json')['manifest_sha256'])
    original = read(old / 'manifest.json')
    old_audit = read(old / 'readonly_audit/offline_audit.json')
    if not old_audit['all_match']:
        raise ValueError('Original development audit did not pass')
    study.mkdir(parents=True, exist_ok=False)
    design = {'selected_units': (['K2_concave_s3460', 'K2_ellipse_s3460', 'K5_concave_s3460', 'K5_ellipse_s3460']
                                if full else ['K2_concave_s3240', 'K5_ellipse_s3240']),
        'resources': ['insufficient', 'moderate', 'sufficient'],
        'methods': ['recompute_all', 'phase_guard', 'counts_phase_guard'],
        'patterns': (original['design']['patterns'] if full else ['independent_jitter']),
        'updates': 50 if full else 3, 'restart_after_updates': [1, 17, 33, 49] if full else [1],
        'workers': 8 if full else 1,
        'selection': 'Fixed smallest seed in each K/shape stratum; reused inputs after outcomes, integration development only'}
    units, cases = [], []
    for unit in original['units']:
        if unit['unit_id'] not in design['selected_units']:
            continue
        exported = dict(unit)
        folder = study / 'inputs' / unit['unit_id']
        folder.mkdir(parents=True)
        for field in ('truth', 'observation'):
            checked(old / unit[field], unit[field + '_sha256'])
            target = folder / (field + '.npz')
            shutil.copyfile(old / unit[field], target)
            exported[field] = target.relative_to(study).as_posix()
        exported['series'] = {}
        for pattern in design['patterns']:
            series = unit['series'][pattern]
            checked(old / series['path'], series['sha256'])
            target = folder / (pattern + '.npz')
            shutil.copyfile(old / series['path'], target)
            exported['series'][pattern] = {'path': target.relative_to(study).as_posix(), 'sha256': digest(target)}
        units.append(exported)
        for pattern in design['patterns']:
            work_id = unit['unit_id'] + ('__pattern_' + pattern if original['design'].get('split_pattern_workers') else '')
            for resource in design['resources']:
                nominal = old / 'results' / (work_id + '_nominal_' + resource)
                nominal_receipt = read(nominal / 'receipt.json')
                for name, checksum in nominal_receipt.items():
                    checked(nominal / name, checksum)
                for method in design['methods']:
                    episode = old / 'results' / f'{work_id}_{resource}_{pattern}_{method}'
                    completion = read(episode / 'completion.json')
                    cases.append({'case_id': unit['unit_id'] + '_' + resource + '_' + pattern + '_' + method,
                        'unit_id': unit['unit_id'], 'resource': resource, 'method': method, 'pattern': pattern,
                        'old_nominal': str(nominal), 'old_episode': str(episode),
                        'old_nominal_receipt_sha256': digest(nominal / 'receipt.json'),
                        'old_completion_sha256': digest(episode / 'completion.json'),
                        'old_config_sha256': completion['config_sha256']})
    expected_cases = len(design['selected_units']) * len(design['patterns']) * 9
    if len(units) != len(design['selected_units']) or len(cases) != expected_cases:
        raise ValueError('Incomplete prespecified integration matrix')
    sources = scientific_sources()
    for name in sources:
        path = study / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    manifest = {'evidence': 'old_input_api_reintegration_development_only', 'new_independent_samples': 0,
        'spec': original['spec'], 'design': design, 'units': units, 'cases': cases,
        'source_manifest_sha256': digest(old / 'manifest.json'), 'source_audit_sha256': digest(old / 'readonly_audit/offline_audit.json'),
        'sources': sources, 'environment': environment_record(),
        'model': original['spec']['model'] | original['design']['model'],
        'expected_actual_events': len(cases) * (design['updates'] + 1)}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    return {'cases': len(cases), 'events': manifest['expected_actual_events'], 'new_independent_samples': 0}


def expected_rows(case, count):
    nominal, episode = Path(case['old_nominal']), Path(case['old_episode'])
    checked(nominal / 'receipt.json', case['old_nominal_receipt_sha256'])
    for name, sha in read(nominal / 'receipt.json').items():
        checked(nominal / name, sha)
    checked(episode / 'completion.json', case['old_completion_sha256'])
    checked(episode / 'config.json', case['old_config_sha256'])
    rows = []
    with np.load(nominal / 'trajectory.npz', allow_pickle=False) as data:
        state = read(nominal / 'state.json')
        rows.append((Execution(data['positions'].copy(), data['velocities'].copy(), data['assignment'].copy(),
            state['execution_status'], (), state['evidence']), data['targets'].copy(), data['target_groups'].copy(), None))
    completion = read(episode / 'completion.json')
    for relative, sha in sorted(completion['chunk_receipt_sha256'].items()):
        receipt_path = episode / relative
        checked(receipt_path, sha)
        chunk = receipt_path.parent
        for name, checksum in read(receipt_path).items():
            checked(chunk / name, checksum)
        states = read(chunk / 'state.json')
        with np.load(chunk / 'trajectory.npz', allow_pickle=False) as data:
            for index, state in enumerate(states):
                if len(rows) > count:
                    return rows
                a, b = data['position_offsets'][index:index + 2]
                c, d = data['velocity_offsets'][index:index + 2]
                n = int(data['target_counts'][index])
                rows.append((Execution(data['positions'][a:b].copy(), data['velocities'][c:d].copy(), data['assignment'][index].copy(),
                    state['execution_status'], (), state['evidence']), data['targets'][index, :n].copy(),
                    data['target_groups'][index, :n].copy(), state['planning']))
        if len(rows) > count:
            return rows
    raise ValueError('Not enough frozen expected update frames')


def execute_case(argument):
    study, case = argument
    study = Path(study)
    manifest = read(study / 'manifest.json')
    units = {unit['unit_id']: unit for unit in manifest['units']}
    compared = 0
    unit = units[case['unit_id']]
    truth, guides, nominal = _load_inputs(study, unit)
    budget = manifest['spec']['resources'][case['resource']] * unit['group_count']
    expected = expected_rows(case, manifest['design']['updates'])
    checked(study / unit['series'][case['pattern']]['path'], unit['series'][case['pattern']]['sha256'])
    with np.load(study / unit['series'][case['pattern']]['path'], allow_pickle=False) as data:
        positions = data['positions'].copy()
    folder = study / 'results' / case['case_id']
    controller = StaticDeploymentSession(guides[:budget], manifest['spec']['room_m'], manifest['model'], folder,
        method=case['method'], provenance={'manifest_sha256': digest(study / 'manifest.json')})
    mismatches = []
    try:
        with threadpool_limits(limits=1):
            for step, (old, targets, labels, old_planning) in enumerate(expected):
                measurement = nominal if step == 0 else BoundedMeasurement(Observation(positions[step],
                    nominal.observation.radii, nominal.observation.demand), unit['error_bound_m'], 'frozen_bounded_reobservation')
                frame = ObservationFrame('nominal' if step == 0 else f'update_{step}', controller.next_observation_time_s,
                    tuple(range(len(nominal.observation.positions))), measurement)
                result = controller.step(frame)
                comparisons = {name: np.array_equal(getattr(result.execution, name), getattr(old, name))
                               for name in ('positions', 'velocities', 'assignment')}
                comparisons.update(targets=np.array_equal(result.targets, targets), target_groups=np.array_equal(result.target_groups, labels))
                if old_planning is not None:
                    comparisons['planning'] = json.loads(json.dumps(result.planning, default=_jsonable)) == old_planning
                if not all(comparisons.values()) or not result.metrics['deployment_completed']:
                    mismatches.append({'step': step, 'comparisons': comparisons, 'status': result.metrics['status']})
                    break
                compared += 1
                if step in manifest['design']['restart_after_updates']:
                    actual, time_s = controller.positions, controller.next_observation_time_s
                    controller.close()
                    controller = StaticDeploymentSession.restore(folder, actual, time_s)
            audit = audit_journal(folder, truth, tuple(range(len(truth.positions))))
    finally:
        controller.close()
    row = {'case_id': case['case_id'], 'mismatches': mismatches, 'physical_audit_all_match': audit['all_match'],
           'events': audit['events'], 'delivered': audit['delivered'], 'full_success': audit['full_success']}
    row['compared_events'] = compared
    write_json(study / 'case_audits' / (case['case_id'] + '.json'), {'comparison': row, 'physical_audit': audit})
    return row


def execute(study):
    study = Path(study).resolve()
    checked(study / 'manifest.json', read(study / 'seal.json')['manifest_sha256'])
    manifest = read(study / 'manifest.json')
    if scientific_sources() != manifest['sources'] or environment_record() != manifest['environment']:
        raise ValueError('Frozen integration source or environment changed')
    results, total = [], 0
    pool = ProcessPoolExecutor(max_workers=manifest['design']['workers'])
    cases = iter(manifest['cases'])
    pending = set()
    for _ in range(manifest['design']['workers']):
        case = next(cases, None)
        if case is not None:
            pending.add(pool.submit(execute_case, (str(study), case)))
    try:
        while pending:
            completed, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                row = future.result()
                results.append(row)
                total += row['compared_events']
                atomic_json(study / 'status.json', {'phase': 'running', 'completed_cases': len(results),
                    'scheduled_cases': len(manifest['cases']), 'compared_events': total, 'new_independent_samples': 0})
                if row['mismatches'] or not row['physical_audit_all_match']:
                    write_json(study / 'scientific_stop.json', row)
                    raise RuntimeError('API reintegration mismatch; preserve result and inspect before any new version')
            for _ in completed:
                case = next(cases, None)
                if case is not None:
                    pending.add(pool.submit(execute_case, (str(study), case)))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    if total != manifest['expected_actual_events']:
        raise ValueError('Integration event denominator mismatch')
    result = {'all_match': True, 'cases': len(results), 'actual_events': total,
        'new_independent_samples': 0, 'results': results,
        'scope': str(manifest['design']['updates']) + ' updates on selected reused inputs, all declared resource/method/pattern cells; integration development only'}
    write_json(study / 'completion.json', result)
    atomic_json(study / 'status.json', {'phase': 'completed', 'completed_cases': len(results), 'compared_events': total})
    return result


def run(study, full=False):
    study = Path(study).resolve()
    prepare(study, full=full)
    process = psutil.Process()
    write_json(study / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline()})
    code = ("import sys;from pathlib import Path;study=Path(sys.argv[1]);sys.path.insert(0,str(study/'source_snapshot/src'));"
            "from udc.experiments.session_integration import execute;execute(study)")
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    with (study / 'execution.log').open('x', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-c', code, str(study)], env=environment, stdout=log, stderr=subprocess.STDOUT)
        child_process = psutil.Process(child.pid)
        write_json(study / 'worker_process.json', {'pid': child.pid, 'create_time': child_process.create_time(),
            'exe': child_process.exe(), 'cmdline': child_process.cmdline()})
        child.wait()
        if child.returncode:
            write_json(study / 'orchestration_error.json', {'exit_code': child.returncode, 'scientific_retry': False})
            raise RuntimeError('Frozen integration child stopped; see preserved execution.log')
    return read(study / 'completion.json')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    parser.add_argument('--full-confirmation-reuse', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.study, full=args.full_confirmation_reuse), ensure_ascii=False), flush=True)
