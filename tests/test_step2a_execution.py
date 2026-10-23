"""Deterministic fixed-path execution. No random crowds and no recovery."""
import ast
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon

from udc.analysis.summary import summarize_results
from udc.controller.abcg import ABCGController
from udc.execution import evaluate_execution, execute_preflight, reevaluate_execution
from udc.execution.acceptance import window_passes
from udc.execution.fixed_paths import EXECUTION_POLICY, candidate_motion_allowed
from udc.navigation import navigation_domain
from udc.preflight.pipeline import FrozenInput, compact_execution_input, run_preflight
from udc.safety import Safety, is_safe, path_clearances

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def compiled(tmp_path_factory):
    preflight = tmp_path_factory.mktemp('preflight')
    execution = tmp_path_factory.mktemp('execution')
    assert run_preflight(compact_execution_input(), preflight)['status'] == 'PREFLIGHT_ACCEPTED'
    before = {path: path.read_bytes() for path in preflight.rglob('*') if path.is_file()}
    record = execute_preflight(preflight, execution)
    after = {path: path.read_bytes() for path in preflight.rglob('*') if path.is_file()}
    assert before == after
    assert record['status'] == 'SUCCESS'
    return preflight, execution, record


def test_common_window_success_is_not_a_general_claim(compiled):
    _preflight, execution, record = compiled
    assert record['policy'] == 'fixed_paths_no_recovery_v1'
    assert record['recovery_called'] is False
    assert record['allocation_feasible'] is True
    assert record['preflight_accepted'] is True
    assert record['deployment_success'] is True
    assert record['orchestration_status'] == 'OK'
    assert record['steps'] > 5
    assert record['executed_path_length_m'] > 1.0
    assert all(item['passed'] for item in record['group_results'].values())
    assert record['system_acceptance']['common_hold_window_ok'] is True
    assert EXECUTION_POLICY['max_steps'] == 400
    assert EXECUTION_POLICY['equivalent_to_step1_recovery'] is False
    assert not (execution / 'metrics.json').exists()
    assert summarize_results(execution)['tasks'] == 0
    review = reevaluate_execution(execution)
    assert review['consistent'] is True and review['displacement_matches_velocity'] is True
    assert review['replanned'] is False


def test_reordered_identity_keeps_group_ownership(tmp_path, compiled):
    base = compact_execution_input()
    shuffled = FrozenInput(
        base.room, base.person_ids[::-1], base.positions[::-1], base.radii[::-1], base.demand[::-1],
        tuple(reversed(base.groups)), base.guide_ids[::-1], base.guide_positions[::-1],
    )
    preflight = tmp_path / 'preflight'
    execution = tmp_path / 'execution'
    assert run_preflight(shuffled, preflight)['status'] == 'PREFLIGHT_ACCEPTED'
    record = execute_preflight(preflight, execution)
    assert record['deployment_success'] is True
    original = json.loads((compiled[0] / 'allocation.json').read_text(encoding='utf-8'))['assignment']
    again = json.loads((preflight / 'allocation.json').read_text(encoding='utf-8'))['assignment']
    assert again == original


def test_joint_safety_catches_cross_group_motion_and_domain_exit():
    limits = Safety(0.75, 0.75, 0.55, 1.0, 1e-7)
    start = np.array([[2.0, 5.0], [4.0, 5.0]])
    end = np.array([[2.9, 5.0], [3.1, 5.0]])
    people = np.array([[2.0, 1.0], [4.0, 1.0]])
    room = np.array([10.0, 10.0])
    assert is_safe(path_clearances(start[0:1], end[0:1], people, room), limits)
    assert is_safe(path_clearances(start[1:2], end[1:2], people, room), limits)
    assert is_safe(path_clearances(start, end, people, room), limits) is False
    obstacle = Polygon([(4, 4), (6, 4), (6, 6), (4, 6)])
    domain = navigation_domain((12.0, 12.0), 0.3, obstacle, 0.3)
    positions = np.array([[2.0, 5.0], [9.0, 5.0]])
    velocity = np.array([[30.0, 0.0], [0.0, 0.0]])
    allowed, reason, _evidence = candidate_motion_allowed(positions, velocity, people, room, 0.1, limits, domain)
    assert allowed is False and reason == 'outside_shared_domain'


def test_terminal_window_is_not_stitched_from_separate_group_successes():
    context, positions, velocities = _synthetic_motion()
    positions[-5:, 0] = positions[-5:, 0] + np.array([1.0, 0.0])
    verdict = evaluate_execution(positions, velocities, context)
    assert window_passes(positions, velocities, context['groups']['a'], context, 5, 15)
    assert window_passes(positions, velocities, context['groups']['b'], context, 22, 32)
    assert verdict['deployment_success'] is False
    assert verdict['system_acceptance']['common_hold_window_ok'] is False
    arrived = np.array(positions, copy=True)
    arrived[:, 0] = context['groups']['a']['targets'][0]
    for frame in range(len(arrived) - 12, len(arrived) - 2):
        arrived[frame, 0] = context['groups']['a']['targets'][0]
    arrived[-2:, 0] = context['groups']['a']['targets'][0] + np.array([0.5, 0.0])
    pushed = evaluate_execution(arrived, velocities, context)
    assert pushed['group_results']['a']['passed'] is False
    assert pushed['deployment_success'] is False


def test_deadline_stall_illegal_start_and_program_error(tmp_path, compiled, monkeypatch):
    preflight, _execution, _record = compiled
    short = tmp_path / 'timeout'
    monkeypatch.setitem(EXECUTION_POLICY, 'max_steps', 2)
    timeout = execute_preflight(preflight, short)
    assert timeout['status'] == 'CONFIG_MISMATCH'
    assert timeout['motion_executed'] is False
    assert timeout['deployment_success'] is False
    assert timeout['recovery_called'] is False
    assert 'REPLAN_EXHAUSTED' not in str(timeout['failure_reason'])
    monkeypatch.undo()

    def stopped(self, position, path, index, domain, dt):
        self._track_note = None
        return index, np.zeros(2)

    monkeypatch.setattr(ABCGController, '_tracking_velocity', stopped)
    stalled = execute_preflight(preflight, tmp_path / 'stall')
    assert stalled['status'] == 'STALL'
    assert stalled['recovery_called'] is False
    assert 'disabled' in stalled['failure_reason']
    monkeypatch.undo()

    base = compact_execution_input()
    guide_positions = np.array(base.guide_positions, copy=True)
    guide_positions[0] = (12.0, 3.0)
    guide_positions[1] = (12.2, 3.0)
    illegal_input = FrozenInput(
        base.room, base.person_ids, base.positions, base.radii, base.demand, base.groups,
        base.guide_ids, guide_positions,
    )
    illegal_preflight = tmp_path / 'illegal-preflight'
    assert run_preflight(illegal_input, illegal_preflight)['status'] == 'PREFLIGHT_ACCEPTED'
    illegal = execute_preflight(illegal_preflight, tmp_path / 'illegal')
    assert illegal['status'] == 'ILLEGAL_INITIAL_STATE'
    assert illegal['motion_executed'] is False
    assert illegal['executed_path_length_m'] is None

    def explode(*_args, **_kwargs):
        raise RuntimeError('during joint filtering')

    monkeypatch.setattr('udc.controller.abcg.safe_velocity', explode)
    failed = execute_preflight(preflight, tmp_path / 'error')
    assert failed['status'] == 'EXPERIMENT_ERROR'
    assert failed['orchestration_status'] == 'EXPERIMENT_ERROR'
    assert (tmp_path / 'error' / 'execution_error.txt').is_file()
    assert summarize_results(tmp_path / 'error')['tasks'] == 0


def test_tamper_and_hash_mismatch_are_rejected(tmp_path, compiled):
    preflight, execution, _record = compiled
    copied = tmp_path / 'preflight'
    shutil.copytree(preflight, copied)
    request = json.loads((copied / 'request.json').read_text(encoding='utf-8'))
    request['guide_positions'][0][0] += 1.0
    (copied / 'request.json').write_text(json.dumps(request), encoding='utf-8')
    rejected = execute_preflight(copied, tmp_path / 'rejected')
    assert rejected['status'] == 'PREFLIGHT_REJECTED'
    assert rejected['motion_executed'] is False
    trajectory = tmp_path / 'trajectory'
    shutil.copytree(execution, trajectory)
    data = np.load(trajectory / 'trajectory.npz', allow_pickle=False)
    shifted = np.array(data['positions'], copy=True)
    shifted[-1, 0, 0] += 1.0
    np.savez_compressed(
        trajectory / 'trajectory.npz', time=data['time'], positions=shifted,
        nominal_velocity=data['nominal_velocity'], safe_velocity=data['safe_velocity'],
        guide_ids=data['guide_ids'], person_ids=data['person_ids'],
        person_positions=data['person_positions'], targets=data['targets'],
    )
    review = reevaluate_execution(trajectory)
    assert review['consistent'] is False or review['displacement_matches_velocity'] is False


def test_step1_runner_does_not_import_execution():
    source = (ROOT / 'src/udc/experiments/runner.py').read_text(encoding='utf-8')
    imported = [node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    assert 'udc.execution' not in imported


def _synthetic_motion():
    guide_a, group_a = _static_group(0, np.array([6.0, 8.0]), np.array([6.0, 5.0]), 3)
    guide_b, group_b = _static_group(1, np.array([16.0, 8.0]), np.array([16.0, 5.0]), 9)
    positions = np.zeros((40, 2, 2))
    positions[:, 0] = guide_a
    positions[:, 1] = guide_b
    velocities = np.zeros((39, 2, 2))
    domain = navigation_domain((24.0, 16.0), 0.3, Polygon([(11, 1), (12, 1), (12, 2), (11, 2)]), 0.3)
    groups = {'a': group_a, 'b': group_b}
    context = {
        'hold_steps': 10,
        'position_tolerance_m': 0.03,
        'speed_tolerance_m_s': 0.03,
        'curve_tolerance_m': 0.04,
        'max_gap_limit_m': 2.0,
        'max_speed_m_s': 1.0,
        'dt': 0.1,
        'limits': Safety(0.75, 0.75, 0.55, 1.0, 1e-7),
        'people': np.array([[6.0, 2.0], [16.0, 2.0]]),
        'room': np.array([24.0, 16.0]),
        'domain': domain,
        'groups': groups,
        'pairs': [
            {'guide_id': 3, 'group_id': 'a', 'target_row': 0},
            {'guide_id': 9, 'group_id': 'b', 'target_row': 1},
        ],
        'guide_ids': [3, 9],
        'target_groups': ['a', 'b'],
        'orchestration_status': 'OK',
    }
    return context, positions, velocities


def _static_group(row, guide, member, guide_id):
    guide = np.asarray(guide, dtype=float)
    member = np.asarray(member, dtype=float)
    return guide, {
        'guide_rows': [row],
        'guide_ids': [guide_id],
        'targets': guide.reshape(1, 2),
        'deployment': _circle(guide - np.array([0.25, 0.0]), 0.25),
        'crowd': _circle(member, 0.5),
        'member_positions': member.reshape(1, 2),
        'member_radii': np.array([0.05]),
    }


def _circle(center, radius, count=16):
    angles = np.linspace(0.0, 2.0 * np.pi, count, endpoint=False)
    return np.column_stack([center[0] + radius * np.cos(angles), center[1] + radius * np.sin(angles)])
