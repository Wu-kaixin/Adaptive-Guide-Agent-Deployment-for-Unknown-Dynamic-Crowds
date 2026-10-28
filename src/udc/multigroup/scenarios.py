"""Frozen scenario generation; none of these shapes/labels go to the planner."""
import numpy as np
from shapely.geometry import Point, Polygon

from .observation import StaticTruth


CENTERS = {
    1: [(20., 15.)],
    2: [(12., 15.), (28., 15.)],
    3: [(8., 8.), (32., 8.), (20., 22.)],
    5: [(8., 8.), (20., 8.), (32., 8.), (12., 22.), (28., 22.)],
}


def make_truth(group_count, shape, seed, namespace, size_multipliers=None, hotspot_multipliers=None):
    if 5000 <= seed <= 5029:
        raise ValueError('Seeds 5000-5029 are reserved and cannot be generated here')
    if group_count not in CENTERS or shape not in ('circle', 'ellipse', 'concave', 'irregular'):
        raise ValueError('Unknown group count or shape')
    # All K share an independent namespace; no use of global NumPy state.
    shape_id = ('circle', 'ellipse', 'concave', 'irregular').index(shape)
    rng = np.random.default_rng(np.random.SeedSequence([namespace, seed, group_count, shape_id]))
    people, labels, demand = [], [], []
    irregular = Polygon([(-1.8, -.7), (-.8, -1.3), (.7, -1.0), (1.8, .15), (.45, 1.35), (-1.25, .8)])
    size_multipliers = size_multipliers or [1.] * group_count
    hotspot_multipliers = hotspot_multipliers or [4.] * group_count
    if len(size_multipliers) < group_count or len(hotspot_multipliers) < group_count:
        raise ValueError('Explicit group heterogeneity patterns must cover all requested groups')
    for group_id, center in enumerate(CENTERS[group_count]):
        size = size_multipliers[group_id]
        # Increase physical extent by adding lattice people, not by stretching
        # neighbours beyond the frozen observation-only clustering radius.
        xmax, ymax = int(np.ceil(3 * size)), int(np.ceil(2 * size))
        grid = np.array([(x, y) for y in np.arange(-ymax, ymax + 1) * .62
                         for x in np.arange(-xmax, xmax + 1) * .62])
        if shape == 'circle':
            mask = np.linalg.norm(grid, axis=1) <= 1.45 * size
        elif shape == 'ellipse':
            mask = (grid[:, 0] / (1.95 * size)) ** 2 + (grid[:, 1] / (1.3 * size)) ** 2 <= 1
        elif shape == 'concave':
            mask = ((np.abs(grid[:, 0]) <= 1.3 * size)
                    & ~((np.abs(grid[:, 0]) < .5 * size) & (grid[:, 1] > -.7 * size)))
        else:
            mask = np.array([irregular.covers(Point(point / size)) for point in grid])
        local = grid[mask]
        # Genuine shape-instance variation, while staying within a declared
        # template family; not a claim to arbitrary nonconvex geometry.
        scales = rng.uniform(.90, 1.10, 2)
        angle = rng.uniform(-np.pi, np.pi)
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        local = (local * scales) @ rotation.T
        local += rng.uniform(-.025, .025, local.shape)
        positions = local + center
        # Observable numerical priority, not validated human risk.
        priorities = (1 + group_id % 3) * np.where(local[:, 0] > .3 * size,
                                                  hotspot_multipliers[group_id], 1.)
        people.extend(positions)
        labels.extend([group_id] * len(positions))
        demand.extend(priorities)
    return StaticTruth(np.asarray(people), np.full(len(people), .20), np.asarray(demand), np.asarray(labels))


def initial_guides(max_count, room):
    """A common nested prefix on the room perimeter, independent of all methods.

    Van der Corput order fills large perimeter gaps without moving old robots.
    """
    width, height = np.asarray(room, dtype=float)
    perimeter = Polygon([(1.1, 1.1), (width - 1.1, 1.1),
                         (width - 1.1, height - 1.1), (1.1, height - 1.1)]).exterior

    def fraction(integer):
        value, divisor = 0., 1.
        while integer:
            integer, digit = divmod(integer, 2)
            divisor *= 2
            value += digit / divisor
        return value

    return np.array([perimeter.interpolate(((fraction(i) + .012) % 1) * perimeter.length).coords[0]
                     for i in range(max_count)])
