import numpy as np
import pytest

from udc.boundary import Boundary
from udc.multigroup.service import continuous_arc_service, continuous_euclidean_service
from udc.analysis.service_audit import bounded_pair_effect, full_ledger_analysis


def square():
    points = np.array([[0., 0.], [2., 0.], [2., 2.], [0., 2.]])
    return Boundary(points, points, np.array([0., 2., 4., 6.]), 8.)


def test_euclidean_corner_disk_has_analytic_length():
    row = continuous_euclidean_service(square(), np.ones(4), [[0., 0.]], 1.)
    assert row['uncovered'] == pytest.approx(.75)
    assert row['uncovered_length_m'] == pytest.approx(6.)
    assert not row['full_curve_covered']


def test_continuous_density_integral_is_not_uniform_length():
    # Bottom first metre: integral 1.25; left first metre: integral 1.
    row = continuous_euclidean_service(square(), [1., 2., 1., 1.], [[0., 0.]], 1.)
    assert row['uncovered'] == pytest.approx(1 - 2.25 / 10.)


def test_disk_union_does_not_double_count_and_handles_no_service():
    single = continuous_euclidean_service(square(), np.ones(4), [[0., 0.]], 1.)
    duplicate = continuous_euclidean_service(square(), np.ones(4), [[0., 0.], [0., 0.]], 1.)
    assert single == duplicate
    assert continuous_euclidean_service(square(), np.ones(4), [], 1.)['uncovered'] == 1.
    assert continuous_euclidean_service(square(), np.ones(4), [[1., 1.]], 2.)['full_curve_covered']


def test_arc_wraparound_and_subsample_gap():
    assert continuous_arc_service(square(), np.ones(4), [0.], 1.)['uncovered'] == pytest.approx(.75)
    row = continuous_arc_service(square(), np.ones(4), [0., 2., 4., 6.], .9999)
    assert not row['full_curve_covered']
    assert row['uncovered_length_m'] == pytest.approx(.0008)
    assert continuous_arc_service(square(), np.ones(4), [0.], 4.)['full_curve_covered']


def test_tangencies_have_no_positive_service_mass():
    assert continuous_euclidean_service(square(), np.ones(4), [[1., 1.]], 1.)['uncovered'] == pytest.approx(1.)


def test_partial_identification_keeps_each_missing_method():
    assert bounded_pair_effect(.2, .3) == pytest.approx((-.1, -.1))
    assert bounded_pair_effect(.2, None) == pytest.approx((-.8, .2))
    assert bounded_pair_effect(None, .3) == pytest.approx((-.3, .7))
    assert bounded_pair_effect(None, None) == (-1., 1.)


def test_full_ledger_does_not_turn_missing_pairs_into_success_subset():
    stats = {'primary_layout': 'projected_mass', 'primary_resource': 'insufficient', 'primary_error_bound_m': .12}
    common = {'group_count': 2, 'shape': 'concave', 'layout': 'projected_mass',
              'resource': 'insufficient', 'error_bound_m': .12}
    tasks = [{**common, 'seed': seed, 'allocator': method} for seed in [1, 2] for method in ['perimeter', 'minimax']]
    records = [{**task, 'planned_common_service': {'worst_group_uncovered': .2 if task['allocator'] == 'minimax' else .4}}
               for task in tasks if task['seed'] == 1]
    result = full_ledger_analysis({'spec': {'statistics': stats}, 'tasks': tasks}, records)
    assert result['scheduled_pairs'] == 2
    assert result['planned_equal_stratum_bounds'] == pytest.approx([-.6, .4])
    assert result['safely_completed_actual_equal_stratum_bounds'] == [-1., 1.]
    assert result['strata'][0]['missing_planned_pairs'] == 1


def test_continuous_euclidean_matches_dense_independent_midpoint_reference():
    # Independent square parameterization, including overlapping service disks.
    robots = np.array([[.2, .1], [1.2, 1.8], [1.8, .3]])
    result = continuous_euclidean_service(square(), [1., 2., 3., 1.], robots, .7)
    arcs = (np.arange(100000) + .5) * 8 / 100000
    points = np.c_[np.interp(arcs, [0, 2, 4, 6, 8], [0, 2, 2, 0, 0]),
                   np.interp(arcs, [0, 2, 4, 6, 8], [0, 0, 2, 2, 0])]
    density = np.interp(arcs, [0, 2, 4, 6, 8], [1, 2, 3, 1, 1])
    distance = np.linalg.norm(points[:, None] - robots[None], axis=2).min(axis=1)
    sampled = density[distance > .7].sum() / density.sum()
    assert result['uncovered'] == pytest.approx(sampled, abs=5e-5)


def test_normalized_service_is_invariant_to_groupwise_demand_scaling():
    from udc.deployment.planners import demand_profile
    boundary = square()
    people = np.array([[.5, .4], [1.4, 1.7]])
    a = demand_profile(boundary, people, np.array([1., 3.]), 1.2)
    b = demand_profile(boundary, people, np.array([1., 3.]) * 12, 1.2)
    np.testing.assert_allclose(a, b, rtol=0, atol=1e-14)
    first = continuous_euclidean_service(boundary, a, [[0., 0.]], 1.)
    second = continuous_euclidean_service(boundary, b, [[0., 0.]], 1.)
    assert first['uncovered'] == pytest.approx(second['uncovered'], abs=1e-14)


def test_2048_midpoints_can_miss_positive_euclidean_service_gaps():
    from udc.deployment.planners import _interpolate
    boundary = square()
    robots = boundary.deployment.copy()
    arcs = (np.arange(2048) + .5) * boundary.length / 2048
    points = _interpolate(boundary, arcs)
    nearest = np.linalg.norm(points[:, None] - robots[None], axis=2).min(axis=1)
    assert np.all(nearest <= .9999)
    continuous = continuous_euclidean_service(boundary, np.ones(4), robots, .9999)
    assert continuous['uncovered'] == pytest.approx(.0001, abs=1e-12)
    assert not continuous['full_curve_covered']
