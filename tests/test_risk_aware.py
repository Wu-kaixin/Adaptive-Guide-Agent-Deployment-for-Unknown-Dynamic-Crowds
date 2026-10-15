"""Demand mapping, feasibility certificates, RA-ABDC bookkeeping and stage labels."""
from pathlib import Path

import numpy as np
import pytest
import yaml

from udc.boundary import Boundary, estimate_boundary
from udc.deployment import (demand_profile, feasibility_bounds, infeasibility_certificate,
                            periodic_gaps, plan_coverage, plan_projected_mass, plan_risk_aware,
                            project_gaps, quadrature_mass)
from udc.deployment import risk_aware
from udc.environment import Environment
from udc.evaluation.acceptance import diagnose_stages
from udc.evaluation.coverage import coverage_diagnostics, distmesh_gap_prediction, hotspot_window
from udc.experiments.comparison import build_tasks, load_spec
from udc.experiments.config import demand_kind, load_config
from udc.experiments.runner import experiment, reevaluate_result

ROOT = Path(__file__).resolve().parents[1]


def circle_boundary(n=256, radius=4.):
    theta = np.arange(n)*2*np.pi/n
    points = np.c_[10+radius*np.cos(theta), 10+radius*np.sin(theta)]
    lengths = np.linalg.norm(np.roll(points, -1, axis=0)-points, axis=1)
    return Boundary(points.copy(), points, np.r_[0., np.cumsum(lengths[:-1])], float(lengths.sum()))


def write(tmp_path, cfg, name='case.yaml'):
    path = tmp_path/name
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
    return path


def test_gap_projection_keeps_sum_and_box_and_reports_empty_box():
    gaps = np.array([2.5, 0.5, 1.0, 1.0, 1.0])
    projected = project_gaps(gaps, .75, 1.94, gaps.sum())
    assert projected.sum() == pytest.approx(gaps.sum())
    assert np.all(projected >= .75-1e-12) and np.all(projected <= 1.94+1e-12)
    assert projected[0] == pytest.approx(1.94) and projected[1] == pytest.approx(.75)
    inside = np.array([1.2, 1.3, 1.1, 1.4])
    np.testing.assert_allclose(project_gaps(inside, .75, 1.94, inside.sum()), inside)
    assert project_gaps(np.ones(4), .75, 1.94, 10.) is None
    assert project_gaps(np.ones(4), 3., 4., 4.) is None


def test_necessary_count_conditions_are_certificates_not_feasibility_proofs():
    bounds = feasibility_bounds(26.4, 1.94, .75)
    assert bounds['necessary_min_count'] == 14 and bounds['necessary_max_count'] == 35
    assert infeasibility_certificate(bounds, 13) == 'GAP_BOUND'
    assert infeasibility_certificate(bounds, 36) == 'SEPARATION_BOUND'
    assert infeasibility_certificate(bounds, 14) is None
    with pytest.raises(ValueError):
        feasibility_bounds(26.4, 0., .75)


def test_certified_infeasible_fixed_n_keeps_status_and_adds_stage_label(tmp_path):
    cfg = load_config(ROOT/'configs/step1/square.yaml')
    result = experiment(write(tmp_path, cfg), tmp_path/'n12', plots=False,
                        protocol='fixed_n', fixed_n=12, method='equal_arc')
    assert result['termination_status'] == 'PLAN_SEARCH_EXHAUSTED'
    assert result['stage_diagnosis'] == 'certified_infeasible'
    assert result['feasibility']['infeasibility_certificate'] == 'GAP_BOUND'
    assert result['feasibility']['necessary_min_count'] > 12
    assert not result['plan_found'] and not result['assignment_found']
    assert 'necessary condition violated' in result['failure_reason']


def test_stage_labels_separate_resource_certificate_search_assignment_execution():
    assert diagnose_stages('SUCCESS', None, None, True, True) == 'accepted'
    assert diagnose_stages('CAPACITY_SHORTFALL', 'planning', None, False, False) == 'resource_shortfall'
    cert = {'infeasibility_certificate': 'GAP_BOUND'}
    assert diagnose_stages('PLAN_SEARCH_EXHAUSTED', 'planning', cert, False, False) == 'certified_infeasible'
    clear = {'infeasibility_certificate': None}
    assert diagnose_stages('PLAN_SEARCH_EXHAUSTED', 'planning', clear, False, False) == 'no_plan_found'
    assert diagnose_stages('PATH_UNREACHABLE', 'planning', clear, True, False) == 'plan_found_assignment_failed'
    assert diagnose_stages('TIMEOUT', 'execution', clear, True, True) == 'plan_found_execution_failed'
    assert diagnose_stages('OFFSET_INVALID', 'planning', None, False, False) == 'geometry_invalid'
    assert diagnose_stages('INITIALIZATION_INVALID', 'initialization', None, False, False) == 'not_evaluated'


def hotspot_case():
    rng = np.random.default_rng(3)
    theta = rng.uniform(0, 2*np.pi, 60)
    radius = 3*np.sqrt(rng.uniform(0, 1, 60))
    people = np.c_[10+radius*np.cos(theta), 10+radius*np.sin(theta)]
    mask = people[:, 0] > 11.5
    demand = np.where(mask, 3., 1.)
    boundary = estimate_boundary(people, 1.3, .1, np.array([20., 20.]), .55, .78, np.full(60, .2))
    return boundary, people, demand, mask


def test_ra_abdc_respects_gap_limit_where_mass_cvt_breaks_it_and_concentrates_versus_equal_arc():
    boundary, people, demand, mask = hotspot_case()
    limit, separation = 1.94, .75
    count = feasibility_bounds(boundary.length, limit, separation)['necessary_min_count']+1
    mass = plan_coverage(boundary, people, demand, count, 1.2, 'mass_cvt')
    equal = plan_coverage(boundary, people, demand, count, 1.2, 'equal_arc')
    risk = plan_risk_aware(boundary, people, demand, count, 1.2, limit, separation)
    assert mass.max_gap > limit
    assert risk.max_gap <= limit+1e-9
    assert periodic_gaps(risk.arc_positions, boundary.length).min() >= separation-1e-9
    assert risk.concentration == 1.0 and not risk.equal_arc_fallback
    assert len(risk.adjustments) == 1 and risk.adjustments[0]['accepted']
    diag = coverage_diagnostics(boundary, people, demand, mask, 1.2, 2048, 2.0,
                                risk.positions, equal.positions)
    assert diag['hotspot_arc_fraction'] > 0 and diag['hotspot_demand_share'] > diag['hotspot_arc_fraction']
    assert diag['actual_hotspot_concentration'] == pytest.approx(1.0, abs=.05)
    assert diag['planned_hotspot_concentration'] > 1.03
    assert diag['planned_demand_weighted_cost'] < diag['actual_demand_weighted_cost']
    # The gap cap costs concentration: the rejected unconstrained plan is denser in the window.
    unconstrained = coverage_diagnostics(boundary, people, demand, mask, 1.2, 2048, 2.0,
                                         mass.positions, mass.positions)
    assert unconstrained['planned_hotspot_concentration'] > diag['planned_hotspot_concentration']


def test_uniform_person_demand_still_has_a_nonuniform_curve_density():
    boundary, people, _, _ = hotspot_case()
    demand = np.ones(len(people))
    rho = demand_profile(boundary, people, demand, 1.2, 'mass_cvt')
    assert np.allclose(demand, 1.0)
    assert rho.max() / rho.min() > 1.2


def test_evaluation_density_ignores_the_planning_exponent():
    boundary, people, demand, mask = hotspot_case()
    rho = demand_profile(boundary, people, demand, 1.2, 'mass_cvt')
    sites = (np.arange(12) + 0.5) * boundary.length / 12
    positions_even = boundary.deployment[np.linspace(0, len(boundary.deployment) - 1, 12).astype(int)]
    original = coverage_diagnostics(boundary, people, demand, mask, 1.2, 512, 2.0,
                                    positions_even, positions_even)
    peaked = quadrature_mass(boundary, rho ** 0.25, 512)[1]
    flat = quadrature_mass(boundary, rho, 512)[1]
    assert not np.allclose(peaked / peaked.sum(), flat / flat.sum())
    assert original['planned_demand_weighted_cost'] == pytest.approx(
        coverage_diagnostics(boundary, people, demand, mask, 1.2, 512, 2.0,
                             positions_even, positions_even)['planned_demand_weighted_cost'])
    assert original['coverage_density'].startswith('mass_cvt')


def test_projected_mass_matches_ra_abdc_when_unit_concentration_is_feasible():
    boundary, people, demand, _ = hotspot_case()
    limit, separation = 1.94, .75
    count = feasibility_bounds(boundary.length, limit, separation)['necessary_min_count'] + 1
    risk = plan_risk_aware(boundary, people, demand, count, 1.2, limit, separation)
    projected = plan_projected_mass(boundary, people, demand, count, 1.2, limit, separation)
    np.testing.assert_allclose(projected.arc_positions, risk.arc_positions, atol=1e-8)
    assert risk.concentration == 1.0 and projected.concentration == 1.0
    assert projected.adapt_concentration is False and risk.adapt_concentration is True
    assert len(projected.adjustments) == 1
    assert projected.adjustments[0]['same_path_as_ra_abdc_unit_concentration']
    assert projected.adjustments[0]['before_projection']['max_gap'] >= projected.max_gap - 1e-9
    assert projected.adjustments[0]['original_density_contrast'] > 1
    assert projected.adjustments[0]['evaluation_density'].startswith('original')


def test_projected_mass_does_not_fall_back_when_separation_fails(monkeypatch):
    boundary, people, demand, _ = hotspot_case()
    original = risk_aware._min_separation

    def always_too_close(bnd, sites):
        points, _ = original(bnd, sites)
        return points, 0.1
    monkeypatch.setattr(risk_aware, '_min_separation', always_too_close)
    projected = plan_projected_mass(boundary, people, demand, 17, 1.2, 1.94, .75)
    assert len(projected.adjustments) == 1
    assert projected.concentration == 1.0 and not projected.equal_arc_fallback
    assert not projected.adjustments[0]['accepted']
    assert projected.adjustments[0]['fallback_reason'] is None


def test_ra_abdc_reduces_concentration_and_records_equal_arc_fallback(monkeypatch):
    boundary, people, demand, _ = hotspot_case()
    original = risk_aware._min_separation

    def violated_unless_uniform(bnd, sites):
        points, value = original(bnd, sites)
        gaps = periodic_gaps(sites, bnd.length)
        return points, (value if np.ptp(gaps) < 1e-6 else 0.1)
    monkeypatch.setattr(risk_aware, '_min_separation', violated_unless_uniform)
    plan = plan_risk_aware(boundary, people, demand, 17, 1.2, 1.94, .75)
    schedule = [item['concentration'] for item in plan.adjustments]
    assert schedule[0] == 1.0 and schedule[-1] == 0.0 and all(np.diff(schedule) < 0)
    assert all(not item['accepted'] for item in plan.adjustments[:-1])
    assert plan.equal_arc_fallback and plan.concentration == 0.0
    assert np.ptp(periodic_gaps(plan.arc_positions, boundary.length)) < 1e-6


def test_ra_abdc_records_one_attempt_when_the_gap_box_is_empty():
    boundary, people, demand, _ = hotspot_case()
    plan = plan_risk_aware(boundary, people, demand, 5, 1.2, 1.94, .75)
    assert len(plan.adjustments) == 1
    assert plan.adjustments[0]['gap_projection'] == 'infeasible_box'
    assert plan.max_gap > 1.94 and not plan.equal_arc_fallback


def test_demand_kinds_hotspot_mask_and_clipping_are_frozen_observables():
    cfg = load_config(ROOT/'configs/step1/static_50.yaml')
    cfg['crowd']['demand'] = {'mean': 1.0, 'std': 0.0, 'min': 1.0, 'max': 2.5}
    assert demand_kind(cfg['crowd']) == 'uniform'
    env = Environment(cfg)
    assert not env.hotspot_mask.any() and np.all(env.observe().demand == 1.0)
    cfg['crowd']['demand_region'] = {'center': [20.0, 19.5], 'radius': 1.2, 'multiplier': 3.0}
    assert demand_kind(cfg['crowd']) == 'hotspot'
    env = Environment(cfg)
    mask = env.hotspot_mask
    assert 0 < mask.sum() < 50
    demand = env.observe().demand
    assert np.all(demand[mask] == 2.5) and np.all(demand[~mask] == 1.0)
    assert env.demand_clipped == int(mask.sum())
    with pytest.raises(ValueError):
        mask_view = env._hotspot_mask
        mask_view[0] = True
    cfg['crowd']['demand'] = {'mean': 1.0, 'std': 0.1, 'min': 0.5, 'max': 3.0}
    assert demand_kind(cfg['crowd']) == 'hotspot_on_independent'
    del cfg['crowd']['demand_region']
    assert demand_kind(cfg['crowd']) == 'independent'


def test_hotspot_window_follows_nearest_person_and_fractional_counts():
    boundary = circle_boundary()
    people = np.array([[14., 10.], [6., 10.]])
    mask = np.array([True, False])
    quadrature, _, length = quadrature_mass(boundary, np.ones(len(boundary.arc)), 1024)
    window = hotspot_window(boundary, quadrature, people, mask)
    assert window.mean() == pytest.approx(.5, abs=.01)
    assert hotspot_window(boundary, quadrature, people, np.array([True, True])) is None
    even = boundary.deployment[::32]
    diag = coverage_diagnostics(boundary, people, np.array([3., 1.]), mask, 1., 1024, 4.,
                                even, even)
    assert diag['actual_hotspot_concentration'] == pytest.approx(1.0, abs=.02)
    right = boundary.deployment[np.r_[0:40:8, 216:256:8, 128]]
    diag = coverage_diagnostics(boundary, people, np.array([3., 1.]), mask, 1., 1024, 4.,
                                right, right)
    assert diag['actual_hotspot_concentration'] > 1.5
    assert diag['actual_hotspot_mean_spacing'] < length/len(right)


def test_ablation_spec_uses_curve_based_counts_and_shared_hotspot_geometry():
    spec = load_spec(ROOT/'experiments/step1_projection_ablation.yaml')
    tasks = build_tasks(spec, 'runs/unused')
    assert len(tasks) == 2*3*3*4*5
    counts = {(task['shape'], task['resource']): task['fixed_n'] for task in tasks}
    assert counts[('circle', 'near')] == 14 and counts[('circle', 'ample')] == 20
    assert counts[('ellipse', 'near')] == 16 and counts[('ellipse', 'ample')] == 22
    assert counts[('circle', 'near')] < counts[('circle', 'margin')] < counts[('circle', 'ample')]
    weak = next(task for task in tasks if task['shape'] == 'ellipse' and task['demand'] == 'weak')
    strong = next(task for task in tasks if task['shape'] == 'ellipse' and task['demand'] == 'strong'
                  and task['seed'] == weak['seed'] and task['resource'] == weak['resource']
                  and task['method'] == weak['method'])
    assert weak['config']['crowd']['demand_region']['radius'] == strong['config']['crowd']['demand_region']['radius']
    assert weak['config']['crowd']['demand_region']['center'] == strong['config']['crowd']['demand_region']['center']
    assert strong['config']['crowd']['demand_region']['multiplier'] > weak['config']['crowd']['demand_region']['multiplier']
    by_cell = {}
    for task in tasks:
        if task['seed'] == 0:
            by_cell.setdefault(task['cell'], []).append(task)
    for members in by_cell.values():
        reference = {key: value for key, value in members[0]['config'].items() if key != 'controller'}
        assert all({key: value for key, value in member['config'].items() if key != 'controller'} == reference
                   for member in members)
        assert {member['method'] for member in members} == {
            'equal_arc', 'mass_cvt', 'projected_mass', 'ra_abdc'}


def test_plan_only_then_resume_executes_without_changing_the_task(tmp_path):
    output = tmp_path/'planned'
    planned = experiment(ROOT/'configs/step1/square.yaml', output, plots=False,
                         protocol='fixed_n', fixed_n=20, method='equal_arc', execute=False)
    assert planned['termination_status'] == 'NOT_EXECUTED'
    assert planned['stage_diagnosis'] == 'not_executed' and planned['plan_found']
    repeated = experiment(ROOT/'configs/step1/square.yaml', output, plots=False,
                          protocol='fixed_n', fixed_n=20, method='equal_arc',
                          execute=False, resume=True)
    assert repeated['task_sha256'] == planned['task_sha256']
    done = experiment(ROOT/'configs/step1/square.yaml', output, plots=False,
                      protocol='fixed_n', fixed_n=20, method='equal_arc', resume=True)
    assert done['success'] and done['task_sha256'] == planned['task_sha256']
    assert done['stage_diagnosis'] == 'accepted'


def test_pilot_spec_pairs_five_methods_shares_inputs_and_shifts_the_hotspot():
    spec = load_spec(ROOT/'experiments/step1_hotspot_pilot.yaml')
    tasks = build_tasks(spec, 'runs/unused', seeds=[0])
    assert len(tasks) == 2*3*5
    assert {task['fixed_n'] for task in tasks} == {14, 17, 19}
    by_cell = {}
    for task in tasks:
        by_cell.setdefault(task['cell'], []).append(task)
    for cell, members in by_cell.items():
        reference = {k: v for k, v in members[0]['config'].items() if k != 'controller'}
        assert all({k: v for k, v in m['config'].items() if k != 'controller'} == reference for m in members)
        assert {m['method'] for m in members} == {'equal_arc', 'average_cvt', 'mass_cvt', 'distmesh', 'ra_abdc'}
        tight = members[0]['config']['controller']
        assert tight['max_gap_limit'] == 2.0 and tight['arc_tolerance'] == 0.03
    hot = next(t for t in tasks if t['demand'] == 'hotspot' and t['resource'] == 'moderate')
    region = hot['config']['crowd']['demand_region']
    assert region['center'][0] > hot['config']['scene']['width']/2
    env = Environment(hot['config'])
    assert 0 < env.hotspot_mask.sum() < 50 and env.observe().demand.max() == 3.0
    uniform = next(t for t in tasks if t['demand'] == 'uniform' and t['resource'] == 'moderate')
    other = Environment(uniform['config'])
    np.testing.assert_array_equal(env.observe().positions, other.observe().positions)
    assert np.all(other.observe().demand == 1.0)


def test_distmesh_gap_follows_sqrt_density_and_has_no_cap():
    boundary, people, demand, _ = hotspot_case()
    limit, count = 1.94, 17
    plan = plan_coverage(boundary, people, demand, count, 1.2, 'distmesh')
    equal = plan_coverage(boundary, people, demand, count, 1.2, 'equal_arc')
    prediction = distmesh_gap_prediction(boundary, people, demand, 1.2, 2048, count, limit)
    assert plan.max_gap > equal.max_gap
    assert prediction['predicted_max_gap'] > limit
    assert prediction['density_contrast'] > 1.5
    assert plan.max_gap == pytest.approx(prediction['predicted_max_gap'], rel=0.2)


def test_spatial_hotspot_scenario_freezes_a_side_disk(tmp_path):
    cfg = load_config(ROOT/'configs/step1/scenarios/spatial_hotspot.yaml')
    assert demand_kind(cfg['crowd']) == 'hotspot'
    env = Environment(cfg)
    demand = env.observe().demand
    assert env.hotspot_mask.any() and not env.hotspot_mask.all()
    assert demand.max() == 3.0 and demand.min() == 1.0
    assert cfg['crowd']['demand_region']['center'][0] > cfg['scene']['width']/2


def test_ra_abdc_run_saves_adjustments_and_reevaluation_matches_online_diagnostics(tmp_path):
    cfg = load_config(ROOT/'configs/step1/square.yaml')
    cfg['crowd']['demand'] = {'mean': 1.0, 'std': 0.0, 'min': 1.0, 'max': 3.0}
    cfg['crowd']['demand_region'] = {'center': [13.5, 10.0], 'radius': 1.6, 'multiplier': 3.0}
    output = tmp_path/'ra'
    metrics = experiment(write(tmp_path, cfg), output, plots=False,
                         protocol='fixed_n', fixed_n=23, method='ra_abdc')
    assert metrics['method'] == 'ra_abdc' and metrics['demand_kind'] == 'hotspot'
    assert metrics['planned_concentration'] is not None and metrics['equal_arc_fallback'] is not None
    assert metrics['planning_attempts'][-1]['adjustments']
    assert metrics['planned_max_gap'] <= cfg['controller']['max_gap_limit']-2*cfg['controller']['arc_tolerance']+1e-9
    assert metrics['feasibility']['necessary_condition_met']
    if metrics['success']:
        assert metrics['stage_diagnosis'] == 'accepted'
        assert metrics['actual_hotspot_concentration'] is not None
    rerun = reevaluate_result(output)
    assert rerun['success'] == metrics['success'] and rerun['criteria_consistent']
    assert all(rerun['diagnostics_consistent'].values())
