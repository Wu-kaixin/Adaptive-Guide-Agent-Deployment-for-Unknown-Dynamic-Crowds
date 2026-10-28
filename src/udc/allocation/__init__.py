"""One-shot static allocation for the Step 2a protocol.

Step 1 does not import this package. A feasible result only says the shared
guide identities meet the supplied floors.
"""

from .allocate import (
    BUDGET_SHORTFALL,
    FROZEN_FLOOR_VIOLATION,
    AllocationInputError,
    allocate,
    floor_from_curve,
    validate_member_partition,
)
from .types import Allocation, GroupResourceFloor, StaticGroup

__all__ = [
    'BUDGET_SHORTFALL',
    'FROZEN_FLOOR_VIOLATION',
    'Allocation',
    'AllocationInputError',
    'GroupResourceFloor',
    'StaticGroup',
    'allocate',
    'floor_from_curve',
    'validate_member_partition',
]
