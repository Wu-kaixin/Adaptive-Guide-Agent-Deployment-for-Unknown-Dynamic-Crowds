"""Validate a two-group request and assign each guide identity once.

The allocator reads membership and floors only. It does not load spawn
polygons, observations, or deployment planners. ``independent_ceil`` is not a
strategy and does not fall back to ``floor_then_size``.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from .types import Allocation, GroupResourceFloor, StaticGroup

BUDGET_SHORTFALL = 'BUDGET_SHORTFALL'
FROZEN_FLOOR_VIOLATION = 'FROZEN_FLOOR_VIOLATION'
_STRATEGIES = frozenset({'floor_then_size', 'frozen_initial_split'})
_POLICIES = frozenset({'declared', 'curve_ceil'})


class AllocationInputError(ValueError):
    """The request is not a well-formed allocation problem."""

    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code


def floor_from_curve(group_id, curve_length_m, planning_gap_m):
    """Record ``ceil(L / H_p)`` together with the two lengths that produced it."""
    _group_id(group_id)
    length = _positive_length(curve_length_m, 'curve length')
    gap = _positive_length(planning_gap_m, 'planning gap')
    minimum = math.ceil(length / gap)
    return GroupResourceFloor(
        group_id, int(minimum), 'curve_ceil', float(length), float(gap),
    )


def validate_member_partition(population_ids, groups):
    """Check that groups partition the frozen person-ID list.

    The experiment harness owns this check. ``allocate`` does not receive the
    population, because ``Observation`` has no person IDs. Row ``k`` of a
    frozen ``PedestrianState`` belongs to ``int(ids[k])``.
    """
    population = _id_tuple(population_ids, 'population id')
    if len(population) != len(set(population)):
        raise AllocationInputError('DUPLICATE_POPULATION_ID', 'frozen person IDs must be unique')
    seen = []
    for group in _groups(groups):
        seen.extend(group.member_ids)
    if len(seen) != len(set(seen)):
        raise AllocationInputError('OVERLAPPING_MEMBERS', 'group member IDs overlap')
    if set(seen) != set(population):
        raise AllocationInputError(
            'INCOMPLETE_PARTITION',
            'group members must be exactly the frozen person IDs',
        )


def allocate(groups, floors, guides, *, strategy, mapping=None):
    """Assign the given guide IDs, or return one explicit scientific refusal.

    Input errors are raised. ``BUDGET_SHORTFALL`` and ``FROZEN_FLOOR_VIOLATION``
    are returned with an empty assignment and ``feasible=False``.
    """
    if type(strategy) is not str or strategy not in _STRATEGIES:
        raise AllocationInputError(
            'STRATEGY_NOT_IMPLEMENTED',
            'only floor_then_size and frozen_initial_split are implemented; '
            'independent_ceil is a floor policy, not a strategy, and there is no fallback',
        )
    group_list = _groups(groups)
    floor_list = _floors(floors, group_list)
    guide_ids = _id_tuple(guides, 'guide id')
    if len(guide_ids) != len(set(guide_ids)):
        raise AllocationInputError('DUPLICATE_GUIDE', 'guide IDs must be unique')
    policy = _single_policy(floor_list)
    floors_by_group = {item.group_id: item.minimum_guides for item in floor_list}
    parsed_mapping = None
    if strategy == 'frozen_initial_split':
        if mapping is None:
            raise AllocationInputError('MAPPING_REQUIRED', 'frozen_initial_split requires a guide-to-group mapping')
        parsed_mapping = _mapping(mapping, guide_ids, {group.group_id for group in group_list})
    elif mapping is not None:
        raise AllocationInputError('MAPPING_UNEXPECTED', 'floor_then_size does not take a frozen mapping')
    if sum(floors_by_group.values()) > len(guide_ids):
        return _refused(strategy, policy, BUDGET_SHORTFALL)
    if strategy == 'floor_then_size':
        quotas = _size_quotas(group_list, floors_by_group, len(guide_ids))
        return _accepted(strategy, policy, _emit(quotas, guide_ids))
    counts = {group.group_id: 0 for group in group_list}
    for guide_id, group_id in parsed_mapping:
        counts[group_id] += 1
    if any(counts[group_id] < floors_by_group[group_id] for group_id in counts):
        return _refused(strategy, policy, FROZEN_FLOOR_VIOLATION)
    return _accepted(strategy, policy, tuple(sorted(parsed_mapping)))


def _refused(strategy, policy, reason):
    return Allocation(strategy, policy, (), False, reason)


def _accepted(strategy, policy, assignment):
    return Allocation(strategy, policy, assignment, True, None)


def _groups(groups):
    if isinstance(groups, (str, bytes)) or not isinstance(groups, Sequence):
        raise AllocationInputError('INVALID_TYPE', 'groups must be a sequence of StaticGroup')
    if len(groups) != 2 or any(type(group) is not StaticGroup for group in groups):
        raise AllocationInputError('GROUP_COUNT', 'Step 2a allocation requires exactly two StaticGroup values')
    normalized = []
    for group in groups:
        _group_id(group.group_id)
        members = _id_tuple(group.member_ids, 'member id')
        if len(members) == 0:
            raise AllocationInputError('EMPTY_GROUP', f'group {group.group_id} has no members')
        if len(members) != len(set(members)):
            raise AllocationInputError('DUPLICATE_MEMBER', f'group {group.group_id} repeats a member ID')
        normalized.append(StaticGroup(group.group_id, members))
    if len({group.group_id for group in normalized}) != 2:
        raise AllocationInputError('DUPLICATE_GROUP', 'group IDs must be distinct')
    merged = [member for group in normalized for member in group.member_ids]
    if len(merged) != len(set(merged)):
        raise AllocationInputError('OVERLAPPING_MEMBERS', 'the two groups share a member ID')
    return normalized


def _floors(floors, groups):
    if isinstance(floors, (str, bytes)) or not isinstance(floors, Sequence):
        raise AllocationInputError('INVALID_TYPE', 'floors must be a sequence of GroupResourceFloor')
    if any(type(item) is not GroupResourceFloor for item in floors):
        raise AllocationInputError('INVALID_TYPE', 'each floor must be a GroupResourceFloor')
    known = {group.group_id for group in groups}
    seen = []
    checked = []
    for item in floors:
        _group_id(item.group_id)
        if item.group_id not in known:
            raise AllocationInputError('UNKNOWN_GROUP', f'floor names unknown group {item.group_id}')
        if type(item.minimum_guides) is not int or item.minimum_guides < 0:
            raise AllocationInputError('INVALID_FLOOR', 'minimum_guides must be a nonnegative integer')
        _check_trace(item)
        seen.append(item.group_id)
        checked.append(item)
    if len(seen) != len(set(seen)):
        raise AllocationInputError('DUPLICATE_FLOOR', 'each group may have one floor')
    if set(seen) != known:
        raise AllocationInputError('MISSING_FLOOR', 'both groups need a floor')
    return checked


def _check_trace(item):
    if item.policy not in _POLICIES:
        raise AllocationInputError('UNKNOWN_FLOOR_POLICY', f'unknown floor policy {item.policy}')
    traced = item.curve_length_m is not None or item.planning_gap_m is not None
    if item.policy == 'declared':
        if traced:
            raise AllocationInputError('FLOOR_TRACE_UNEXPECTED', 'a declared floor has no curve trace')
        return
    if item.curve_length_m is None or item.planning_gap_m is None:
        raise AllocationInputError('FLOOR_TRACE_INCOMPLETE', 'curve_ceil requires length and H_p')
    length = _positive_length(item.curve_length_m, 'curve length')
    gap = _positive_length(item.planning_gap_m, 'planning gap')
    if item.minimum_guides != math.ceil(length / gap):
        raise AllocationInputError(
            'FLOOR_TRACE_MISMATCH',
            'minimum_guides is not ceil(curve_length_m / planning_gap_m)',
        )


def _single_policy(floors):
    policies = {item.policy for item in floors}
    if len(policies) != 1:
        raise AllocationInputError(
            'MIXED_FLOOR_POLICY',
            'one allocation must use one floor policy so a later comparison does not mix resource conditions',
        )
    return policies.pop()


def _mapping(mapping, guide_ids, group_ids):
    if isinstance(mapping, (str, bytes)) or isinstance(mapping, dict) or not isinstance(mapping, Sequence):
        raise AllocationInputError('INVALID_TYPE', 'mapping must be a sequence of (guide_id, group_id) pairs')
    parsed = []
    for pair in mapping:
        if isinstance(pair, (str, bytes)) or not isinstance(pair, Sequence) or len(pair) != 2:
            raise AllocationInputError('INVALID_TYPE', 'each mapping entry must be a pair')
        guide_id, group_id = pair
        if type(guide_id) is not int or guide_id < 0:
            raise AllocationInputError('INVALID_ID', 'mapping guide IDs must be nonnegative integers')
        _group_id(group_id)
        if guide_id not in guide_ids:
            raise AllocationInputError('UNKNOWN_GUIDE', f'mapping names unknown guide {guide_id}')
        if group_id not in group_ids:
            raise AllocationInputError('UNKNOWN_GROUP', f'mapping names unknown group {group_id}')
        parsed.append((guide_id, group_id))
    mapped = [guide_id for guide_id, _group in parsed]
    if len(mapped) != len(set(mapped)):
        raise AllocationInputError('DUPLICATE_GUIDE', 'a guide appears twice in the frozen mapping')
    if set(mapped) != set(guide_ids):
        raise AllocationInputError('MAPPING_INCOMPLETE', 'the frozen mapping must name every guide once')
    return tuple(parsed)


def _size_quotas(groups, floors, budget):
    sizes = {group.group_id: len(group.member_ids) for group in groups}
    remainder = budget - sum(floors.values())
    total_members = sum(sizes.values())
    base = {group_id: (remainder * sizes[group_id]) // total_members for group_id in sizes}
    leftover = remainder - sum(base.values())
    preference = sorted(sizes, key=lambda group_id: (-sizes[group_id], group_id))
    bonus = dict.fromkeys(sizes, 0)
    for group_id in preference[:leftover]:
        bonus[group_id] += 1
    return {
        group_id: floors[group_id] + base[group_id] + bonus[group_id]
        for group_id in sizes
    }


def _emit(quotas, guide_ids):
    remaining = sorted(guide_ids)
    assigned = []
    for group_id in sorted(quotas):
        count = quotas[group_id]
        assigned.extend((guide_id, group_id) for guide_id in remaining[:count])
        remaining = remaining[count:]
    return tuple(sorted(assigned))


def _group_id(value):
    if type(value) is not str or value == '':
        raise AllocationInputError('INVALID_GROUP_ID', 'group IDs must be nonempty strings')


def _id_tuple(values, label):
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise AllocationInputError('INVALID_TYPE', f'{label}s must be a sequence')
    parsed = []
    for value in values:
        if type(value) is not int or value < 0:
            raise AllocationInputError('INVALID_ID', f'{label}s must be nonnegative integers')
        parsed.append(value)
    return tuple(parsed)


def _positive_length(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AllocationInputError('INVALID_LENGTH', f'{label} must be a positive real number')
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise AllocationInputError('INVALID_LENGTH', f'{label} must be a positive real number')
    return float(value)
