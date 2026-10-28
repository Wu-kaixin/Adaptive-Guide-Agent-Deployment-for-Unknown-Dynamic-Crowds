"""Contract tests for one-shot Step 2a allocation. No episodes are run."""
import ast
from pathlib import Path

import pytest

from udc.allocation import (
    BUDGET_SHORTFALL,
    FROZEN_FLOOR_VIOLATION,
    AllocationInputError,
    GroupResourceFloor,
    StaticGroup,
    allocate,
    floor_from_curve,
    validate_member_partition,
)
from udc.interfaces import Observation, ResourceAllocator
import inspect

ROOT = Path(__file__).resolve().parents[1]


def _group(name, members):
    return StaticGroup(name, tuple(members))


def _floor(name, minimum, policy='declared'):
    return GroupResourceFloor(name, minimum, policy)


def _counts(result):
    counts = {}
    for _guide, group_id in result.assignment:
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def _conserves(result, guides, floors):
    assert result.feasible is True and result.reason is None
    assigned = [guide for guide, _group_id in result.assignment]
    assert assigned == sorted(assigned)
    assert set(assigned) == set(guides)
    assert len(assigned) == len(guides)
    counts = _counts(result)
    for floor in floors:
        assert counts[floor.group_id] >= floor.minimum_guides
    assert sum(counts.values()) == len(guides)


def test_exact_budget_assigns_lowest_ids_in_group_id_order():
    groups = (_group('b', (4, 5)), _group('a', (1, 2, 3)))
    floors = (_floor('b', 1), _floor('a', 2))
    result = allocate(groups, floors, (30, 10, 20), strategy='floor_then_size')
    _conserves(result, (10, 20, 30), floors)
    assert result.assignment == ((10, 'a'), (20, 'a'), (30, 'b'))
    assert result.floor_policy == 'declared' and result.strategy == 'floor_then_size'


def test_shortfall_returns_no_assignment():
    groups = (_group('a', (1,)), _group('b', (2,)))
    floors = (_floor('a', 2), _floor('b', 2))
    result = allocate(groups, floors, (1, 2, 3), strategy='floor_then_size')
    assert result.feasible is False
    assert result.reason == BUDGET_SHORTFALL
    assert result.assignment == ()


def test_remainder_and_equal_size_tie_are_independent_of_input_order():
    groups = (_group('b', (3, 4)), _group('a', (1,)))
    floors = (_floor('a', 0), _floor('b', 0))
    guides = (8, 1, 5, 2, 4)
    forward = allocate(groups, floors, guides, strategy='floor_then_size')
    reverse = allocate(tuple(reversed(groups)), tuple(reversed(floors)), tuple(reversed(guides)),
                       strategy='floor_then_size')
    assert forward == reverse
    # 5*1//3 and 5*2//3 leave one seat; it goes to the larger group, b.
    assert _counts(forward) == {'a': 1, 'b': 4}
    assert forward.assignment == ((1, 'a'), (2, 'b'), (4, 'b'), (5, 'b'), (8, 'b'))
    equal_groups = (_group('b', (1, 2)), _group('a', (3, 4)))
    equal_floors = (_floor('a', 0), _floor('b', 0))
    tied = allocate(equal_groups, equal_floors, (9,), strategy='floor_then_size')
    again = allocate(equal_groups, equal_floors, (9,), strategy='floor_then_size')
    assert tied == again
    assert tied.assignment == ((9, 'a'),)


def test_noncontiguous_ids_and_spare_capacity_meet_every_floor():
    groups = (_group('west', (10, 30)), _group('east', (20,)))
    floors = (_floor('east', 1), _floor('west', 1))
    guides = (100, 4, 40)
    result = allocate(groups, floors, guides, strategy='floor_then_size')
    _conserves(result, guides, floors)
    assert _counts(result)['west'] == 2
    assert result.assignment == ((4, 'east'), (40, 'west'), (100, 'west'))


def test_rejected_requests_are_not_budget_shortfalls():
    groups = (_group('a', (1, 2)), _group('b', (3,)))
    floors = (_floor('a', 1), _floor('b', 1))
    with pytest.raises(AllocationInputError) as duplicate_guide:
        allocate(groups, floors, (1, 1), strategy='floor_then_size')
    assert duplicate_guide.value.code == 'DUPLICATE_GUIDE'
    with pytest.raises(AllocationInputError) as bad_floor:
        allocate(groups, (_floor('a', -1), _floor('b', 1)), (1,), strategy='floor_then_size')
    assert bad_floor.value.code == 'INVALID_FLOOR'
    with pytest.raises(AllocationInputError) as boolean_id:
        allocate(groups, floors, (1, True), strategy='floor_then_size')
    assert boolean_id.value.code == 'INVALID_ID'
    with pytest.raises(AllocationInputError) as overlap:
        allocate((_group('a', (1, 2)), _group('b', (2, 3))), floors, (7, 8), strategy='floor_then_size')
    assert overlap.value.code == 'OVERLAPPING_MEMBERS'
    with pytest.raises(AllocationInputError) as missing:
        allocate(groups, (_floor('a', 1),), (7, 8), strategy='floor_then_size')
    assert missing.value.code == 'MISSING_FLOOR'
    with pytest.raises(AllocationInputError) as unknown:
        allocate(groups, (_floor('a', 1), _floor('c', 1)), (7, 8), strategy='floor_then_size')
    assert unknown.value.code == 'UNKNOWN_GROUP'
    with pytest.raises(AllocationInputError) as unnamed:
        allocate(groups, floors, (7, 8), strategy='independent_ceil')
    assert unnamed.value.code == 'STRATEGY_NOT_IMPLEMENTED'


def test_curve_ceil_is_a_floor_policy_and_must_match_its_trace():
    recorded = floor_from_curve('a', 10, 3)
    other = _floor('b', 1)
    assert recorded.minimum_guides == 4
    assert recorded.curve_length_m == 10 and recorded.planning_gap_m == 3
    groups = (_group('a', (1, 2, 3, 4)), _group('b', (5,)))
    with pytest.raises(AllocationInputError) as mixed:
        allocate(groups, (recorded, other), (1, 2, 3, 4, 5), strategy='floor_then_size')
    assert mixed.value.code == 'MIXED_FLOOR_POLICY'
    matched = GroupResourceFloor('b', 2, 'curve_ceil', 3, 2)
    result = allocate(groups, (recorded, matched), (1, 2, 3, 4, 5, 6), strategy='floor_then_size')
    _conserves(result, (1, 2, 3, 4, 5, 6), (recorded, matched))
    assert result.floor_policy == 'curve_ceil'
    with pytest.raises(AllocationInputError) as mismatch:
        allocate(groups, (recorded, GroupResourceFloor('b', 1, 'curve_ceil', 3, 2)),
                 (1, 2, 3, 4, 5), strategy='floor_then_size')
    assert mismatch.value.code == 'FLOOR_TRACE_MISMATCH'


def test_frozen_mapping_accepts_a_sufficient_split_in_any_order():
    groups = (_group('b', (2,)), _group('a', (1, 3)))
    floors = (_floor('a', 1), _floor('b', 1))
    mapping = ((30, 'a'), (10, 'b'), (20, 'a'))
    result = allocate(tuple(reversed(groups)), tuple(reversed(floors)), (20, 30, 10),
                      strategy='frozen_initial_split', mapping=tuple(reversed(mapping)))
    assert result.feasible is True
    assert result.assignment == ((10, 'b'), (20, 'a'), (30, 'a'))
    assert allocate(groups, floors, (10, 20, 30), strategy='frozen_initial_split', mapping=mapping) == result


def test_sufficient_pool_with_a_low_frozen_group_is_not_a_shortfall():
    groups = (_group('a', (1, 2)), _group('b', (3, 4)))
    floors = (_floor('a', 2), _floor('b', 2))
    mapping = ((1, 'a'), (2, 'a'), (3, 'a'), (4, 'b'))
    result = allocate(groups, floors, (1, 2, 3, 4), strategy='frozen_initial_split', mapping=mapping)
    assert result.feasible is False
    assert result.reason == FROZEN_FLOOR_VIOLATION
    assert result.reason != BUDGET_SHORTFALL
    assert result.assignment == ()


def test_frozen_shortfall_and_broken_mappings_stay_distinct():
    groups = (_group('a', (1,)), _group('b', (2,)))
    floors = (_floor('a', 3), _floor('b', 3))
    mapping = ((1, 'a'), (2, 'a'), (3, 'b'), (4, 'b'))
    short = allocate(groups, floors, (1, 2, 3, 4), strategy='frozen_initial_split', mapping=mapping)
    assert short.reason == BUDGET_SHORTFALL and short.assignment == ()
    with pytest.raises(AllocationInputError) as missing:
        allocate(groups, (_floor('a', 1), _floor('b', 1)), (1, 2),
                 strategy='frozen_initial_split', mapping=((1, 'a'),))
    assert missing.value.code == 'MAPPING_INCOMPLETE'
    with pytest.raises(AllocationInputError) as repeated:
        allocate(groups, (_floor('a', 1), _floor('b', 1)), (1, 2),
                 strategy='frozen_initial_split', mapping=((1, 'a'), (1, 'b')))
    assert repeated.value.code == 'DUPLICATE_GUIDE'
    with pytest.raises(AllocationInputError) as unknown_guide:
        allocate(groups, (_floor('a', 1), _floor('b', 1)), (1, 2),
                 strategy='frozen_initial_split', mapping=((1, 'a'), (9, 'b')))
    assert unknown_guide.value.code == 'UNKNOWN_GUIDE'
    with pytest.raises(AllocationInputError) as unknown_group:
        allocate(groups, (_floor('a', 1), _floor('b', 1)), (1, 2),
                 strategy='frozen_initial_split', mapping=((1, 'a'), (2, 'c')))
    assert unknown_group.value.code == 'UNKNOWN_GROUP'


def test_member_partition_uses_frozen_ids_rather_than_observation_rows():
    population = (30, 10, 20)
    groups = (_group('a', (10, 30)), _group('b', (20,)))
    validate_member_partition(population, groups)
    validate_member_partition(tuple(reversed(population)), tuple(reversed(groups)))
    with pytest.raises(AllocationInputError) as incomplete:
        validate_member_partition((10, 20, 30, 40), groups)
    assert incomplete.value.code == 'INCOMPLETE_PARTITION'
    assert set(Observation.__dataclass_fields__) == {'positions', 'radii', 'demand'}
    signature = inspect.signature(ResourceAllocator.allocate)
    assert list(signature.parameters) == ['self', 'groups', 'guide_count']


def test_step1_modules_do_not_call_the_new_allocator():
    for relative in (
        'src/udc/experiments/runner.py',
        'src/udc/experiments/validation.py',
        'src/udc/controller/abcg.py',
        'src/udc/environment.py',
    ):
        source = (ROOT / relative).read_text(encoding='utf-8')
        assert 'udc.allocation' not in source
        tree = ast.parse(source)
        imported = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert all(node.module != 'udc.allocation' for node in imported)
    allocation_source = (ROOT / 'src/udc/allocation/allocate.py').read_text(encoding='utf-8')
    assert 'udc.deployment' not in allocation_source
    assert 'udc.navigation' not in allocation_source
    assert 'estimate_boundary' not in allocation_source
