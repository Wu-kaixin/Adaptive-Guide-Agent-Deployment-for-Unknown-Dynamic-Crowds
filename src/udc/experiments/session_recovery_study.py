"""Frozen handwritten recovery development: process death after event commit.

Uses simulated backend feedback, not hardware. Compares to uninterrupted
delivery, audits the saved motions, and keeps refusal witnesses separately.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
from threadpoolctl import threadpool_limits
import yaml

from .static_multigroup import ROOT, scientific_sources, environment_record, write_json, digest
from udc.interfaces import Observation
from udc.multigroup.observation import StaticTruth, BoundedMeasurement
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.session import StaticDeploymentSession
from udc.analysis.session_audit import audit_journal


def observation_frame(name, time_s, update=False):
    points = np.array([[10., 10.], [22., 10.]])
    if update:
        points += np.array([[.001, 0.], [0., -.001]])
    order = [1, 0] if update else [0, 1]
    obs = Observation(points[order], np.array([.2, .2])[order], np.array([1., 2.])[order])
    return ObservationFrame(name, time_s, tuple(np.array([42, 7])[order]), BoundedMeasurement(obs, .04, 'handwritten_bounded'))


def run(study):
    study = Path(study).resolve()
    study.mkdir(parents=True, exist_ok=False)
    model = yaml.safe_load((ROOT / 'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))['model']
    sources = scientific_sources()
    for name in sources:
        path = study / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    manifest = {'evidence': 'handwritten_recovery_development_not_independent_confirmation',
        'new_independent_samples': 0, 'sources': sources, 'environment': environment_record(), 'model': model,
        'interruption': 'os._exit_17_after_committed_event_and_simulated_feedback_saved',
        'feedback_scope': 'same_logical_simulation_clock_and_actual_positions_no_unrecorded_motion',
        'comparison': 'saved_next_targets_assignments_positions_velocities_metrics_bitwise_equal',
        'fixture': {'person_ids': [42, 7], 'positions': [[10., 10.], [22., 10.]],
                    'radii': [.2, .2], 'demand': [1., 2.], 'guides': [[2., 2.], [24., 2.]]}}
    write_json(study / 'manifest.json', manifest)
    write_json(study / 'seal.json', {'manifest_sha256': digest(study / 'manifest.json')})
    provenance = {'manifest_sha256': digest(study / 'manifest.json')}
    with threadpool_limits(limits=1), StaticDeploymentSession(manifest['fixture']['guides'], [40., 30.], model,
            study / 'uninterrupted', provenance=provenance) as controller:
        controller.step(observation_frame('one', 0.))
        expected = controller.step(observation_frame('two', controller.next_observation_time_s, True))
    code = '''import json,os,sys,numpy as np
from pathlib import Path
study=Path(sys.argv[1]);sys.path.insert(0,str(study/'source_snapshot/src'))
from udc.experiments.session_recovery_study import observation_frame
from udc.experiments.static_multigroup import digest
from udc.multigroup.session import StaticDeploymentSession
manifest=json.loads((study/'manifest.json').read_text(encoding='utf-8'))
mode=sys.argv[2]
with (study/(mode+'_started.json')).open('x',encoding='utf-8') as stream:json.dump({'pid':os.getpid()},stream)
controller=StaticDeploymentSession(manifest['fixture']['guides'],[40.,30.],manifest['model'],study/mode,
    provenance={'manifest_sha256':digest(study/'manifest.json')})
if mode=='in_flight':
 import udc.multigroup.session as implementation
 implementation.execute_hybrid=lambda *args:os._exit(18)
controller.step(observation_frame('one',0.))
np.savez_compressed(study/'backend_feedback.npz',positions=controller.positions,time_s=controller.next_observation_time_s)
os._exit(17)
'''
    environment = os.environ | {'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    child = subprocess.run([sys.executable, '-c', code, str(study), 'interrupted'], env=environment,
                           capture_output=True, text=True, timeout=60)
    write_json(study / 'child_exit.json', {'exit_code': child.returncode,
        'expected_exit_code': 17, 'stdout': child.stdout, 'stderr': child.stderr})
    if child.returncode != 17:
        raise RuntimeError('Controlled process exit did not reach the declared committed event')
    with np.load(study / 'backend_feedback.npz', allow_pickle=False) as feedback:
        positions, time_s = feedback['positions'].copy(), float(feedback['time_s'])
    old_receipt_sha = digest(study / 'interrupted/event_000000/receipt.json')
    with threadpool_limits(limits=1), StaticDeploymentSession.restore(study / 'interrupted', positions, time_s) as controller:
        actual = controller.step(observation_frame('two', time_s, True))
    comparisons = {name: np.array_equal(getattr(expected.execution, name), getattr(actual.execution, name))
                   for name in ('positions', 'velocities', 'assignment')}
    comparisons.update(targets=np.array_equal(expected.targets, actual.targets),
        target_groups=np.array_equal(expected.target_groups, actual.target_groups), metrics=expected.metrics == actual.metrics,
        original_event_preserved=old_receipt_sha == digest(study / 'interrupted/event_000000/receipt.json'))
    if not all(comparisons.values()):
        write_json(study / 'comparison_failure.json', comparisons)
        raise RuntimeError('Restored delivery differs from uninterrupted delivery')
    fixture = manifest['fixture']
    truth = StaticTruth(fixture['positions'], fixture['radii'], fixture['demand'], [0, 1])
    with threadpool_limits(limits=1):
        audits = {name: audit_journal(study / name, truth, fixture['person_ids'])
                  for name in ('uninterrupted', 'interrupted')}
    for name, result in audits.items():
        write_json(study / (name + '_physical_audit.json'), result)
    if not all(result['all_match'] for result in audits.values()):
        raise RuntimeError('Recovery physical audit mismatch')
    with np.load(study / 'interrupted/event_000001/trajectory.npz', allow_pickle=False) as saved:
        terminal = saved['positions'][-1].copy()
    checkpoint = json.loads((study / 'interrupted/event_000001/checkpoint.json').read_text(encoding='utf-8'))
    rejections = []
    child_in_flight = subprocess.run([sys.executable, '-c', code, str(study), 'in_flight'],
        env=environment, capture_output=True, text=True, timeout=60)
    write_json(study / 'in_flight_exit.json', {'exit_code': child_in_flight.returncode,
        'expected_exit_code': 18, 'stdout': child_in_flight.stdout, 'stderr': child_in_flight.stderr})
    if child_in_flight.returncode != 18:
        raise RuntimeError('In-flight interruption did not reach the intended executor phase')
    try:
        controller = StaticDeploymentSession.restore(study / 'in_flight', fixture['guides'], 0.)
    except ValueError as error:
        rejections.append({'case': 'process_death_during_execution_with_old_matching_feedback',
            'rejected': True, 'reason': str(error),
            'intent_sha256': digest(study / 'in_flight/event_000000/intent.json')})
    else:
        controller.close()
        raise RuntimeError('In-flight process death was inferred to have no unknown motion')
    for name, feedback, time in [('pose_mismatch', terminal + .01, checkpoint['time_s']),
                                ('unknown_elapsed_motion', terminal, checkpoint['time_s'] + 1.)]:
        try:
            controller = StaticDeploymentSession.restore(study / 'interrupted', feedback, time)
        except ValueError as error:
            rejections.append({'case': name, 'rejected': True, 'reason': str(error)})
        else:
            controller.close()
            raise RuntimeError('Invalid recovery feedback was accepted')
    copied = study / 'partial_event_witness'
    shutil.copytree(study / 'interrupted', copied)
    partial = copied / 'event_000002'
    partial.mkdir()
    (partial / 'checkpoint.json').write_text('uncommitted state', encoding='utf-8')
    try:
        controller = StaticDeploymentSession.restore(copied, terminal, checkpoint['time_s'])
    except ValueError as error:
        rejections.append({'case': 'partial_event', 'rejected': True, 'reason': str(error),
                           'partial_file_sha256': digest(partial / 'checkpoint.json')})
    else:
        controller.close()
        raise RuntimeError('Uncommitted event was silently resumed')
    write_json(study / 'rejection_receipts.json', rejections)
    result = {'comparisons': comparisons, 'all_match': True, 'new_independent_samples': 0,
        'physical_audits_all_match': True, 'physical_events': sum(a['events'] for a in audits.values()),
        'accounted_deliveries': sum(a['delivered'] for a in audits.values()),
        'full_task_success': sum(a['full_success'] for a in audits.values()), 'expected_refusals': len(rejections),
        'original_event_receipt_sha256': old_receipt_sha,
        'limits': 'Completed-event logical simulation restart only; not arbitrary power loss or live actuator recovery'}
    write_json(study / 'completion.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.study), ensure_ascii=False), flush=True)
