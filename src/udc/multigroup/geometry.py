"""Observation-only robust occupied sets and per-component deployment curves."""
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from udc.boundary import Boundary
from udc.navigation.paths import navigation_domain
from .observation import static_groups


class ScientificFailure(ValueError):
    def __init__(self, status, message, **evidence):
        super().__init__(message)
        self.status = status
        self.evidence = evidence


@dataclass(frozen=True)
class ObservedGroup:
    group_id: int
    rows: tuple[int, ...]
    boundary: Boundary
    occupied: object
    demand_total: float


def outer_disk(center, radius, quad_segs=16):
    """Circumscribed polygon containing an analytic disk, explicit new contract.

    Shapely buffers are inscribed. Scaling by sec(pi / number_of_sides)
    plus a 1e-12 m construction pad gives an outward approximation. This is
    not an epsilon acceptance rule and does not change the legacy q16/q32 path.
    """
    radius = float(radius)
    if not np.isfinite(radius) or radius <= 0 or int(quad_segs) != quad_segs or quad_segs < 2:
        raise ValueError('Outer disk needs positive radius and integer quad_segs >= 2')
    circumradius = radius / np.cos(np.pi / (4 * quad_segs)) + 1e-12
    return Point(center).buffer(circumradius, quad_segs=int(quad_segs))


def construct_groups(measurement, room, config):
    """No simulator truth, spawn polygon, or true group count enters here."""
    obs = measurement.observation
    groups = static_groups(obs, config['group_link_m'])
    result = []
    for group_id, rows in enumerate(groups):
        index = np.array(rows, dtype=int)
        required = unary_union([outer_disk(obs.positions[i], obs.radii[i] + measurement.position_error_bound_m)
                                for i in rows])
        # An explicit 5 mm (protocol parameter) conservative construction layer
        # separates the physical uncertainty disks from boolean-union contacts.
        # Acceptance still uses strict covers; no failed predicate is waived.
        disks = unary_union([outer_disk(obs.positions[i], obs.radii[i] + measurement.position_error_bound_m
                                       + config['construction_margin_m']) for i in rows])
        closing = config['closing_radius_m']
        closed = disks.buffer(closing, quad_segs=16).buffer(-closing, quad_segs=16)
        occupied = unary_union([disks, closed])
        if occupied.geom_type != 'Polygon' or occupied.is_empty or not occupied.is_valid:
            raise ScientificFailure('BOUNDARY_INVALID', 'Observed component has no single occupied exterior',
                                    group_id=group_id, geometry_type=occupied.geom_type)
        # Explicit external-envelope model: holes are filled; no component is dropped.
        occupied = orient(Polygon(occupied.exterior), sign=1)
        if not occupied.covers(required):
            raise ScientificFailure('BOUNDARY_INVALID', 'External envelope lost required uncertainty geometry', group_id=group_id)
        deployment = occupied.buffer(config['offset_m'], quad_segs=16)
        if deployment.geom_type != 'Polygon' or not deployment.is_valid or deployment.interiors:
            raise ScientificFailure('OFFSET_INVALID', 'Deployment is not a simple external ring', group_id=group_id)
        deployment = orient(deployment, sign=1)
        line = deployment.exterior
        count = max(64, int(np.ceil(line.length / config['sample_spacing_m'])))
        points = np.array([line.interpolate(i * line.length / count).coords[0] for i in range(count)])
        edge_lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
        boundary = Boundary(np.asarray(occupied.exterior.coords[:-1]), points,
                            np.r_[0., np.cumsum(edge_lengths[:-1])], float(edge_lengths.sum()))
        result.append(ObservedGroup(group_id, rows, boundary, occupied, float(obs.demand[index].sum())))
    occupied_union = unary_union([group.occupied for group in result])
    domain = navigation_domain(room, config['wall_distance_m'], occupied_union,
                               config['guide_radius_m'] + config['clearance_m'])
    # Conservative separability assumption allows a separable finite-catalog DP.
    for i, first in enumerate(result):
        for second in result[:i]:
            distance = Polygon(first.boundary.deployment).exterior.distance(Polygon(second.boundary.deployment).exterior)
            if (distance < config['guide_distance_m']
                    or Polygon(first.boundary.deployment).intersects(second.occupied)
                    or Polygon(second.boundary.deployment).intersects(first.occupied)):
                raise ScientificFailure('GROUP_SEPARATION_INVALID', 'Deployment rings are not safely separated',
                                        groups=[first.group_id, second.group_id], ring_distance_m=float(distance))
    return tuple(result), domain
