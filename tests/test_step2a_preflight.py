"""Deterministic Step 2a planning preflight. No motion and no random crowds."""
import ast
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon
from shapely.ops import unary_union

from udc.allocation import GroupResourceFloor, StaticGroup
from udc.analysis.summary import summarize_results
from udc.boundary import estimate_boundary
from udc.navigation import navigate_and_assign, navigation_domain, segment_in_free
from udc.navigation.paths import _obstacle_graph_nodes, assign_within_groups, target_domain_check
from udc.preflight import reevaluate_preflight, run_preflight, separated_demo_input
from udc.preflight.pipeline import WIRING_BASELINE, _pairwise

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def accepted(tmp_path_factory):
    directory = tmp_path_factory.mktemp('accepted')
    record = run_preflight(separated_demo_input(), directory)
    assert record['status'] == 'PREFLIGHT_ACCEPTED'
    return directory


def test_separated_preflight_is_not_a_deployment_success(accepted):
    record = json.loads((accepted / 'preflight.json').read_text(encoding='utf-8'))
    assert record['preflight_accepted'] is True
    assert record['allocation_feasible'] is True
    assert record['deployment_success'] is None
    assert record['motion_executed'] is False
    assert record['arrival_evaluated'] is None
    assert record['executed_path_length_m'] is None
    assert not (accepted / 'metrics.json').exists()
    assert not (accepted / 'runner_error.txt').exists()
    assert summarize_results(accepted)['tasks'] == 0
    domain = json.loads((accepted / 'domain.json').read_text(encoding='utf-8'))
    assert domain['occupied_type'] == 'MultiPolygon'
    assert domain['joint_convex_hull'] is False
    assert domain['component_count'] == 2
    review = reevaluate_preflight(accepted)
    assert review['consistent'] is True and review['replanned'] is False
    request = json.loads((accepted / 'request.json').read_text(encoding='utf-8'))
    assert max(request['person_ids']) > len(request['person_ids'])
    identity = json.loads((accepted / 'identity_map.json').read_text(encoding='utf-8'))
    assert identity[str(request['person_ids'][3])] == 3
    boundary = np.load(accepted / 'groups' / 'a' / 'boundary.npz', allow_pickle=False)
    rows = boundary['rows']
    own = estimate_boundary(
        np.asarray(request['positions'])[rows], WIRING_BASELINE['offset_m'], WIRING_BASELINE['sample_spacing_m'],
        request['room_m'], WIRING_BASELINE['wall_margin_m'], WIRING_BASELINE['crowd_distance_m'],
        np.asarray(request['radii'])[rows],
    )
    assert np.allclose(boundary['crowd'], own.crowd)
    everyone = estimate_boundary(
        request['positions'], WIRING_BASELINE['offset_m'], WIRING_BASELINE['sample_spacing_m'],
        request['room_m'], WIRING_BASELINE['wall_margin_m'], WIRING_BASELINE['crowd_distance_m'],
        request['radii'],
    )
    assert not np.isclose(boundary['length'], everyone.length)


def test_reordered_rows_and_groups_do_not_change_the_assignment(tmp_path, accepted):
    base = separated_demo_input()
    positions = base.positions[::-1]
    radii = base.radii[::-1]
    demand = base.demand[::-1]
    ids = base.person_ids[::-1]
    groups = tuple(reversed(base.groups))
    guides = base.guide_ids[::-1]
    guide_positions = base.guide_positions[::-1]
    shuffled = type(base)(
        base.room, ids, positions, radii, demand, groups, guides, guide_positions,
        strategy=base.strategy, floor_policy=base.floor_policy,
    )
    record = run_preflight(shuffled, tmp_path)
    assert record['status'] == 'PREFLIGHT_ACCEPTED'
    original = json.loads((accepted / 'allocation.json').read_text(encoding='utf-8'))['assignment']
    again = json.loads((tmp_path / 'allocation.json').read_text(encoding='utf-8'))['assignment']
    assert again == original


def test_budget_shortfall_and_frozen_violation_stay_distinct(tmp_path):
    base = separated_demo_input()
    short_dir = tmp_path / 'short'
    short = type(base)(
        base.room, base.person_ids, base.positions, base.radii, base.demand, base.groups,
        (7, 8), [(3.0, 4.0), (36.0, 4.0)],
    )
    short_record = run_preflight(short, short_dir)
    assert short_record['status'] == 'BUDGET_SHORTFALL'
    assert short_record['allocation_feasible'] is False
    assert short_record['preflight_accepted'] is False
    assert not (short_dir / 'targets.npz').exists()
    floors = (GroupResourceFloor('a', 3), GroupResourceFloor('b', 3))
    frozen_dir = tmp_path / 'frozen'
    frozen = type(base)(
        base.room, base.person_ids, base.positions, base.radii, base.demand, base.groups,
        tuple(range(6)), [(3.0, 4.0 + i) for i in range(3)] + [(36.0, 4.0 + i) for i in range(3)],
        strategy='frozen_initial_split', floor_policy='declared', declared_floors=floors,
        mapping=((0, 'a'), (1, 'a'), (2, 'a'), (3, 'a'), (4, 'a'), (5, 'b')),
    )
    frozen_record = run_preflight(frozen, frozen_dir)
    assert frozen_record['status'] == 'FROZEN_FLOOR_VIOLATION'
    assert frozen_record['allocation_feasible'] is False
    assert frozen_record['status'] != 'BUDGET_SHORTFALL'


def test_membership_geometry_and_declared_plan_failures_keep_their_artifacts(tmp_path):
    base = separated_demo_input()
    missing = type(base)(
        base.room, base.person_ids, base.positions, base.radii, base.demand,
        (StaticGroup('a', base.groups[0].member_ids[:-1]), base.groups[1]),
        base.guide_ids, base.guide_positions,
    )
    missing_dir = tmp_path / 'missing'
    missing_record = run_preflight(missing, missing_dir)
    assert missing_record['status'] == 'MEMBERSHIP_INVALID'
    assert missing_record['failure_code'] == 'INCOMPLETE_PARTITION'
    assert (missing_dir / 'request.json').is_file()
    overlap = type(base)(
        base.room, base.person_ids, base.positions, base.radii, base.demand,
        (StaticGroup('a', base.groups[0].member_ids),
         StaticGroup('b', (base.groups[0].member_ids[0],) + base.groups[1].member_ids)),
        base.guide_ids, base.guide_positions,
    )
    overlap_record = run_preflight(overlap, tmp_path / 'overlap')
    assert overlap_record['failure_code'] == 'OVERLAPPING_MEMBERS'
    positions = np.array(base.positions, copy=True)
    radii = np.array(base.radii, copy=True)
    rows_b = [index for index, person in enumerate(base.person_ids) if person in set(base.groups[1].member_ids)]
    positions[rows_b] = (24.0, 18.0)
    radii[rows_b] = 0.0
    broken = type(base)(
        base.room, base.person_ids, positions, radii, base.demand, base.groups,
        base.guide_ids, base.guide_positions,
    )
    broken_dir = tmp_path / 'geometry'
    broken_record = run_preflight(broken, broken_dir)
    assert broken_record['status'] == 'GEOMETRY_REJECTED'
    assert broken_record['allocation_feasible'] is None
    assert (broken_dir / 'groups' / 'a' / 'boundary.npz').is_file()
    assert not (broken_dir / 'allocation.json').exists()
    floors = (GroupResourceFloor('a', 3), GroupResourceFloor('b', 3))
    declared = type(base)(
        base.room, base.person_ids, base.positions, base.radii, base.demand, base.groups,
        tuple(range(6)), [(3.0, 5.0 + i) for i in range(3)] + [(36.0, 5.0 + i) for i in range(3)],
        floor_policy='declared', declared_floors=floors,
    )
    declared_record = run_preflight(declared, tmp_path / 'declared')
    assert declared_record['allocation_feasible'] is True
    assert declared_record['status'] == 'PLAN_REJECTED'
    assert declared_record['preflight_accepted'] is False
    assignment = json.loads((tmp_path / 'declared' / 'allocation.json').read_text(encoding='utf-8'))
    assert len(assignment['assignment']) == 6


def test_shared_domain_rejects_targets_that_one_group_would_allow():
    own = Polygon([(2, 2), (6, 2), (6, 6), (2, 6)])
    other = Polygon([(8, 2), (12, 2), (12, 6), (8, 6)])
    room = (20.0, 10.0)
    alone = navigation_domain(room, 0.3, own, 0.3)
    shared = navigation_domain(room, 0.3, unary_union([own, other]), 0.3)
    inside_other = np.array([[10.0, 4.0]])
    assert target_domain_check(alone, inside_other)['accepted'] is True
    assert target_domain_check(shared, inside_other)['accepted'] is False
    close = _pairwise(np.array([[1.0, 1.0], [1.2, 1.0]]), WIRING_BASELINE['guide_separation_m'])
    assert close['accepted'] is False


def test_multipolygon_route_avoids_the_other_component_and_groups_do_not_swap():
    left = Polygon([(4, 8), (8, 8), (8, 12), (4, 12)])
    right = Polygon([(12, 8), (16, 8), (16, 12), (12, 12)])
    merged = unary_union([left, right])
    assert merged.geom_type == 'MultiPolygon'
    domain = navigation_domain((22.0, 20.0), 0.4, merged, 0.4)
    routed = assign_within_groups(
        np.array([[1.0, 10.0]]), ('a',), np.array([[20.0, 10.0]]), ('a',), domain, (22.0, 20.0),
    )
    assert routed['groups'][0]['matched'] is True
    path = routed['groups'][0]['pairs'][0]['path']
    assert len(path) > 2
    assert segment_in_free(np.array([1.0, 10.0]), np.array([20.0, 10.0]), domain) is False
    assert all(segment_in_free(path[i], path[i + 1], domain) for i in range(len(path) - 1))
    square = Polygon([(0, 0), (2, 0), (2, 1), (0, 1)])
    nodes = _obstacle_graph_nodes(square)
    expected = [np.asarray(point) for point in square.exterior.coords[:-1]]
    assert len(nodes) == len(expected)
    assert all(np.allclose(left_node, right_node) for left_node, right_node in zip(nodes, expected))
    guides = np.array([[2.0, 2.5], [8.0, 2.5]])
    targets = np.array([[8.0, 3.5], [2.0, 3.5]])
    crowd = Polygon([(4.5, 0.4), (5.2, 0.4), (5.2, 1.0), (4.5, 1.0)])
    open_domain = navigation_domain((12.0, 8.0), 0.2, crowd, 0.2)
    kept = assign_within_groups(guides, ('a', 'b'), targets, ('a', 'b'), open_domain, (12.0, 8.0))
    assert [(pair['guide_row'], pair['target_row']) for pair in kept['groups'][0]['pairs']] == [(0, 0)]
    assert [(pair['guide_row'], pair['target_row']) for pair in kept['groups'][1]['pairs']] == [(1, 1)]
    global_match = navigate_and_assign(guides, targets, (12.0, 8.0), 0.2, crowd, 0.2)
    assert list(global_match.assignment) == [1, 0]


def test_tamper_is_detected_and_program_errors_stay_out_of_step1_success(accepted, tmp_path, monkeypatch):
    copied = tmp_path / 'copy'
    shutil.copytree(accepted, copied)
    data = np.load(copied / 'targets.npz', allow_pickle=False)
    shifted = np.array(data['positions'], copy=True)
    shifted[0] = (24.0, 19.0)
    np.savez_compressed(
        copied / 'targets.npz', positions=shifted, arc=data['arc'], local_target_id=data['local_target_id'],
    )
    assert reevaluate_preflight(copied)['consistent'] is False
    assert reevaluate_preflight(accepted)['consistent'] is True

    request = separated_demo_input()

    def explode(*_args, **_kwargs):
        raise RuntimeError('after the request was registered')

    monkeypatch.setattr('udc.preflight.pipeline.estimate_boundary', explode)
    failed = tmp_path / 'error'
    record = run_preflight(request, failed)
    assert record['status'] == 'EXPERIMENT_ERROR'
    assert (failed / 'request.json').is_file()
    assert (failed / 'preflight_error.txt').is_file()
    assert not (failed / 'runner_error.txt').exists()
    assert summarize_results(failed)['tasks'] == 0


def test_step1_runner_does_not_import_preflight_or_allocation():
    source = (ROOT / 'src/udc/experiments/runner.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert 'udc.preflight' not in imported
    assert 'udc.allocation' not in imported
    assert 'udc.preflight' not in source
