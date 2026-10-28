"""Frozen physical truth and bounded measurements with no shared array storage."""
from dataclasses import dataclass

import numpy as np

from udc.interfaces import Observation


def immutable_array(value, dtype=float):
    result = np.array(value, dtype=dtype, copy=True)
    result.setflags(write=False)
    return result


def validate_observation(observation):
    p, r, d = observation.positions, observation.radii, observation.demand
    if (p.ndim != 2 or p.shape[1] != 2 or not len(p)
            or r.shape != (len(p),) or d.shape != (len(p),)
            or not all(np.isfinite(a).all() for a in (p, r, d))
            or np.any(r <= 0) or np.any(d <= 0)):
        raise ValueError('OBSERVATION_INVALID: finite positions, positive radii and demand required')


@dataclass(frozen=True)
class StaticTruth:
    """Simulator/evaluator data. Never an argument to a deployment planner."""
    positions: np.ndarray
    radii: np.ndarray
    demand: np.ndarray
    group_ids: np.ndarray

    def __post_init__(self):
        for name in ('positions', 'radii', 'demand'):
            object.__setattr__(self, name, immutable_array(getattr(self, name)))
        object.__setattr__(self, 'group_ids', immutable_array(self.group_ids, int))
        validate_observation(Observation(self.positions, self.radii, self.demand))
        if self.group_ids.shape != (len(self.positions),):
            raise ValueError('TRUTH_INVALID: group identities must match people')


@dataclass(frozen=True)
class BoundedMeasurement:
    observation: Observation
    position_error_bound_m: float
    model: str

    def __post_init__(self):
        source = self.observation
        frozen = Observation(*(immutable_array(getattr(source, key))
                               for key in ('positions', 'radii', 'demand')))
        validate_observation(frozen)
        bound = float(self.position_error_bound_m)
        if not np.isfinite(bound) or bound < 0:
            raise ValueError('OBSERVATION_INVALID: error bound must be finite and nonnegative')
        object.__setattr__(self, 'observation', frozen)
        object.__setattr__(self, 'position_error_bound_m', bound)


def measure(truth, error_bound_m, seed, model='bounded_disk'):
    """One frozen draw; all people detected, radii/demand and robot pose exact.

    Gaussian scene jitter is deliberately not used as a sensor-error model.
    The same RNG stream at different bounds couples the normalized errors.
    """
    bound = float(error_bound_m)
    if not np.isfinite(bound) or bound < 0:
        raise ValueError('OBSERVATION_INVALID: error bound must be finite and nonnegative')
    rng = np.random.default_rng(seed)
    if model == 'bounded_disk':
        angle = rng.uniform(0, 2 * np.pi, len(truth.positions))
        radius = np.sqrt(rng.uniform(0, 1, len(truth.positions)))
        delta = radius[:, None] * np.column_stack((np.cos(angle), np.sin(angle)))
    elif model == 'common_bias':
        angle = rng.uniform(0, 2 * np.pi)
        delta = np.tile([np.cos(angle), np.sin(angle)], (len(truth.positions), 1))
    else:
        raise ValueError(f'Unknown measurement model: {model}')
    return BoundedMeasurement(Observation(truth.positions + bound * delta,
                                          truth.radii, truth.demand), bound, model)


def static_groups(observation, link_distance_m):
    """Connected components of observed centres; no true labels or requested K.

    Canonical component IDs follow the smallest stable pedestrian row index.
    The protocol's link radius is a grouping assumption, not a safety distance.
    """
    validate_observation(observation)
    link = float(link_distance_m)
    if not np.isfinite(link) or link <= 0:
        raise ValueError('Grouping link distance must be finite and positive')
    adjacent = np.linalg.norm(observation.positions[:, None] - observation.positions[None, :], axis=2) <= link
    remaining = set(range(len(adjacent)))
    groups = []
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        pending, component = [first], [first]
        while pending:
            row = pending.pop()
            neighbors = sorted(remaining.intersection(np.flatnonzero(adjacent[row]).tolist()))
            remaining.difference_update(neighbors)
            pending.extend(neighbors)
            component.extend(neighbors)
        groups.append(tuple(sorted(component)))
    return tuple(groups)
