"""Execute one accepted preflight on a shared clock without recovery."""
from __future__ import annotations

import hashlib
import json
import traceback
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from udc.controller import ABCGController
from udc.controller.progress import ordinary_stall
from udc.execution.acceptance import evaluate_execution
from udc.navigation import navigation_domain, segment_in_free
from udc.execution.config import (
    ConfigMismatch, assert_same_config, load_execution_policy, working_tree_provenance,
)
from udc.preflight.pipeline import _artifact_hashes
from udc.preflight.review import reevaluate_preflight
from udc.safety import Safety, is_safe, path_clearances

# Loaded from configs/step2a/fixed_paths_no_recovery_v1.yaml. A mutated copy must not run.
EXECUTION_POLICY = load_execution_policy()


def execute_preflight(preflight, output):
    """Run in a new directory. The preflight directory is only read."""
    preflight = Path(preflight)
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'Use an empty execution directory: {output}')
    output.mkdir(parents=True, exist_ok=True)
    before = {path: path.read_bytes() for path in preflight.rglob('*') if path.is_file()}
    record = _blank()
    try:
        assert_same_config(EXECUTION_POLICY, load_execution_policy(), 'execution policy')
        loaded = _load(preflight)
        record['effective_config'] = {
            'execution': dict(EXECUTION_POLICY),
            'wiring': loaded['wiring'],
            'provenance': working_tree_provenance(),
        }
        _write_json(output / 'effective_config.json', record['effective_config'])
        record['preflight_source'] = str(preflight.resolve())
        record['preflight_artifact_sha256'] = loaded['hashes']
        record['allocation_feasible'] = loaded['allocation_feasible']
        record['preflight_accepted'] = True
        _simulate(loaded, output, record)
    except ConfigMismatch as mismatch:
        record['status'] = 'CONFIG_MISMATCH'
        record['failure_reason'] = str(mismatch)
        record['deployment_success'] = False
        record['orchestration_status'] = 'OK'
        record['motion_executed'] = False
        record['executed_path_length_m'] = None
        record['steps'] = 0
    except _Rejected as rejected:
        record['status'] = rejected.status
        record['failure_reason'] = rejected.reason
        record['deployment_success'] = False
        record['orchestration_status'] = 'OK'
        record['motion_executed'] = False
        record['executed_path_length_m'] = None
        record['steps'] = 0
    except Exception:
        record['status'] = 'EXPERIMENT_ERROR'
        record['orchestration_status'] = 'EXPERIMENT_ERROR'
        record['deployment_success'] = False
        record['failure_reason'] = traceback.format_exc().strip().splitlines()[-1]
        try:
            (output / 'execution_error.txt').write_text(traceback.format_exc(), encoding='utf-8')
            _persist_partial(output, record)
        except Exception as save_error:
            record['save_error'] = f'{type(save_error).__name__}: {save_error}'
    record.pop('_partial', None)
    record['source_sha256'] = working_tree_provenance()['working_tree_source_sha256']
    record['git_head'] = working_tree_provenance()['git_head']
    record['git_dirty'] = working_tree_provenance()['git_dirty']
    record['policy_sha256'] = _policy_hash()
    _write_json(output / 'execution.json', record)
    after = {path: path.read_bytes() for path in preflight.rglob('*') if path.is_file()}
    if before != after:
        raise RuntimeError('execution wrote into the preflight directory')
    return record


def candidate_motion_allowed(positions, velocity, people, room, dt, limits, domain):
    """Re-check one joint candidate. Rejection does not mean an executed violation."""
    end = np.asarray(positions, dtype=float) + float(dt) * np.asarray(velocity, dtype=float)
    clearances = path_clearances(positions, end, people, room)
    if not is_safe(clearances, limits):
        return False, 'interval_safety', {'clearances': clearances}
    for index in range(len(end)):
        if not segment_in_free(positions[index], end[index], domain):
            return False, 'outside_shared_domain', {'guide_row': index}
    return True, None, None


def _simulate(loaded, output, record):
    policy = loaded['policy']
    positions = np.array(loaded['guide_positions'], dtype=float, copy=True)
    people = loaded['people']
    limits = loaded['limits']
    initial = path_clearances(positions, positions, people, loaded['room'])
    if not is_safe(initial, limits):
        _save_trajectory(output, loaded, positions[None, :, :], np.zeros((0, len(positions), 2)),
                         np.zeros((0, len(positions), 2)), [])
        record['executed_path_length_m'] = None
        record['motion_executed'] = False
        record['status'] = 'ILLEGAL_INITIAL_STATE'
        record['failure_reason'] = 'initial guide state violates safety distances; fallback was not used'
        record['deployment_success'] = False
        record['orchestration_status'] = 'OK'
        record['group_results'] = _unevaluated(loaded)['group_results']
        record['system_acceptance'] = _unevaluated(loaded)['system_acceptance']
        record['steps'] = 0
        record['guide_ids'] = list(loaded['guide_ids'])
        return
    controller = _tracker(loaded, positions)
    history = [positions.copy()]
    nominals = []
    actuals = []
    waypoints = [dict(controller.waypoint)]
    record['_partial'] = {'loaded': loaded, 'history': history, 'nominals': nominals,
                          'actuals': actuals, 'waypoints': waypoints}
    progress = [_mean_remaining(controller, positions)]
    dt = policy['dt_s']
    status = 'TIMEOUT'
    reason = 'deadline reached before the common hold window; recovery was not called'
    for _step in range(policy['max_steps']):
        saved = dict(controller.waypoint)
        nominal, result = controller.step(_People(people), positions, dt)
        allowed, why, evidence = candidate_motion_allowed(
            positions, result.velocity, people, loaded['room'], dt, limits, loaded['domain'],
        )
        if not allowed:
            controller.waypoint = saved
            _write_json(output / 'candidate_rejection.json', {
                'reason': why, 'evidence': _jsonable(evidence),
                'executed': False, 'recovery_called': False,
            })
            status = 'CANDIDATE_REJECTED'
            reason = f'filtered candidate failed {why}; the interval was not executed'
            break
        positions = positions + dt * result.velocity
        history.append(positions.copy())
        nominals.append(np.array(nominal, copy=True))
        actuals.append(np.array(result.velocity, copy=True))
        waypoints.append(dict(controller.waypoint))
        progress.append(_mean_remaining(controller, positions))
        verdict = _verdict(loaded, history, actuals, 'OK')
        if verdict['deployment_success']:
            status = 'SUCCESS'
            reason = 'both groups and the system passed the same terminal window'
            record.update(verdict)
            break
        errors = _target_errors(loaded, positions)
        mean_speed = float(np.linalg.norm(result.velocity, axis=1).mean()) if len(result.velocity) else 0.0
        if ordinary_stall(progress, policy['stall_window'], policy['position_tolerance_m'],
                          float(np.max(errors, initial=0.0)), mean_speed, policy['speed_tolerance_m_s']):
            status = 'STALL'
            reason = 'progress stalled; recovery_policy=disabled and recover was not called'
            break
    trajectory = np.asarray(history, dtype=float)
    velocity = np.asarray(actuals, dtype=float) if actuals else np.zeros((0, len(positions), 2))
    nominal_velocity = np.asarray(nominals, dtype=float) if nominals else np.zeros((0, len(positions), 2))
    executed_length = float(np.linalg.norm(np.diff(trajectory, axis=0), axis=2).sum()) if len(trajectory) > 1 else None
    record['motion_executed'] = bool(len(velocity))
    record['executed_path_length_m'] = executed_length
    record['status'] = status
    record['failure_reason'] = None if status == 'SUCCESS' else reason
    record['recovery_called'] = False
    record['orchestration_status'] = 'OK'
    if 'deployment_success' not in record or status != 'SUCCESS':
        verdict = _verdict(loaded, history, actuals, 'OK') if len(velocity) else _unevaluated(loaded)
        record['group_results'] = verdict['group_results']
        record['system_acceptance'] = verdict['system_acceptance']
        record['deployment_success'] = False if status != 'SUCCESS' else verdict['deployment_success']
    _save_trajectory(output, loaded, trajectory, nominal_velocity, velocity, waypoints)
    record['steps'] = int(len(velocity))
    record['guide_ids'] = list(loaded['guide_ids'])


def _load(preflight):
    record = json.loads((preflight / 'preflight.json').read_text(encoding='utf-8'))
    if record.get('status') != 'PREFLIGHT_ACCEPTED':
        raise _Rejected('PREFLIGHT_REJECTED', 'execution requires PREFLIGHT_ACCEPTED')
    hashes = record.get('artifact_sha256')
    if not isinstance(hashes, dict) or hashes != _artifact_hashes(preflight):
        raise _Rejected('PREFLIGHT_REJECTED', 'preflight artifact hash does not match the saved files')
    review = reevaluate_preflight(preflight, write=False)
    if not review['consistent']:
        raise _Rejected('PREFLIGHT_REJECTED', 'independent preflight review is not consistent')
    request = json.loads((preflight / 'request.json').read_text(encoding='utf-8'))
    labels = json.loads((preflight / 'target_labels.json').read_text(encoding='utf-8'))
    assignment = json.loads((preflight / 'assignment.json').read_text(encoding='utf-8'))
    paths = json.loads((preflight / 'paths.json').read_text(encoding='utf-8'))
    targets = np.load(preflight / 'targets.npz', allow_pickle=False)
    wiring = loaded_wiring(preflight)
    if len(paths['pairs']) != len(paths['paths']):
        raise _Rejected('PREFLIGHT_REJECTED', 'path list length does not equal assignment length')
    guide_index = {guide_id: row for row, guide_id in enumerate(request['guide_ids'])}
    ordered = []
    for pair, path in zip(paths['pairs'], paths['paths']):
        path = np.asarray(path, dtype=float)
        origin = np.asarray(request['guide_positions'][guide_index[pair['guide_id']]], dtype=float)
        target = np.asarray(targets['positions'][pair['target_row']], dtype=float)
        if (len(path) < 2 or np.linalg.norm(path[0] - origin) > wiring['path_start_atol_m']
                or np.linalg.norm(path[-1] - target) > wiring['path_end_atol_m']):
            raise _Rejected('PREFLIGHT_REJECTED', 'a saved path does not match its guide start or target')
        if labels['group_id'][pair['target_row']] != pair['group_id']:
            raise _Rejected('PREFLIGHT_REJECTED', 'a saved path target belongs to another group')
        ordered.append((pair, path))
    people = np.asarray(request['positions'], dtype=float)
    room = np.asarray(request['room_m'], dtype=float)
    radii = np.asarray(request['radii'], dtype=float)
    limits = _limits(radii, wiring, EXECUTION_POLICY)
    domain = _domain(preflight, request, wiring)
    groups = _group_context(preflight, request, ordered, targets['positions'], labels)
    return {
        'hashes': hashes,
        'allocation_feasible': True,
        'request': request,
        'people': people,
        'room': room,
        'radii': radii,
        'limits': limits,
        'domain': domain,
        'ordered': ordered,
        'groups': groups,
        'labels': labels,
        'guide_positions': np.asarray([request['guide_positions'][guide_index[pair['guide_id']]] for pair, _path in ordered], dtype=float),
        'guide_ids': [pair['guide_id'] for pair, _path in ordered],
        'guide_groups': [pair['group_id'] for pair, _path in ordered],
        'target_rows': [pair['target_row'] for pair, _path in ordered],
        'targets': np.asarray(targets['positions'], dtype=float),
        'wiring': wiring,
        'policy': dict(EXECUTION_POLICY),
    }


def _tracker(loaded, positions):
    policy = loaded['policy']
    controller = ABCGController({
        'gain': policy['gain'],
        'waypoint_tolerance': policy['waypoint_tolerance_m'],
        'position_tolerance': policy['position_tolerance_m'],
        'speed_tolerance': policy['speed_tolerance_m_s'],
        'hold_steps': policy['hold_steps'],
        'stall_window': policy['stall_window'],
        'max_gap_limit': policy['max_gap_limit_m'],
        'curve_tolerance': policy['curve_tolerance_m'],
    }, loaded['limits'], loaded['room'], 'fixed_n', len(positions))
    controller.navigation_domain = loaded['domain']
    controller.assignment = np.asarray(loaded['target_rows'], dtype=int)
    controller.paths = {index: path for index, (_pair, path) in enumerate(loaded['ordered'])}
    controller.waypoint = {index: 1 for index in range(len(positions))}
    controller.priority_mode = False
    return controller


def _verdict(loaded, history, actuals, orchestration):
    context = _acceptance_context(loaded, orchestration)
    velocities = np.asarray(actuals, dtype=float) if actuals else np.zeros((0, len(history[0]), 2))
    return evaluate_execution(np.asarray(history, dtype=float), velocities, context)


def _unevaluated(loaded):
    return {
        'group_results': {group_id: {'passed': False, 'reasons': ['not_executed']} for group_id in loaded['groups']},
        'system_acceptance': {'passed': False, 'reasons': ['not_executed']},
        'deployment_success': False,
    }


def _acceptance_context(loaded, orchestration):
    policy = loaded['policy']
    return {
        'hold_steps': policy['hold_steps'],
        'position_tolerance_m': policy['position_tolerance_m'],
        'speed_tolerance_m_s': policy['speed_tolerance_m_s'],
        'curve_tolerance_m': policy['curve_tolerance_m'],
        'max_gap_limit_m': policy['max_gap_limit_m'],
        'max_speed_m_s': policy['max_speed_m_s'],
        'dt': policy['dt_s'],
        'limits': loaded['limits'],
        'people': loaded['people'],
        'room': loaded['room'],
        'domain': loaded['domain'],
        'groups': loaded['groups'],
        'pairs': [pair for pair, _path in loaded['ordered']],
        'guide_ids': loaded['request']['guide_ids'],
        'target_groups': loaded['labels']['group_id'],
        'orchestration_status': orchestration,
    }


def _group_context(preflight, request, ordered, targets, labels):
    groups = {}
    for item in request['groups']:
        boundary = np.load(preflight / 'groups' / item['group_id'] / 'boundary.npz', allow_pickle=False)
        rows = [index for index, (pair, _path) in enumerate(ordered) if pair['group_id'] == item['group_id']]
        member_rows = [request['person_ids'].index(member) for member in item['member_ids']]
        groups[item['group_id']] = {
            'guide_rows': rows,
            'guide_ids': [ordered[index][0]['guide_id'] for index in rows],
            'targets': targets[[ordered[index][0]['target_row'] for index in rows]],
            'deployment': boundary['deployment'],
            'crowd': boundary['crowd'],
            'member_positions': np.asarray(request['positions'], dtype=float)[member_rows],
            'member_radii': np.asarray(request['radii'], dtype=float)[member_rows],
        }
    return groups


def _domain(preflight, request, wiring):
    polygons = []
    for item in sorted(request['groups'], key=lambda group: group['group_id']):
        boundary = np.load(preflight / 'groups' / item['group_id'] / 'boundary.npz', allow_pickle=False)
        polygons.append(Polygon(boundary['crowd']))
    return navigation_domain(
        request['room_m'], wiring['wall_margin_m'], unary_union(polygons), wiring['crowd_margin_m'],
        simplify_m=wiring['simplify_m'], planning_margin_m=wiring['planning_margin_m'],
    )


def loaded_wiring(preflight):
    path = Path(preflight) / 'effective_config.json'
    if not path.is_file():
        raise _Rejected('INSUFFICIENT_EVIDENCE', 'preflight has no effective_config snapshot; defaults were not substituted')
    document = json.loads(path.read_text(encoding='utf-8'))
    wiring = document.get('wiring')
    if not isinstance(wiring, dict):
        raise _Rejected('INSUFFICIENT_EVIDENCE', 'effective_config has no wiring snapshot')
    return wiring


def _limits(radii, wiring, policy):
    return Safety(
        wiring['guide_separation_m'],
        wiring['guide_radius_m'] + float(np.max(radii)) + wiring['clearance_m'],
        wiring['wall_margin_m'],
        policy['max_speed_m_s'],
        wiring['numeric_tolerance_m'],
    )


def _persist_partial(output, record):
    partial = record.get('_partial')
    if not partial:
        record['steps'] = 0
        record['motion_executed'] = False
        record['executed_path_length_m'] = None
        record['prefix_acceptance'] = None
        return
    history = partial['history']
    actuals = partial['actuals']
    nominals = partial['nominals']
    loaded = partial['loaded']
    trajectory = np.asarray(history, dtype=float)
    width = trajectory.shape[1]
    velocity = np.asarray(actuals, dtype=float) if actuals else np.zeros((0, width, 2))
    nominal = np.asarray(nominals, dtype=float) if nominals else np.zeros((0, width, 2))
    _save_trajectory(output, loaded, trajectory, nominal, velocity, partial['waypoints'])
    record['steps'] = int(len(actuals))
    record['guide_ids'] = list(loaded['guide_ids'])
    record['motion_executed'] = bool(len(actuals))
    record['executed_path_length_m'] = (
        float(np.linalg.norm(np.diff(trajectory, axis=0), axis=2).sum()) if len(actuals) else None
    )
    verdict = _verdict(loaded, history, actuals, 'EXPERIMENT_ERROR') if len(actuals) else _unevaluated(loaded)
    record['prefix_acceptance'] = verdict
    record['group_results'] = verdict['group_results']
    record['system_acceptance'] = verdict['system_acceptance']


def _mean_remaining(controller, positions):
    active = np.flatnonzero(controller.assignment >= 0)
    if len(active) == 0:
        return 0.0
    return float(np.mean([controller.guide_remaining_path(int(index), positions[index]) for index in active]))


def _target_errors(loaded, positions):
    errors = []
    for index, (pair, _path) in enumerate(loaded['ordered']):
        errors.append(np.linalg.norm(positions[index] - loaded['targets'][pair['target_row']]))
    return np.asarray(errors, dtype=float)


def _save_trajectory(output, loaded, positions, nominal, actual, waypoints):
    np.savez_compressed(
        output / 'trajectory.npz',
        time=np.arange(len(positions)) * loaded['policy']['dt_s'],
        positions=positions,
        nominal_velocity=nominal,
        safe_velocity=actual,
        guide_ids=np.asarray(loaded['guide_ids'], dtype=int),
        person_ids=np.asarray(loaded['request']['person_ids'], dtype=int),
        person_positions=loaded['people'],
        targets=loaded['targets'],
    )
    _write_json(output / 'waypoints.json', [{str(key): int(value) for key, value in item.items()} for item in waypoints])


def _blank():
    return {
        'schema': 'step2a_execution_v1',
        'policy': EXECUTION_POLICY['policy'],
        'recovery_policy': 'disabled',
        'equivalent_to_step1_recovery': False,
        'allocation_feasible': None,
        'preflight_accepted': False,
        'group_results': {},
        'system_acceptance': {'passed': False, 'reasons': ['not_executed']},
        'deployment_success': False,
        'orchestration_status': 'OK',
        'motion_executed': False,
        'executed_path_length_m': None,
        'status': None,
        'recovery_called': False,
    }


def _policy_hash():
    path = Path(__file__).resolve().parents[3] / 'configs' / 'step2a' / 'fixed_paths_no_recovery_v1.yaml'
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path, payload):
    path.write_text(json.dumps(_jsonable(payload), indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


class _People:
    def __init__(self, positions):
        self.positions = positions


class _Rejected(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason
