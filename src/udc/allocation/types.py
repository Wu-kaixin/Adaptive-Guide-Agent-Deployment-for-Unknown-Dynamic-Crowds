"""Immutable Step 2a allocation records. They do not carry geometry."""
from dataclasses import dataclass


@dataclass(frozen=True)
class StaticGroup:
    """Members are stable person IDs, not rows of ``Observation``."""

    group_id: str
    member_ids: tuple[int, ...]


@dataclass(frozen=True)
class GroupResourceFloor:
    """Guide-count lower bound plus the rule that produced it.

    ``declared`` stores only the integer. ``curve_ceil`` also stores the curve
    length and planning gap ``H_p`` used for ``ceil(L / H_p)``.
    """

    group_id: str
    minimum_guides: int
    policy: str = 'declared'
    curve_length_m: float | None = None
    planning_gap_m: float | None = None


@dataclass(frozen=True)
class Allocation:
    """Pure assignment. Feasibility is not deployment, safety, or arrival."""

    strategy: str
    floor_policy: str
    assignment: tuple[tuple[int, str], ...]
    feasible: bool
    reason: str | None
