"""Paired factor expansion for the Step 1 method comparison."""
from pathlib import Path

import pytest

from udc.environment import Environment
from udc.evaluation.acceptance import classify_outcome
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


def test_fair_grid_pairs_methods_and_reserves_validation_seeds():
    spec = load_spec(SPEC)
    tasks = build_tasks(spec, 'runs/unused')
    assert len(tasks) == 2 * 3 * 3 * 3 * 3 * 4 * 5
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


def test_hotspot_reproduction_lists_both_guide_budgets():
    tasks = hotspot_tasks('runs/unused', ROOT / 'configs/step1/scenarios/high_demand_side.yaml')
    assert {(task['fixed_n'], task['method']) for task in tasks} == {
        (n, method) for n in (20, 24) for method in METHODS
    }
