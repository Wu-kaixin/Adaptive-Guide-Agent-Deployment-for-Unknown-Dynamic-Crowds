"""Explicit new candidate restriction matching serial robot obstacle polygons."""
import numpy as np
import shapely

from .geometry import outer_disk


def serial_target_admissible(positions, minimum_distance):
    points = np.asarray(positions, dtype=float).reshape(-1, 2)
    if not np.isfinite(points).all() or minimum_distance <= 0:
        raise ValueError('Finite target coordinates and positive distance required')
    if len(points) < 2:
        return True
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    if distances.min() < minimum_distance:
        return False
    queries = shapely.points(points)
    for index, point in enumerate(points):
        disk = outer_disk(point, minimum_distance, quad_segs=4)
        interior = shapely.contains(disk, queries)
        interior[index] = False
        if interior.any():
            return False
    return True
