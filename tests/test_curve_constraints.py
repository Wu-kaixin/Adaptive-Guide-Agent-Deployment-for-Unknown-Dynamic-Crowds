"""Adhesion handoff, pairwise acceptance, and disk-union isolation."""
import inspect

import numpy as np
from shapely.geometry import Point, Polygon

from udc.boundary import estimate_boundary, estimate_disk_union
from udc.boundary.prescribed import boundary_from_ring, build_prescribed_curve, curve_report
from udc.controller.abcg import ABCGController
from udc.deployment.constrained import (
    constant_quadrature, optimize_pairwise, planning_outcome, rotate_sites,
)
from udc.deployment.planners import _interpolate, periodic_gaps, plan_coverage
from udc.environment import Environment
from udc.evaluation.acceptance import evaluate
from udc.experiments.config import load_config
from udc.experiments.curve_constraints import load_spec
from udc.experiments.curve_diagnostics import positions_from_arc
from udc.experiments.scene_closure import scene_people
from udc.safety import Safety


def _shallow(shift=True):
    built = build_prescribed_curve('shallow_concave', 'strong', 15)
    boundary = built['boundary']
    if shift:
        centre = boundary.deployment.mean(axis=0)
        moved = boundary.deployment - centre + np.array([20.0, 20.0])
        boundary = boundary_from_ring(moved)
    return boundary


def _curve_distance(boundary, points):
    line = Polygon(boundary.deployment).exterior
    return np.array([line.distance(Point(point)) for point in np.asarray(points, dtype=float)])


def test_full_precision_targets_lie_on_the_curve_and_six_decimals_do_not():
    boundary = _shallow(False)
    plan = plan_coverage(
        boundary, None, None, 17, None, 'equal_arc', 0.0, 2048, 10,
        density_override=np.ones(len(boundary.deployment)),
    )
    full = _curve_distance(boundary, plan.positions)
    rounded = _curve_distance(boundary, np.round(plan.positions, 6))
    rebuilt, _ = positions_from_arc(boundary, np.round(plan.arc_positions, 6))
    assert full.max() <= 1e-7
    assert rounded.max() > 1e-7
    assert _curve_distance(boundary, rebuilt).max() <= 1e-7


def test_saved_float64_round_trip_and_arc_projection(tmp_path):
    boundary = _shallow(False)
    sites = (np.arange(12) + 0.5) * boundary.length / 12
    points = _interpolate(boundary, sites)
    path = tmp_path / 'targets.npz'
    np.savez(path, targets=points, arc=sites)
    loaded = np.load(path)
    assert loaded['targets'].dtype == np.float64
    np.testing.assert_array_equal(loaded['targets'], points)
    line = Polygon(boundary.deployment).exterior
    projected = np.array([line.project(Point(point)) for point in points])
    error = ((projected - sites + boundary.length / 2) % boundary.length) - boundary.length / 2
    assert np.max(np.abs(error)) < 1e-8
    seam = 0.5 * (boundary.deployment[-1] + boundary.deployment[0])
    assert line.distance(Point(seam)) == 0.0


def test_evaluator_does_not_snap_or_zero_an_off_curve_target():
    boundary = _shallow()
    plan = plan_coverage(
        boundary, None, None, 17, None, 'equal_arc', 0.0, 2048, 10,
        density_override=np.ones(len(boundary.deployment)),
    )
    targets = np.array(plan.positions, copy=True)
    centre = boundary.deployment.mean(axis=0)
    direction = targets[0] - centre
    direction /= np.linalg.norm(direction)
    targets[0] = targets[0] + direction * 1e-6
    distance = _curve_distance(boundary, targets)
    assert 1e-7 < distance.max() < 5e-6
    trajectory = np.stack([targets, targets])
    criteria, _ = evaluate(
        trajectory, np.zeros((1, 17, 2)), np.ones(17, dtype=bool), targets, np.arange(17),
        boundary, Safety(0.75, 0.75, 0.55, 1.0, 1e-7),
        {'hold_steps': 1, 'position_tolerance': 0.03, 'speed_tolerance': 0.03,
         'curve_tolerance': 0.04, 'max_gap_limit': 2.0, 'arc_tolerance': 0.03,
         '_people': np.array([[20.0, 20.0]]), '_radii': np.array([0.2]),
         '_room': np.array([40.0, 40.0])},
    )
    assert criteria['plan_ok'] is False
    assert criteria['curve_ok'] is True


def test_phase_is_free_and_solver_success_is_not_acceptance():
    boundary = _shallow(False)
    base = (np.arange(8) + 0.2) * boundary.length / 8
    rotated = rotate_sites(base, 0.37 * boundary.length, boundary.length)
    assert not np.allclose(np.sort(base), rotated)
    np.testing.assert_allclose(
        np.sort(periodic_gaps(np.sort(base), boundary.length)),
        np.sort(periodic_gaps(rotated, boundary.length)),
    )
    assert 'solver_success' not in inspect.signature(planning_outcome).parameters
    assert planning_outcome(False, None) == 'no_plan_found'
    assert planning_outcome(True, None) == 'feasible_witness'
    assert planning_outcome(False, 'GAP_BOUND') == 'certified_infeasible'


def test_pairwise_keeps_a_feasible_start_without_calling_it_optimal():
    theta = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    radius = 16 / (2 * np.pi)
    points = np.column_stack([10 + radius * np.cos(theta), 10 + radius * np.sin(theta)])
    boundary = boundary_from_ring(points)
    quadrature, mass, _ = constant_quadrature(boundary, 256)
    count = 8
    sites = np.arange(count) * boundary.length / count
    result = optimize_pairwise(
        boundary, [('equal_arc_phase0', sites)], quadrature, mass, 3.0, 0.5, maxiter=3, timeout=5,
    )
    assert result['global_optimum'] is False
    assert result['accepted'] is True
    assert result['outcome'] == 'feasible_witness'
    assert result['plan'].max_gap <= 3.0 + 1e-8
    gaps = periodic_gaps(result['plan'].arc_positions, boundary.length)
    assert gaps.min() + 1e-9 >= 0.5
    assert result['unevaluated']['path'] is False


def test_disk_union_keeps_a_notch_the_convex_hull_removes():
    scene = {
        'kind': 'u', 'origin': [18.0, 18.0], 'arm_spacing': 0.55,
        'arm_length': 3.3, 'body_gap': 4.0,
    }
    points, radii = scene_people(scene, 0.2)
    room = np.array([50.0, 50.0])
    convex = estimate_boundary(points, 0.80, 0.1, room, 0.55, 0.75, radii)
    union = estimate_disk_union(
        points, 0.80, 0.1, room, 0.55, 0.75, radii, closing_radius=0.30, quad_segs=8,
    )
    convex_report = curve_report(convex.deployment)
    union_report = curve_report(union.deployment)
    assert convex_report['valid'] and union_report['valid']
    assert convex_report['convexity_defect'] < 1e-3
    assert union_report['convexity_defect'] > 0.02
    config = load_config('configs/step1/static_50.yaml')
    env = Environment(config)
    observation = env.observe()
    guides = env.initialize_guides(config['guides'])
    control = config['controller']
    safety = Safety(0.75, 0.75, 0.55, 1.0, 1e-7)
    controller = ABCGController(control, safety, env.scene.size, 'fixed_n', len(guides))
    controller.prepare(observation, guides)
    assert controller.boundary_source == 'estimated_convex_offset'
    controller.prepare_prescribed(observation, guides, union, boundary_source='estimated_disk_union')
    assert controller.boundary_source == 'estimated_disk_union'
    assert controller.prescribed_plan_installed is False


def test_constraint_spec_locks_the_twelve_units_and_the_known_conflict():
    spec = load_spec('experiments/step1_curve_constraints.yaml')
    assert len(spec['units']) == 12
    narrow_near = [
        unit for unit in spec['units']
        if unit['family'] == 'deep_notch' and unit['intensity'] == 'narrow' and unit['tier'] == 'near'
    ]
    assert narrow_near == [{'family': 'deep_notch', 'intensity': 'narrow', 'tier': 'near', 'seed': 18}]
    assert spec['solver_maxiter'] == 40
    assert spec['per_start_timeout_s'] == 20
