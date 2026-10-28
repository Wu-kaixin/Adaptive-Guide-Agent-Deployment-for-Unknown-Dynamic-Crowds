"""Adversarial contracts for the opt-in static multi-group study."""
from dataclasses import replace
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import Point

from udc.interfaces import Observation
from udc.multigroup.observation import StaticTruth, BoundedMeasurement, measure, static_groups
from udc.multigroup.geometry import construct_groups, outer_disk, ScientificFailure
from udc.multigroup.allocation import Candidate, allocate, allocation_objective, build_catalog, selected_targets
from udc.multigroup.execution import Execution, execute
from udc.multigroup.evaluation import motion_metrics, partition_matches, evaluate
from udc.multigroup.scenarios import make_truth, initial_guides
from udc.experiments.static_multigroup import load_protocol, prepare, verify, run, replay


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'configs/step1/static_multigroup_v1.yaml'


@pytest.fixture
def spec():
    return load_protocol(PROTOCOL)


def synthetic_candidate(count, uncovered, cost=.1):
    return Candidate(count, np.zeros(count), np.zeros((count, 2)), uncovered, cost, cost, 2., 0)


def synthetic_groups(weights):
    return [SimpleNamespace(demand_total=w, boundary=SimpleNamespace(length=10. + i)) for i, w in enumerate(weights)]


def test_measurement_is_bounded_frozen_and_does_not_alias_truth():
    truth = StaticTruth([[1., 2.], [3., 4.]], [.2, .2], [1., 3.], [0, 1])
    frozen = truth.positions.copy()
    measured = measure(truth, .12, 123)
    assert 0 < np.linalg.norm(measured.observation.positions - truth.positions, axis=1).max() <= .12
    for field in ('positions', 'radii', 'demand'):
        assert not np.shares_memory(getattr(truth, field), getattr(measured.observation, field))
        assert not getattr(measured.observation, field).flags.writeable
    with pytest.raises(ValueError):
        measured.observation.positions[0, 0] = -100
    np.testing.assert_array_equal(truth.positions, frozen)
    np.testing.assert_array_equal(measure(truth, 0., 123).observation.positions, truth.positions)
    np.testing.assert_array_equal(measured.observation.positions, measure(truth, .12, 123).observation.positions)
    for bad in (-1., np.inf, np.nan):
        with pytest.raises(ValueError):
            measure(truth, bad, 0)


def test_bias_is_common_and_error_levels_are_coupled():
    truth = StaticTruth([[1., 2.], [3., 4.]], [.2, .2], [1., 3.], [0, 1])
    delta = measure(truth, .1, 17, 'common_bias').observation.positions - truth.positions
    np.testing.assert_allclose(delta[0], delta[1], atol=1e-15)
    np.testing.assert_allclose(np.linalg.norm(delta, axis=1), .1)
    low = measure(truth, .04, 5).observation.positions - truth.positions
    high = measure(truth, .12, 5).observation.positions - truth.positions
    np.testing.assert_allclose(high, 3 * low, atol=1e-15)


def test_grouping_uses_observations_not_true_labels_or_requested_k():
    obs = Observation(np.array([[0., 0.], [.6, 0.], [10., 0.], [10.6, 0.]]), np.full(4, .2), np.ones(4))
    assert static_groups(obs, .95) == ((0, 1), (2, 3))
    # Moving observed centres genuinely changes inferred membership.
    joined = Observation(obs.positions * .01, obs.radii, obs.demand)
    assert static_groups(joined, .95) == ((0, 1, 2, 3),)


@pytest.mark.parametrize('group_count', [1, 2, 3, 5])
@pytest.mark.parametrize('shape', ['circle', 'ellipse', 'concave', 'irregular'])
def test_four_shape_families_and_group_counts_have_observation_only_envelopes(spec, group_count, shape):
    truth = make_truth(group_count, shape, 1200, spec['namespace'])
    measured = measure(truth, .12, 123)
    groups, _ = construct_groups(measured, spec['room_m'], spec['model'])
    assert len(groups) == group_count
    assert partition_matches(groups, truth.group_ids)
    for group in groups:
        for i in group.rows:
            # Independent physical-circle sampling is inside the conservative
            # analytic-disk outer model, even for worst support directions.
            physical = Point(truth.positions[i]).buffer(truth.radii[i], quad_segs=256)
            assert group.occupied.covers(physical)
    poisoned_truth = StaticTruth(truth.positions + .03, truth.radii, truth.demand,
                                 np.zeros_like(truth.group_ids))
    assert not np.shares_memory(poisoned_truth.positions, measured.observation.positions)
    repeated, _ = construct_groups(measured, spec['room_m'], spec['model'])
    assert [g.occupied.wkb for g in groups] == [g.occupied.wkb for g in repeated]


def test_outer_disk_contains_analytic_support_and_group_overlap_is_rejected(spec):
    polygon = outer_disk([0., 0.], .32)
    angle = np.linspace(0, 2 * np.pi, 10001)
    assert all(polygon.covers(Point(.32 * np.cos(a), .32 * np.sin(a))) for a in angle)
    truth = StaticTruth([[10., 10.], [14., 10.]], [.2, .2], [1., 1.], [0, 1])
    config = dict(spec['model'], offset_m=2.)
    with pytest.raises(ScientificFailure, match='not safely separated'):
        construct_groups(measure(truth, 0., 0), spec['room_m'], config)


def test_bottleneck_dp_matches_exhaustive_search_including_masked_maximum():
    rng = np.random.default_rng(19)
    for _ in range(50):
        catalogs = tuple({0: synthetic_candidate(0, 1., .25),
                          1: synthetic_candidate(1, float(rng.choice([.1, .3, .5, .8])), float(rng.uniform(.01, .2))),
                          2: synthetic_candidate(2, float(rng.choice([0., .1, .4])), float(rng.uniform(.01, .2)))}
                         for _ in range(3))
        weights = [1., 2., 4.]
        groups = synthetic_groups(weights)
        for budget in range(0, 7):
            result = allocate(catalogs, groups, budget)
            possible = [counts for counts in itertools.product(range(3), repeat=3) if sum(counts) <= budget]
            optimum = min(allocation_objective([c[n] for c, n in zip(catalogs, counts)], weights)
                          for counts in possible)
            np.testing.assert_allclose(result.objective, optimum, atol=1e-15)
            assert sum(result.counts) <= budget
    # Zero-budget is represented, never dropped as a failed initialization.
    zero = allocate(catalogs, groups, 0)
    assert zero.counts == (0, 0, 0) and zero.objective[0] == 1.


def test_allocator_can_leave_surplus_idle_and_baselines_share_candidate_options():
    catalogs = ({0: synthetic_candidate(0, 1.), 1: synthetic_candidate(1, .2), 3: synthetic_candidate(3, .1)},
                {0: synthetic_candidate(0, 1.), 2: synthetic_candidate(2, .1)})
    groups = synthetic_groups([2., 1.])
    for policy in ('minimax', 'perimeter', 'demand'):
        result = allocate(catalogs, groups, 20, policy)
        assert result.active == 5 and result.available == 20
        assert all(n in c for n, c in zip(result.counts, catalogs))
    sparse = allocate(catalogs, groups, 1)
    assert sparse.counts == (1, 0) and sparse.objective[0] == 1.


def test_catalog_supports_one_two_and_zero_service_without_changing_legacy_planner(spec):
    truth = make_truth(1, 'circle', 1201, spec['namespace'])
    measured = measure(truth, .12, 99)
    groups, domain = construct_groups(measured, spec['room_m'], spec['model'])
    for layout in spec['layouts']:
        catalog = build_catalog(groups, measured, domain, 3, layout, spec['model'])
        assert set(catalog[0]) == {0, 1, 2, 3}
        result = allocate(catalog, groups, 2)
        targets, ids = selected_targets(result, spec['model'])
        assert len(targets) <= 2 and len(ids) == len(targets)
        assert result.objective[0] > 0


def test_truth_evaluator_detects_mid_interval_collision_and_does_not_read_observation(spec):
    truth = StaticTruth([[5., 5.]], [.2], [1.], [0])
    crossing = Execution(np.array([[[3., 5.]], [[7., 5.]]]), np.array([[[20., 0.]]]),
                         np.array([0]), 'ARRIVED', (), {})
    result = motion_metrics(crossing, truth, [10., 10.], spec['model'])
    assert result['initial_safety_ok']
    assert not result['interval_safety_ok'] and result['unsafe_interval_count'] == 1
    assert result['minimum_net_clearances_m']['human'] == pytest.approx(-.75)
    far_truth = StaticTruth([[5., 8.]], [.2], [1.], [0])
    assert motion_metrics(crossing, far_truth, [10., 10.], spec['model'])['interval_safety_ok']


def test_serial_execution_moves_safely_with_an_independent_truth_check(spec):
    truth = make_truth(2, 'concave', 1200, spec['namespace'])
    measurement = measure(truth, .12, 123)
    groups, domain = construct_groups(measurement, spec['room_m'], spec['model'])
    catalog = build_catalog(groups, measurement, domain, 2, 'equal_arc', spec['model'])
    allocation = allocate(catalog, groups, 2)
    targets, ids = selected_targets(allocation, spec['model'])
    execution = execute(initial_guides(2, spec['room_m']), targets, domain, spec['room_m'], spec['model'])
    result = evaluate(execution, targets, ids, groups, measurement, truth, spec['room_m'], spec['model'])
    assert execution.status == 'ARRIVED'
    assert result['deployment_completed'] and result['motion']['interval_safety_ok']
    assert not result['full_task_success']  # Partial service never becomes full success.
    assert result['motion']['max_speed_m_s'] <= spec['model']['max_speed_m_s'] + 1e-12


def test_frozen_manifest_separates_files_rejects_tampering_and_reserved_seeds(tmp_path, spec):
    study = tmp_path / 'frozen'
    selection = {'group_counts': [1], 'shapes': ['circle'], 'error_bounds_m': [.12],
                 'resources': ['insufficient'], 'layouts': ['equal_arc'], 'allocators': ['minimax']}
    result = prepare(PROTOCOL, study, True, [1200], selection)
    assert result['tasks'] == 1
    manifest = verify(study)
    task = manifest['tasks'][0]
    assert task['truth'] != task['observation']
    with np.load(study / task['observation'], allow_pickle=False) as data:
        assert set(data.files) == {'positions', 'radii', 'demand'}
    with pytest.raises(ValueError, match='reserved'):
        prepare(PROTOCOL, tmp_path / 'reserved', True, [5000], selection)
    with pytest.raises(ValueError, match='whole frozen'):
        prepare(PROTOCOL, tmp_path / 'short-confirmation', False, [2000])
    observation = study / task['observation']
    observation.write_bytes(observation.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='FROZEN_INPUT_CHANGED'):
        verify(study)


def test_saved_run_replays_and_retains_expected_partial_service(tmp_path, spec):
    study = tmp_path / 'execution'
    selection = {'group_counts': [1], 'shapes': ['irregular'], 'error_bounds_m': [.12],
                 'resources': ['insufficient'], 'layouts': ['equal_arc'], 'allocators': ['minimax']}
    prepare(PROTOCOL, study, True, [1200], selection)
    summary = run(study, workers=1)
    assert summary['recorded_tasks'] == 1
    assert summary['statuses'] == {'PARTIAL_SERVICE': 1}
    assert summary['unsafe_intervals'] == 0
    assert replay(study) == {'checked': 1, 'mismatches': [], 'all_match': True}
    manifest = verify(study)
    folder = study / 'results' / manifest['tasks'][0]['task_id']
    assert {p.name for p in folder.iterdir()} >= {'config.json', 'planning.json', 'trajectory.npz', 'metrics.json', 'state.json', 'receipt.json'}
    metrics = json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))
    assert metrics['certified_planning_capacity_shortfall']
    assert not metrics['full_task_success']


def test_failure_artifacts_and_missing_motion_are_not_zero_safety_evidence(tmp_path, monkeypatch):
    import udc.experiments.static_multigroup as module
    study = tmp_path / 'expected-failure'
    selection = {'group_counts': [1], 'shapes': ['circle'], 'error_bounds_m': [.12],
                 'resources': ['insufficient'], 'layouts': ['equal_arc'], 'allocators': ['minimax']}
    prepare(PROTOCOL, study, True, [1200], selection)
    def reject(*args):
        raise ScientificFailure('GROUP_SEPARATION_INVALID', 'test witness', distance_m=.2)
    monkeypatch.setattr(module, 'construct_groups', reject)
    summary = run(study, workers=1)
    assert summary['statuses'] == {'GROUP_SEPARATION_INVALID': 1}
    assert summary['motion_tasks'] == 0
    assert summary['full_successes'] == 0


def test_v2_explicit_heterogeneity_preserves_physical_spacing_and_group_identity():
    spec = load_protocol(ROOT / 'configs/step1/static_multigroup_v2.yaml')
    truth = make_truth(5, 'concave', 1200, spec['namespace'],
                       spec['group_size_multipliers'], spec['group_hotspot_multipliers'])
    distances = np.linalg.norm(truth.positions[:, None] - truth.positions[None, :], axis=2)
    distances[np.diag_indices(len(distances))] = np.inf
    assert distances.min() > .4
    groups, _ = construct_groups(measure(truth, .12, 123), spec['room_m'], spec['model'])
    assert partition_matches(groups, truth.group_ids)
    assert max(g.boundary.length for g in groups) > 1.3 * min(g.boundary.length for g in groups)
    assert len(set(len(g.rows) for g in groups)) > 1
    assert spec['namespace'] != load_protocol(PROTOCOL)['namespace']


def test_primary_statistics_keep_failed_draws_and_weight_strata_equally():
    from udc.analysis.static_multigroup import primary_analysis
    spec = load_protocol(PROTOCOL)
    tasks, records = [], []
    for k, shape in [(2, 'circle'), (5, 'concave')]:
        for seed in (1, 2, 3):
            for method in ('minimax', 'perimeter'):
                row = {'group_count': k, 'shape': shape, 'seed': seed, 'layout': 'projected_mass',
                       'resource': 'insufficient', 'error_bound_m': .12, 'allocator': method}
                tasks.append(row)
                # One missing paired physical draw stays in the second stratum.
                if k == 5 and seed == 3:
                    records.append(row | {'status': 'BOUNDARY_INVALID'})
                else:
                    value = .4 if method == 'perimeter' else (.3 if k == 2 else .2)
                    records.append(row | {'planned_common_service': {'worst_group_uncovered': value}})
    manifest = {'spec': spec, 'tasks': tasks, 'seeds': [1, 2, 3], 'evidence': 'development'}
    result = primary_analysis(manifest, records)
    assert [row['draws'] for row in result['strata']] == [3, 3]
    assert [row['valid_pairs'] for row in result['strata']] == [3, 2]
    assert result['equal_stratum_mean_delta'] == pytest.approx(-.15)
    assert not result['prospective_confirmation_completed']


def test_program_error_is_saved_and_stops_instead_of_becoming_scientific_failure(tmp_path, monkeypatch):
    import udc.experiments.static_multigroup as module
    study = tmp_path / 'program-error'
    selection = {'group_counts': [1], 'shapes': ['circle'], 'error_bounds_m': [.12],
                 'resources': ['insufficient'], 'layouts': ['equal_arc'], 'allocators': ['minimax']}
    prepare(PROTOCOL, study, True, [1200], selection)
    def broken(*args):
        raise RuntimeError('adversarial implementation fault')
    monkeypatch.setattr(module, 'construct_groups', broken)
    summary = run(study, workers=1)
    assert summary['statuses'] == {'EXPERIMENT_ERROR': 1}
    assert summary['stopped_on_error_or_safety']
    manifest = verify(study)
    folder = study / 'results' / manifest['tasks'][0]['task_id']
    assert 'adversarial implementation fault' in (folder / 'runner_error.txt').read_text(encoding='utf-8')
