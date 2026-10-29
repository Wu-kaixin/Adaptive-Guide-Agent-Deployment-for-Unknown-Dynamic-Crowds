"""Independent physical and bookkeeping audit of a static delivery journal.

Truth enters here, never through the session controller. This checks saved
motions; it does not replay or improve the original scientific decisions.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement, StaticTruth
from udc.multigroup.execution import Execution
from udc.multigroup.evaluation import evaluate, reference_groups
from udc.multigroup.geometry import construct_groups
from udc.experiments.static_mechanism import continuous_summary


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def audit_journal(root, truth, truth_person_ids):
    root = Path(root)
    header = read(root / 'session.json')
    schema = header.get('journal_schema_version')
    if schema not in (None, 1, 2, 3):
        raise ValueError('Unsupported session journal schema')
    header_bound = schema in (2, 3)
    if header_bound and read(root / 'session_receipt.json') != {'session.json': hashlib.sha256((root / 'session.json').read_bytes()).hexdigest()}:
        raise ValueError('Session header changed')
    model, room = header['model'], header['room_m']
    if len(set(truth_person_ids)) != len(truth.positions) or len(truth_person_ids) != len(truth.positions):
        raise ValueError('Evaluator person identity map is incomplete')
    canonical = tuple(sorted(truth_person_ids))
    order = np.array([tuple(truth_person_ids).index(i) for i in canonical])
    truth = StaticTruth(truth.positions[order], truth.radii[order], truth.demand[order], truth.group_ids[order])
    reference = reference_groups(truth, room, model)
    previous = np.asarray(header['initial_guides'], float)
    end_time = 0.
    previous_receipt = None
    mismatches, rows = [], []
    for index, folder in enumerate(sorted(root.glob('event_*'))):
        if folder.name != f'event_{index:06d}':
            raise ValueError('Journal contains an event gap')
        receipt = read(folder / 'receipt.json')
        if schema == 3 and not {'frame.json', 'observation.npz', 'intent.json', 'checkpoint.json',
                                'metrics.json', 'planning.json', 'state.json', 'trajectory.npz'} <= set(receipt):
            raise ValueError('Committed event omits required pre-motion or checkpoint artifacts')
        for filename, expected in receipt.items():
            path = (folder / filename).resolve()
            if not path.is_relative_to(folder.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError('Journal artifact changed: ' + filename)
        frame, saved, state, planning = (read(folder / f'{n}.json') for n in ('frame', 'metrics', 'state', 'planning'))
        if schema == 3:
            intent, checkpoint = read(folder / 'intent.json'), read(folder / 'checkpoint.json')
            header_sha = hashlib.sha256((root / 'session.json').read_bytes()).hexdigest()
            if (intent['phase'] != 'before_planning_and_motion' or intent['event_index'] != index
                    or intent['frame_id'] != frame['frame_id'] or intent['session_header_sha256'] != header_sha
                    or intent['previous_event_receipt_sha256'] != previous_receipt
                    or checkpoint['previous_event_receipt_sha256'] != previous_receipt
                    or checkpoint['session_header_sha256'] != header_sha or checkpoint['next_event_index'] != index + 1):
                mismatches.append(folder.name + ':EVENT_COMMIT_CHAIN')
        previous_receipt = hashlib.sha256((folder / 'receipt.json').read_bytes()).hexdigest()
        if set(frame['person_ids']) != set(canonical):
            # The controller rejected a stage/identity change. There is no
            # matching evaluator world for that frame; preserve an explicit
            # unavailable evaluation rather than invent physical success.
            with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                positions = data['positions'].copy()
                intervals = len(data['velocities'])
            if (saved['status'] not in ('STATIC_IDENTITY_CHANGED', 'SESSION_HALTED', 'STEP_SCOPE_VIOLATION')
                    or saved['deployment_completed'] or intervals or not np.array_equal(positions[0], previous)
                    or not np.array_equal(positions[-1], previous) or saved['end_time_s'] != end_time):
                mismatches.append(folder.name + ':IDENTITY_REJECTION_ACCOUNTING')
            rows.append({'event': folder.name, 'controller_status': saved['status'],
                'evaluation': {'deployment_completed': False, 'full_task_success': False, 'motion': None},
                'evaluation_unavailable': 'frame identities differ from frozen evaluator world',
                'original_service': None, 'accounted_full_success': False})
            continue
        frame_order = np.array([frame['person_ids'].index(i) for i in canonical])
        with np.load(folder / 'observation.npz', allow_pickle=False) as data:
            obs = Observation(data['positions'][frame_order], data['radii'][frame_order], data['demand'][frame_order])
        measurement = BoundedMeasurement(obs, frame['position_error_bound_m'], frame['measurement_model'])
        groups, _ = construct_groups(measurement, room, model)
        with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
            execution = Execution(data['positions'].copy(), data['velocities'].copy(), data['assignment'].copy(),
                                  state['execution_status'], (), state['evidence'])
            targets, labels = data['targets'].copy(), data['target_groups'].copy()
        if not np.array_equal(execution.positions[0], previous):
            mismatches.append(folder.name + ':ACTUAL_STATE_RESET')
        if not np.array_equal(execution.positions[-1], np.asarray(state['actual_positions'])):
            mismatches.append(folder.name + ':SAVED_ACTUAL_STATE')
        duration = len(execution.velocities) * model['dt_s']
        expected_end = frame['time_s'] + duration if len(execution.velocities) else end_time
        if saved['end_time_s'] != expected_end or saved['simulation_time_s'] != duration:
            mismatches.append(folder.name + ':EXECUTION_TIME')
        fresh = evaluate(execution, targets, labels, groups, measurement, truth, room, model)
        if fresh['motion']['interval_safety_ok'] is False:
            mismatches.append(folder.name + ':PHYSICAL_SAFETY')
        if len(execution.velocities) and (not fresh['motion']['kinematics_ok'] or not fresh['motion']['speed_limit_ok']):
            mismatches.append(folder.name + ':KINEMATICS_OR_SPEED')
        if saved['status'] != 'EXPERIMENT_ERROR' and saved['deployment_completed'] != fresh['deployment_completed']:
            mismatches.append(folder.name + ':DELIVERY_ACCOUNTING')
        service = continuous_summary(reference, truth, execution.positions[-1][execution.assignment >= 0], model)
        if saved.get('accepted_service_certificate') and saved['deployment_completed']:
            baseline = continuous_summary(reference, truth, np.asarray(planning['baseline_targets']), model)
            losses = [a['uncovered'] - b['uncovered'] for a, b in zip(service['groups'], baseline['groups'])]
            if max(losses) > model.get('true_service_delta', .002):
                mismatches.append(folder.name + ':ORIGINAL_SERVICE_BOUND')
        rows.append({'event': folder.name, 'controller_status': saved['status'],
            'evaluation': fresh, 'original_service': service,
            'accounted_full_success': bool(saved['status'] != 'EXPERIMENT_ERROR' and saved['deployment_completed'] and fresh['full_task_success'])})
        previous, end_time = execution.positions[-1].copy(), saved['end_time_s']
    return {'events': len(rows), 'mismatches': mismatches, 'all_match': not mismatches,
        'statuses': dict(Counter(r['controller_status'] for r in rows)),
        'delivered': sum(r['controller_status'] == 'DELIVERED_OBSERVATION_PLAN' and r['evaluation']['deployment_completed'] for r in rows),
        'physically_delivered_including_program_error': sum(r['evaluation']['deployment_completed'] for r in rows),
        'full_success': sum(r['accounted_full_success'] for r in rows), 'rows': rows,
        'header_hash_bound': header_bound,
        'pre_motion_intent_bound': schema == 3,
        'decision_reconstruction_performed': False, 'new_independent_samples': 0}
