"""Recompute execution acceptance from the saved snapshot and trajectory."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from udc.execution.acceptance import evaluate_execution
from udc.execution.fixed_paths import _acceptance_context, _domain, _group_context, _limits, _write_json
from udc.safety import is_safe, path_clearances


def reevaluate_execution(directory):
    """Review one execution directory. Live module constants are not the standard."""
    directory = Path(directory)
    original = (directory / 'execution.json').read_bytes()
    record = json.loads(original)
    config = _saved_config(directory)
    checks = {
        'effective_config': config is not None,
        'orchestration': record.get('orchestration_status') in {'OK', 'EXPERIMENT_ERROR'},
    }
    data = _trajectory(directory)
    checks['trajectory_file'] = data is not None or record.get('steps') in (0, None) and record.get('motion_executed') is False
    if record.get('status') in {'CONFIG_MISMATCH', 'PREFLIGHT_REJECTED', 'INSUFFICIENT_EVIDENCE'}:
        checks = {
            'refused_before_motion': (
                record.get('motion_executed') is False and record.get('steps') in (0, None)
                and record.get('deployment_success') is False and not (directory / 'trajectory.npz').is_file()
            ),
        }
        report = _report(record, checks, None, None)
        _write_json(directory / 'execution_reevaluation.json', report)
        _guard(directory, original)
        return report
    if config is None or data is None and record.get('motion_executed'):
        report = _report(record, checks, None, None)
        _write_json(directory / 'execution_reevaluation.json', report)
        _guard(directory, original)
        return report
    if data is not None and config is not None:
        checks.update(_trajectory_checks(data, record, config))
    verdict = None
    if data is not None and config is not None and checks.get('trajectory_identity') and checks.get('time_axis') and record.get('steps', 0):
        loaded = _context_from_saved(record, data, config)
        if loaded is None:
            checks['source_binding'] = False
        else:
            checks['source_binding'] = _source_matches(loaded, data, record)
            verdict = evaluate_execution(data['positions'], data['safe_velocity'], _acceptance_context(
                loaded, record.get('orchestration_status')))
            checks['criteria_agreement'] = _criteria_agree(record, verdict)
            checks['within_deadline'] = _within_deadline(data, config, record)
    if record.get('status') == 'ILLEGAL_INITIAL_STATE':
        checks['illegal_initial'] = _illegal_initial(directory, data, config)
    if record.get('status') == 'EXPERIMENT_ERROR':
        checks['prefix_reported'] = 'prefix_acceptance' in record or record.get('steps') in (0, None)
        checks['not_promoted'] = record.get('deployment_success') is False
    physical = checks.get('criteria_agreement')
    report = _report(record, checks, verdict, physical)
    _write_json(directory / 'execution_reevaluation.json', report)
    _guard(directory, original)
    return report


def _report(record, checks, verdict, physical):
    consistent = bool(checks) and all(value is True for value in checks.values())
    return {
        'schema': 'step2a_execution_reevaluation_v1',
        'source_status': record.get('status'),
        'checks': checks,
        'completeness': bool(checks.get('effective_config') and checks.get('trajectory_file')),
        'physical_acceptance': None if verdict is None else verdict['deployment_success'],
        'process_status': record.get('status'),
        'consistent': bool(consistent),
        'displacement_matches_velocity': checks.get('displacement'),
        'replanned': False,
        'recovery_called': False,
        'used_live_defaults': False,
    }


def _trajectory_checks(data, record, config):
    policy = config['execution']
    positions = data['positions']
    velocities = data['safe_velocity']
    nominal = data['nominal_velocity']
    time = data['time']
    identity = (
        list(map(int, data['guide_ids'])) == list(map(int, record.get('guide_ids', [])))
        and positions.ndim == 3 and positions.shape[1] == len(data['guide_ids'])
        and velocities.shape[0] == max(0, len(positions) - 1)
        and nominal.shape[0] == velocities.shape[0]
        and len(time) == len(positions)
        and np.isfinite(positions).all() and (len(velocities) == 0 or np.isfinite(velocities).all())
    )
    dt = float(policy['dt_s'])
    atol = float(policy['displacement_atol_m'])
    time_ok = bool(len(time) and abs(float(time[0])) <= atol and (
        len(time) == 1 or np.max(np.abs(np.diff(time) - dt)) <= atol
    ))
    if len(velocities):
        delta = positions[1:] - (positions[:-1] + dt * velocities)
        displacement = bool(np.max(np.abs(delta)) <= atol)
    else:
        displacement = len(positions) == 1
    return {'trajectory_identity': bool(identity), 'time_axis': time_ok, 'displacement': displacement}


def _within_deadline(data, config, record):
    policy = config['execution']
    steps = len(data['safe_velocity'])
    if steps > int(policy['max_steps']):
        return False
    if len(data['time']) and float(data['time'][-1]) > float(policy['deadline_s']) + float(policy['displacement_atol_m']):
        return False
    if record.get('status') == 'SUCCESS' and steps > int(policy['max_steps']):
        return False
    return True


def _criteria_agree(record, verdict):
    saved = record.get('group_results') or record.get('prefix_acceptance', {}).get('group_results')
    if not isinstance(saved, dict) or not saved:
        return False
    for group_id, item in saved.items():
        fresh = verdict['group_results'].get(group_id)
        if fresh is None or bool(item.get('passed')) != bool(fresh['passed']) or list(item.get('reasons', [])) != list(fresh['reasons']):
            return False
    saved_system = record.get('system_acceptance') or record.get('prefix_acceptance', {}).get('system_acceptance')
    if not isinstance(saved_system, dict):
        return False
    return bool(saved_system.get('passed')) == bool(verdict['system_acceptance']['passed']) and list(
        saved_system.get('reasons', [])) == list(verdict['system_acceptance']['reasons'])


def _illegal_initial(directory, data, config):
    if data is None or len(data['positions']) != 1 or len(data['safe_velocity']) != 0:
        return False
    record = json.loads((directory / 'execution.json').read_text(encoding='utf-8'))
    loaded = _context_from_saved(record, data, config)
    if loaded is None:
        return False
    clearances = path_clearances(data['positions'][0], data['positions'][0], loaded['people'], loaded['room'])
    return (not is_safe(clearances, loaded['limits']) and record.get('motion_executed') is False
            and record.get('executed_path_length_m') is None and record.get('deployment_success') is False)


def _source_matches(loaded, data, record):
    request = loaded['request']
    return (
        list(map(int, data['person_ids'])) == list(map(int, request['person_ids']))
        and np.max(np.abs(data['person_positions'] - loaded['people'])) <= loaded['policy']['displacement_atol_m']
        and list(map(int, data['guide_ids'])) == list(map(int, record['guide_ids']))
    )


def _context_from_saved(record, data, config):
    preflight = Path(record.get('preflight_source', ''))
    if not preflight.is_dir() or not (preflight / 'request.json').is_file():
        return None
    request = json.loads((preflight / 'request.json').read_text(encoding='utf-8'))
    labels = json.loads((preflight / 'target_labels.json').read_text(encoding='utf-8'))
    paths = json.loads((preflight / 'paths.json').read_text(encoding='utf-8'))
    if len(paths['pairs']) != len(paths['paths']):
        return None
    ordered = [(pair, np.asarray(path, dtype=float)) for pair, path in zip(paths['pairs'], paths['paths'])]
    wiring = config['wiring']
    policy = config['execution']
    limits = _limits(np.asarray(request['radii'], dtype=float), wiring, policy)
    return {
        'limits': limits,
        'people': np.asarray(request['positions'], dtype=float),
        'room': np.asarray(request['room_m'], dtype=float),
        'domain': _domain(preflight, request, wiring),
        'groups': _group_context(preflight, request, ordered, data['targets'], labels),
        'ordered': ordered,
        'request': request,
        'labels': labels,
        'targets': data['targets'],
        'policy': policy,
        'wiring': wiring,
    }


def _saved_config(directory):
    path = directory / 'effective_config.json'
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or 'execution' not in document or 'wiring' not in document:
        return None
    return document


def _trajectory(directory):
    path = directory / 'trajectory.npz'
    if not path.is_file():
        return None
    return np.load(path, allow_pickle=False)


def _guard(directory, original):
    if (directory / 'execution.json').read_bytes() != original:
        raise RuntimeError('reevaluation rewrote execution.json')
