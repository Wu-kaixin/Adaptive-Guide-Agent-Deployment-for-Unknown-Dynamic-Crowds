"""Records passed between the environment, controller and later stages.

Step 1 controllers still consume ``Observation`` plus guide positions.
``Perception`` adds stable pedestrian identities, velocities and time.
``ResourceAllocator`` and ``LocalPolicy`` are typed seams for Step 2 and
Step 3. Nothing in the Step 1 loop calls them.
"""
from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Observation:
    """Controller input. Spawn geometry is not part of this record."""
    positions: np.ndarray
    radii: np.ndarray
    demand: np.ndarray


@dataclass(frozen=True)
class PedestrianState:
    ids: np.ndarray
    positions: np.ndarray
    radii: np.ndarray
    demand: np.ndarray
    velocities: np.ndarray


@dataclass(frozen=True)
class GuideState:
    positions: np.ndarray
    velocities: np.ndarray


@dataclass(frozen=True)
class Perception:
    """Crowd and optional guide state at one timestamp."""
    time: float
    pedestrians: PedestrianState
    guides: GuideState | None = None

    def observation(self):
        people = self.pedestrians
        return Observation(people.positions.copy(), people.radii.copy(), people.demand.copy())


@dataclass(frozen=True)
class GroupDemand:
    """Step 2 input. Step 1 has one frozen crowd and does not build groups."""
    group_id: int
    pedestrian_ids: tuple[int, ...]
    demand_total: float


@dataclass(frozen=True)
class AllocationDecision:
    guides_per_group: tuple[int, ...]


class ResourceAllocator(Protocol):
    """Step 2 integer assignment of a shared guide pool. Not implemented."""
    def allocate(self, groups: tuple[GroupDemand, ...], guide_count: int) -> AllocationDecision:
        ...


@dataclass(frozen=True)
class NeighborMessage:
    sender: int
    timestamp: float
    payload: dict


class LocalPolicy(Protocol):
    """Step 3 local velocity from a partial observation and received messages."""
    def velocity(self, own_position: np.ndarray, observation: Observation,
                 messages: tuple[NeighborMessage, ...]) -> np.ndarray: ...
