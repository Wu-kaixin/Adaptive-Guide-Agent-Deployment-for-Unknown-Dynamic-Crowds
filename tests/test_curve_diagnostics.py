"""Prescribed-curve diagnostics stay out of the normal observation path."""
import numpy as np
import pytest
import yaml

from udc.boundary import estimate_boundary
from udc.boundary.prescribed import (
    boundary_from_ring, build_prescribed_curve, prepare_curve, stadium_ring,
)
from udc.controller.abcg import ABCGController
from udc.deployment.geometry import (
    classify_geometry, count_rule, pair_geometry, termination_for,
)
from udc.deployment.planners import _density, plan_coverage
from udc.environment import Environment
from udc.experiments.config import load_config
from udc.experiments.curve_diagnostics import load_spec, run_task
from udc.safety import Safety

ROOT_SPEC = 'experiments/step1_general_curve_diagnostics.yaml'


def _rectangle(length=10.0, width=0.40, spacing=0.05):
    raw = []
    def edge(start, end):
        distance = float(np.linalg.norm(np.subtract(end, start)))
        count = max(1, int(np.ceil(distance / spacing)))
        for index in range(count):
            raw.append(np.asarray(start) + (np.asarray(end) - np.asarray(start)) * (index / count))
    edge((0, 0), (length, 0))
    edge((length, 0), (length, width))
    edge((length, width), (0, width))
    edge((0, width), (0, 0))
    return boundary_from_ring(np.asarray(raw, dtype=float))


def test_prescribed_curve_is_not_read_by_the_convex_estimator(tmp_path):
    built = build_prescribed_curve('shallow_concave', 'strong', 15)
    centre = built['points'].mean(axis=0) + np.array([20.0, 20.0])
    people = centre + np.array([
        [0.0, 0.0], [0.6, 0.2], [-0.5, 0.4], [0.3, -0.6], [-0.4, -0.3],
    ])
    radii = np.full(len(people), 0.2)
    estimated = estimate_boundary(people, 1.3, 0.1, np.array([40.0, 40.0]), 0.55, 0.75, radii)
    prescribed = built['nominal_report']
    from udc.boundary.prescribed import curve_report
    estimated_report = curve_report(estimated.deployment)
    assert prescribed['convexity_defect'] > 0.005
    assert estimated_report['convexity_defect'] < 1e-4
    assert not np.allclose(estimated.length, built['boundary'].length, atol=0.5)
    config = load_config('configs/step1/square.yaml')
    config['controller']['prescribed_curve'] = built['points'].tolist()
    path = tmp_path / 'case.yaml'
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
    with pytest.raises(ValueError, match='controller'):
        load_config(path)


def test_normal_prepare_estimates_and_does_not_install_a_prescribed_plan():
    config = load_config('configs/step1/static_50.yaml')
    env = Environment(config)
    observation = env.observe()
    guides = env.initialize_guides(config['guides'])
    control = config['controller']
    safety = Safety(
        2 * config['guides']['radius'] + control['clearance'],
        config['guides']['radius'] + float(observation.radii.max()) + control['clearance'],
        config['guides']['radius'] + control['clearance'],
        config['guides']['max_speed'],
        control['safety_numeric_tolerance'],
    )
    controller = ABCGController(control, safety, env.scene.size, 'fixed_n', config['guides']['count'])
    controller.prepare(observation, guides)
    assert controller.boundary_source == 'estimated_convex_offset'
    assert controller.prescribed_plan_installed is False
    assert controller.plan_found


def test_seam_pair_is_adjacent_and_the_opposite_pair_is_not():
    theta = np.linspace(0, 2 * np.pi, 200, endpoint=False)
    boundary = boundary_from_ring(np.column_stack([5 * np.cos(theta), 5 * np.sin(theta)]))
    length = boundary.length
    sites = np.array([0.2, length * 0.25, length * 0.55, length - 0.2])
    report = pair_geometry(boundary, sites, 1.94, 0.75)
    adjacent = {(item['i'], item['j']) for item in report['pairwise'] if item['adjacent']}
    other = {(item['i'], item['j']) for item in report['pairwise'] if not item['adjacent']}
    assert (3, 0) in adjacent
    assert (0, 2) in other
    assert report['seam_arc_gap_m'] == pytest.approx(0.4, abs=1e-6)


def test_arc_gap_pass_is_not_labelled_as_euclidean_pass():
    boundary = _rectangle()
    count = int(np.ceil(boundary.length / 1.94))
    plan = plan_coverage(
        boundary, None, None, count, None, 'equal_arc', 0.0, 256, 20,
        density_override=np.ones(len(boundary.deployment)),
    )
    report = pair_geometry(boundary, plan.arc_positions, 1.94, 0.75)
    assert report['arc_limit_ok']
    assert not report['euclidean_ok']
    assert report['worst_pair']['adjacent'] is False
    assert report['min_euclidean_m'] < 0.75
    label = classify_geometry(True, None, [report])
    status, stage = termination_for(label)
    assert label == 'arc_pass_euclidean_fail'
    assert status == 'PLAN_SEARCH_EXHAUSTED'
    assert stage == 'no_plan_found'
    assert status != 'SUCCESS'


def test_uniform_override_matches_the_gaussian_density_and_does_not_need_people():
    theta = np.linspace(0, 2 * np.pi, 128, endpoint=False)
    boundary = boundary_from_ring(np.column_stack([8 + 4 * np.cos(theta), 8 + 4 * np.sin(theta)]))
    people = np.array([[8.0, 8.0], [8.4, 8.2], [7.7, 8.3]])
    demand = np.array([1.0, 2.0, 1.5])
    density = _density(boundary.deployment, people, demand, 1.2, 'mass_cvt')
    normal = plan_coverage(boundary, people, demand, 8, 1.2, 'mass_cvt', 0.0, 256, 30)
    overridden = plan_coverage(
        boundary, None, None, 8, None, 'mass_cvt', 0.0, 256, 30, density_override=density,
    )
    np.testing.assert_allclose(normal.positions, overridden.positions, atol=1e-8)
    constant = plan_coverage(
        boundary, None, None, 8, None, 'mass_cvt', 0.0, 256, 30,
        density_override=np.ones(len(boundary.deployment)),
    )
    equal = plan_coverage(
        boundary, people, np.ones(len(people)), 8, 1.2, 'equal_arc', 0.0, 256, 30,
    )
    # Constant curve density is not the Gaussian image of unit person weights.
    gaussian_units = _density(
        boundary.deployment, people, np.ones(len(people)), 1.2, 'mass_cvt',
    )
    assert gaussian_units.max() / gaussian_units.min() > 1.01
    from udc.deployment.planners import periodic_gaps
    gaps = periodic_gaps(constant.arc_positions, boundary.length)
    np.testing.assert_allclose(gaps, periodic_gaps(equal.arc_positions, boundary.length), atol=1e-5)
    # A constant curve density has the same equal spacing. Its phase can sit
    # half a cell away from the Equal Arc origin; that is still the same pattern.


def test_planning_task_is_not_a_deployment_success_and_leaves_safety_empty(tmp_path):
    spec = load_spec(ROOT_SPEC)
    built = build_prescribed_curve('high_curvature_convex', 'mild', 15)
    task = {
        'family': 'high_curvature_convex', 'intensity': 'mild', 'tier': 'near',
        'seed': 15, 'method': 'equal_arc', 'count': 17, 'certificate': None,
        'curve_valid': True, 'length_m': float(built['boundary'].length),
        'points': built['points'],
        'count_rule': count_rule(built['boundary'].length, 1.94, 0.75),
        'output': str(tmp_path / 'task'),
        'task_sha256': 'test-task',
        'points_sha256': 'test-points',
        'planner': spec['planner'],
    }
    record = run_task(task)
    assert record['termination_status'] != 'SUCCESS'
    assert record['deployment_success'] is False
    assert record['evaluation']['people_evaluated'] is False
    assert record['evaluation']['path_evaluated'] is False
    assert record['evaluation']['path_length_m'] is None
    assert record['evaluation']['minimum_net_clearances_m'] is None
    assert record['evaluation']['safety_violation_count'] is None
    assert record['boundary_source'] == 'prescribed_diagnostic'


def test_frozen_catalog_rejects_used_seeds_and_keeps_slot_sides():
    with pytest.raises(ValueError):
        build_prescribed_curve('deep_notch', 'narrow', 4)
    with pytest.raises(ValueError):
        build_prescribed_curve('deep_notch', 'narrow', 5000)
    wide = build_prescribed_curve('deep_notch', 'wide', 15)
    narrow = build_prescribed_curve('deep_notch', 'narrow', 19)
    assert wide['report']['valid'] and narrow['report']['valid']
    assert wide['report']['measured_slot_width_m'] == pytest.approx(1.20, abs=1e-3)
    assert narrow['report']['measured_slot_width_m'] == pytest.approx(0.45, abs=1e-3)
    rule = count_rule(wide['boundary'].length, 1.94, 0.75)
    assert rule['near'] == 17
    assert rule['margin'] == 28
    assert rule['separation_upper_bound'] >= rule['margin']
    points, _ = stadium_ring(0.55, 32.0, 0.01)
    _, _, report, _ = prepare_curve(points, 0.05, 15, 0.04, 32.0)
    assert report['min_curvature_per_m'] < 0
