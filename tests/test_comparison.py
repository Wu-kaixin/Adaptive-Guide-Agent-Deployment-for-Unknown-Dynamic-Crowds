"""Paired factor expansion for the Step 1 method comparison."""
from pathlib import Path

import pytest

from udc.environment import Environment
from udc.evaluation.acceptance import classify_outcome
from udc.analysis.summary import formal_projection_report, seed_cluster_report
from udc.experiments.comparison import RESERVED_VALIDATION, build_tasks, hotspot_tasks, load_spec
from udc.experiments.config import METHODS

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'experiments' / 'step1_fair_comparison.yaml'


def test_outcome_labels_keep_resource_planning_and_execution_apart():
    assert classify_outcome('SUCCESS') == 'success'
    assert classify_outcome('CAPACITY_SHORTFALL', 'planning') == 'resource_shortfall'
    assert classify_outcome('PLAN_SEARCH_EXHAUSTED', 'planning') == 'planning_failure'
    assert classify_outcome('PATH_UNREACHABLE', 'planning') == 'planning_failure'
    assert classify_outcome('REPLAN_EXHAUSTED', 'execution') == 'execution_failure'
    assert classify_outcome('TIMEOUT', 'execution') == 'execution_failure'
    assert classify_outcome('INITIALIZATION_INVALID', 'initialization') == 'initialization_failure'
    assert classify_outcome('NOT_EXECUTED') == 'not_executed'


def test_fair_grid_pairs_methods_and_reserves_validation_seeds():
    spec = load_spec(SPEC)
    tasks = build_tasks(spec, 'runs/unused')
    assert len(tasks) == 2 * 3 * 3 * 3 * 3 * len(METHODS) * 5
    assert {task['seed'] for task in tasks} == {0, 1, 2, 3, 4}
    assert RESERVED_VALIDATION.start == 5000
    with pytest.raises(ValueError, match='5000-5029'):
        build_tasks(spec, 'runs/unused', seeds=[5000])
    cell = [task for task in tasks if task['cell'] == 'square_circle_small_uniform_sufficient' and task['seed'] == 0]
    assert {task['method'] for task in cell} == METHODS
    controllers = []
    for task in cell:
        config = task['config']
        assert task['fixed_n'] == config['guides']['count']
        controllers.append(config['controller'])
        others = {key: value for key, value in config.items() if key != 'controller'}
        reference = {key: value for key, value in cell[0]['config'].items() if key != 'controller'}
        assert others == reference
    assert {item['method'] for item in controllers} == METHODS
    shared = [{key: value for key, value in item.items() if key != 'method'} for item in controllers]
    assert all(item == shared[0] for item in shared)


def test_resource_budgets_are_ordered_and_one_cell_initializes():
    spec = load_spec(SPEC)
    counts = {}
    for resource in ('severe', 'moderate', 'sufficient'):
        tasks = build_tasks(
            spec, 'runs/unused', seeds=[0], methods=['equal_arc'],
            selection={
                'environment': 'square', 'shape': 'circle', 'size': 'small',
                'demand': 'uniform', 'resource': resource,
            },
        )
        assert len(tasks) == 1
        counts[resource] = tasks[0]['fixed_n']
    assert counts['severe'] < counts['moderate'] < counts['sufficient']
    config = tasks[0]['config']
    env = Environment(config)
    assert len(env.observe().positions) == 20
    assert len(env.initialize_guides(config['guides'])) == counts['sufficient']


def test_confirmation_protocol_freezes_counts_and_seeds():
    spec = load_spec(ROOT / 'experiments' / 'step1_projection_confirmation.yaml')
    tasks = build_tasks(spec, 'runs/unused')
    assert len(tasks) == 2 * 3 * 3 * 3 * 10
    assert {task['seed'] for task in tasks} == set(range(5, 15))
    assert {task['method'] for task in tasks} == {'equal_arc', 'mass_cvt', 'projected_mass'}
    counts = {}
    for task in tasks:
        counts.setdefault(task['shape'], set()).add(task['fixed_n'])
        assert task['fixed_n'] == task['config']['guides']['count']
    assert counts == {'circle': {14, 17, 20}, 'ellipse': {16, 18, 22}}
    with pytest.raises(ValueError, match='5000-5029'):
        build_tasks(spec, 'runs/unused', seeds=[5005])


def test_formal_protocol_is_the_only_entry_for_reserved_seeds():
    spec = load_spec(ROOT / 'experiments' / 'step1_projection_formal.yaml')
    tasks = build_tasks(spec, 'runs/unused')
    assert len(tasks) == 2 * 3 * 3 * 3 * 30
    assert len({(task['cell'], task['seed'], task['method']) for task in tasks}) == 1620
    assert {task['seed'] for task in tasks} == set(range(5000, 5030))
    assert {task['method'] for task in tasks} == {'equal_arc', 'mass_cvt', 'projected_mass'}
    counts = {}
    for task in tasks:
        counts.setdefault(task['shape'], set()).add(task['fixed_n'])
    assert counts == {'circle': {14, 17, 20}, 'ellipse': {16, 18, 22}}
    subset = build_tasks(spec, 'runs/unused', seeds=[5000])
    assert len(subset) == 54
    with pytest.raises(ValueError, match='5000-5029'):
        build_tasks(spec, 'runs/unused', seeds=[4])
    developed = load_spec(ROOT / 'experiments' / 'step1_projection_ablation.yaml')
    with pytest.raises(ValueError, match='5000-5029'):
        build_tasks(developed, 'runs/unused', seeds=[5000])


def test_formal_case_rules_are_deterministic():
    rows = []
    for seed, projected_cost, projected_ok in ((5000, 0.4, True), (5001, 1.4, False)):
        for method, cost, success in (
            ('equal_arc', 1.0, True),
            ('projected_mass', projected_cost, projected_ok),
            ('mass_cvt', None, False),
        ):
            rows.append({
                'cell': 'square_circle_medium_strong_near', 'seed': seed, 'method': method,
                'shape': 'circle', 'demand': 'strong', 'resource': 'near', 'fixed_n': 14,
                'success': success, 'plan_found': success,
                'stage_diagnosis': 'accepted' if success else 'no_plan_found',
                'planned_demand_weighted_cost': cost, 'output': f'{method}-{seed}',
                'feasibility': {'necessary_condition_met': True},
                'criteria': {'safety_ok': True, 'gap_ok': True, 'curve_ok': True, 'coverage_ok': True},
            })
    report = formal_projection_report(rows, replicates=20, rng_seed=1)
    assert report['slice_counts']['projection_rescued_units'] == 1
    assert report['slice_counts']['projected_failed_or_worse_cost_units'] == 1
    assert report['cases']['worst_planned_cost_delta']['seed'] == 5001
    assert report['cases']['projected_failures_where_equal_arc_succeeded'][0]['seed'] == 5001


def test_seed_cluster_keeps_missing_metrics_out_of_the_mean():
    rows = []
    for seed in (5, 6):
        for method, cost, success in (
            ('equal_arc', 1.0, True),
            ('projected_mass', 0.5, True),
            ('mass_cvt', None, False),
        ):
            rows.append({
                'cell': 'square_circle_medium_uniform_near', 'seed': seed, 'method': method,
                'shape': 'circle', 'demand': 'uniform', 'resource': 'near',
                'success': success, 'plan_found': success, 'stage_diagnosis': 'accepted' if success else 'no_plan_found',
                'planned_demand_weighted_cost': cost,
                'feasibility': {'necessary_condition_met': True},
                'criteria': {'safety_ok': True, 'gap_ok': True, 'curve_ok': True, 'coverage_ok': True},
            })
    report = seed_cluster_report(rows, replicates=200, rng_seed=1)
    paired = report['paired']['projected_mass']['planned_demand_weighted_cost']
    assert paired['pairs_defined'] == 2
    assert paired['better'] == 2
    assert report['paired']['mass_cvt']['planned_demand_weighted_cost']['pairs_defined'] == 0
    assert report['methods']['mass_cvt']['successful'] == 0
    assert report['methods']['mass_cvt']['tasks'] == 2
    assert report['constraints']['safety_violations'] == 0
    assert report['necessary_condition_pairs']['met'] == 2


def test_hotspot_reproduction_lists_both_guide_budgets():
    tasks = hotspot_tasks('runs/unused', ROOT / 'configs/step1/scenarios/high_demand_side.yaml')
    assert {(task['fixed_n'], task['method']) for task in tasks} == {
        (n, method) for n in (20, 24) for method in METHODS
    }
