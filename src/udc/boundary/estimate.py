"""Estimate the crowd envelope and construct an exterior deployment curve."""
from dataclasses import dataclass

import numpy as np
from shapely.geometry import MultiPoint, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union


@dataclass(frozen=True)
class Boundary:
    crowd: np.ndarray
    deployment: np.ndarray
    arc: np.ndarray
    length: float


def occupied_hull(points, radii=None):
    """Convex hull of observed centres, or of their radius disks when radii are positive."""
    points = np.asarray(points, dtype=float)
    radii = np.zeros(len(points)) if radii is None else np.asarray(radii, dtype=float)
    if not np.any(radii):
        hull = MultiPoint(points).convex_hull
    else:
        occupied = [Point(p).buffer(float(r), quad_segs=32) for p, r in zip(points, radii)]
        hull = Polygon(MultiPoint(np.vstack([np.asarray(g.exterior.coords) for g in occupied])).convex_hull.exterior)
    if hull.geom_type != 'Polygon' or hull.area <= 1e-10:
        raise ValueError('BOUNDARY_INVALID: observed positions are degenerate')
    return hull


def estimate_boundary(points, offset, spacing, room, wall_margin, crowd_distance, radii=None):
    """Convex hull is the initial, conservative boundary model.

    Only observed pedestrian positions enter the estimator. The deployment
    buffer is sampled by its own arc length; room geometry is a feasibility check.
    """
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        raise ValueError('BOUNDARY_INVALID: need at least three finite observed points')
    if not np.isfinite(offset) or offset <= 0 or not np.isfinite(spacing) or spacing <= 0:
        raise ValueError('Offset and sample spacing must be positive')
    radii = np.zeros(len(points)) if radii is None else np.asarray(radii, dtype=float)
    if radii.shape != (len(points),) or np.any(radii < 0) or not np.isfinite(radii).all():
        raise ValueError('BOUNDARY_INVALID: radii must be finite and nonnegative')
    # Buffer each observed centre by its physical radius before the conservative
    # convex envelope.  This never reads the generator's spawn polygon.
    hull = occupied_hull(points, radii)
    polygon = orient(hull.buffer(offset, quad_segs=32), sign=1)
    line = polygon.exterior
    count = max(32, int(np.ceil(line.length / spacing)))
    deployment = np.array([line.interpolate(i*line.length/count).coords[0] for i in range(count)])
    sampled = Polygon(deployment)
    if np.any(deployment < wall_margin) or np.any(deployment > np.asarray(room)-wall_margin):
        raise ValueError('OFFSET_INVALID: deployment curve exceeds room clearance')
    if not all(sampled.covers(Point(p)) for p in points):
        raise ValueError('OFFSET_INVALID: deployment curve does not enclose the observations')
    if min(sampled.exterior.distance(Point(p)) for p in points) < crowd_distance:
        raise ValueError('OFFSET_INVALID: deployment curve is too close to people')
    lengths = np.linalg.norm(np.roll(deployment, -1, axis=0)-deployment, axis=1)
    arc = np.r_[0., np.cumsum(lengths[:-1])]
    return Boundary(np.asarray(hull.exterior.coords[:-1]), deployment, arc, float(lengths.sum()))


def _sample_exterior(polygon, spacing):
    line = polygon.exterior
    count = max(32, int(np.ceil(line.length / spacing)))
    return np.array([line.interpolate(i * line.length / count).coords[0] for i in range(count)]), float(line.length)


def estimate_disk_union(points, offset, spacing, room, wall_margin, crowd_distance,
                        radii=None, closing_radius=0.30, quad_segs=16):
    """Observation-only non-convex envelope.

    The occupied set is the union of the observed disks. A morphological
    closing of radius ``closing_radius`` joins neighbours whose body surfaces
    are less than ``2 * closing_radius`` apart. The deployment curve is the
    exterior offset of that set by ``offset``. Neither step reads a spawn
    polygon. ``closing_radius`` is an estimator length, not a safety distance.
    The convex-hull estimator remains ``estimate_boundary``.
    """
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        raise ValueError('BOUNDARY_INVALID: need at least three finite observed points')
    if not np.isfinite(offset) or offset <= 0 or not np.isfinite(spacing) or spacing <= 0:
        raise ValueError('Offset and sample spacing must be positive')
    if not np.isfinite(closing_radius) or closing_radius < 0:
        raise ValueError('BOUNDARY_INVALID: closing radius must be finite and nonnegative')
    radii = np.zeros(len(points)) if radii is None else np.asarray(radii, dtype=float)
    if radii.shape != (len(points),) or np.any(radii < 0) or not np.isfinite(radii).all():
        raise ValueError('BOUNDARY_INVALID: radii must be finite and nonnegative')
    # The acceptance check covers disks drawn with 16 segments per quarter.
    # A coarser or chordal ring lies inside those disks, so the occupied ring
    # keeps the buffer vertices and uses at least 32 segments per quarter.
    disk_quad_segs = max(int(quad_segs), 32)
    disks = [Point(point).buffer(float(radius), quad_segs=disk_quad_segs)
             for point, radius in zip(points, radii)]
    original = unary_union(disks)
    occupied = original
    if closing_radius > 0:
        closed = original.buffer(float(closing_radius), quad_segs=disk_quad_segs)
        closed = closed.buffer(-float(closing_radius), quad_segs=disk_quad_segs)
        if not closed.is_empty:
            # Closing can shave the disk arcs. Union with the original disks
            # restores coverage without reopening a gap the closing filled.
            occupied = unary_union([original, closed])
    if occupied.is_empty or occupied.geom_type != 'Polygon':
        kind = 'empty' if occupied.is_empty else occupied.geom_type
        raise ValueError(f'BOUNDARY_INVALID: disk union is {kind} at this closing radius')
    polygon = orient(occupied.buffer(float(offset), quad_segs=32), sign=1)
    if polygon.geom_type != 'Polygon' or polygon.area <= 0 or polygon.interiors:
        raise ValueError('OFFSET_INVALID: disk-union offset is not a simple polygon')
    deployment, _ = _sample_exterior(polygon, spacing)
    sampled = Polygon(deployment)
    if not sampled.is_valid or sampled.interiors:
        raise ValueError('OFFSET_INVALID: sampled disk-union curve is not a simple ring')
    if np.any(deployment < wall_margin) or np.any(deployment > np.asarray(room) - wall_margin):
        raise ValueError('OFFSET_INVALID: deployment curve exceeds room clearance')
    if not all(sampled.covers(Point(point)) for point in points):
        raise ValueError('OFFSET_INVALID: deployment curve does not enclose the observations')
    centre_distance = min(sampled.exterior.distance(Point(point)) for point in points)
    if centre_distance < crowd_distance:
        raise ValueError('OFFSET_INVALID: deployment curve is too close to people')
    occupied = orient(occupied, sign=1)
    crowd = np.asarray(occupied.exterior.coords[:-1])
    if not all(Polygon(crowd).covers(Point(point).buffer(float(radius), quad_segs=16))
               for point, radius in zip(points, radii)):
        raise ValueError('BOUNDARY_INVALID: occupied ring does not cover the observed disks')
    lengths = np.linalg.norm(np.roll(deployment, -1, axis=0) - deployment, axis=1)
    arc = np.r_[0.0, np.cumsum(lengths[:-1])]
    return Boundary(crowd, deployment, arc, float(lengths.sum()))
