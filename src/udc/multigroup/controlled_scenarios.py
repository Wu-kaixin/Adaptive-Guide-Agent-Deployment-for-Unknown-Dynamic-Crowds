"""Controlled evaluator worlds for population and known-wall development.

These generators are never called by a deployment controller. Paired wall
conditions are rigid translations of one base physical input, not new draws.
"""
from functools import lru_cache

import numpy as np

from .observation import StaticTruth
from .scenarios import CENTERS, initial_guides


def grid_truth(k, side, seed, namespace, left_wall_clearance=None):
    if k not in CENTERS or side not in (3, 5, 7, 9) or 5000 <= seed <= 5029:
        raise ValueError('Unsupported controlled geometry or reserved seed')
    rng = np.random.default_rng(np.random.SeedSequence([namespace, seed, k, side]))
    grid = np.array([(x, y) for y in range(side) for x in range(side)], float)
    grid = (grid - (side - 1) / 2) * .62
    positions = np.concatenate([grid + center + rng.uniform(-.01, .01, grid.shape) for center in CENTERS[k]])
    if left_wall_clearance is not None:
        positions[:, 0] += .2 + left_wall_clearance - positions[:, 0].min()
    demand = np.tile(1 + np.arange(side * side) % 3 + 3 * (grid[:, 0] > 0), k)
    return StaticTruth(positions, np.full(len(positions), .2), demand, np.repeat(np.arange(k), side * side))


@lru_cache(maxsize=4)
def _perimeter_pool(room):
    points = initial_guides(2048, room)
    points.setflags(write=False)
    return points


def observed_safe_guides(measurement, count, room, config):
    """Deterministic nested initialization, filtered before outcome generation.

    Uses declared observation disks, not truth. The 1um initialization margin
    constructs an interior input; it never changes execution safety acceptance.
    Exhausting this finite input candidate pool is not an infeasibility proof.
    """
    obs = measurement.observation
    pool = _perimeter_pool(tuple(room))
    thresholds = obs.radii + measurement.position_error_bound_m + config['guide_radius_m'] + config['clearance_m'] + 1e-6
    valid = (np.linalg.norm(pool[:, None] - obs.positions[None], axis=2) >= thresholds).all(axis=1)
    selected = []
    for point in pool[valid]:
        if not selected or np.all(np.linalg.norm(np.asarray(selected) - point, axis=1) >= config['guide_distance_m']):
            selected.append(point)
            if len(selected) == count:
                return np.asarray(selected)
    raise ValueError('Controlled initialization candidate pool exhausted; do not redraw or claim physical impossibility')
