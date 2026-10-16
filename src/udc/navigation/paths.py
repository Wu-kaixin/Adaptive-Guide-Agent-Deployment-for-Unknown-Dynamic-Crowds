"""Static visibility-graph navigation and reachable assignment."""
from dataclasses import dataclass
import hashlib
import heapq

import numpy as np
from scipy.optimize import linear_sum_assignment
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from udc.safety import is_safe, path_clearances


# Vertex reduction tolerance, then a larger outward buffer. The second length
# is a planning margin so simplification cannot shrink the crowd obstacle.
# It is not a safety distance and it is not applied to people or walls.
SIMPLIFY_TOLERANCE_M = 0.10
PLANNING_MARGIN_M = 0.11
# Planner-node nudge when a boundary sample falls a few ulps inside the
# obstacle. The executed state is never moved by this amount.
BOUNDARY_NODE_NUDGE_M = 1e-8
# GEOS can report a boundary point as outside by a fraction of a nanometre.
# This is not a safety distance and it does not admit the planning-margin shell.
ENDPOINT_TOLERANCE_M = 1e-7


@dataclass(frozen=True)
class NavigationResult:
    assignment: np.ndarray
    paths: tuple
    lengths: np.ndarray
    reachable: np.ndarray


@dataclass(frozen=True)
class NavigationDomain:
    """Shared geometry for the initial assignment and every later replan.

    ``physical_obstacle`` is the crowd polygon expanded by ``crowd_margin``.
    When that polygon is the occupied body, this matches the guide-centre
    clearance. ``conservative_obstacle`` expands it further by the planning
    margin after simplification. ``free`` is the room inset minus that
    conservative obstacle. Guide positions are not part of either obstacle.
    """

    room_poly: object
    physical_obstacle: object
    conservative_obstacle: object
    free: object
    physical_free: object
    wall_margin: float
    crowd_margin: float
    simplify_tolerance_m: float
    planning_margin_m: float
    geometry_sha256: str

    def signed_free_distance(self, point):
        geometry = Point(point)
        distance = float(self.free.boundary.distance(geometry))
        return distance if self.free.covers(geometry) else -distance

    def component_index(self, point):
        geometry = Point(point)
        if self.free.geom_type == 'Polygon':
            return 0 if self.free.covers(geometry) else None
        for index, part in enumerate(self.free.geoms):
            if part.covers(geometry):
                return index
        return None


def navigation_domain(room, wall_margin, crowd_polygon, crowd_margin):
    """Build the conservative free space used by planning and replanning."""
    room = np.asarray(room, dtype=float)
    room_poly = Polygon([(0, 0), (room[0], 0), (room[0], room[1]), (0, room[1])]).buffer(-float(wall_margin))
    physical = crowd_polygon.buffer(float(crowd_margin), join_style='mitre')
    conservative = physical.simplify(SIMPLIFY_TOLERANCE_M, preserve_topology=True).buffer(
        PLANNING_MARGIN_M, join_style='mitre')
    free = room_poly.difference(conservative)
    physical_free = room_poly.difference(physical)
    identity = hashlib.sha256()
    identity.update(np.asarray([wall_margin, crowd_margin, SIMPLIFY_TOLERANCE_M, PLANNING_MARGIN_M], dtype=np.float64).tobytes())
    for geometry in (physical, conservative, free):
        identity.update(_coordinate_bytes(geometry))
    return NavigationDomain(
        room_poly, physical, conservative, free, physical_free,
        float(wall_margin), float(crowd_margin), SIMPLIFY_TOLERANCE_M, PLANNING_MARGIN_M,
        identity.hexdigest(),
    )


def _coordinate_bytes(geometry):
    if geometry.is_empty:
        return b''
    pieces = [geometry] if geometry.geom_type == 'Polygon' else list(getattr(geometry, 'geoms', [geometry]))
    arrays = []
    for piece in pieces:
        if piece.geom_type != 'Polygon':
            arrays.append(np.asarray(piece.coords, dtype=np.float64))
            continue
        arrays.append(np.asarray(piece.exterior.coords, dtype=np.float64))
        arrays.extend(np.asarray(ring.coords, dtype=np.float64) for ring in piece.interiors)
    return b''.join(array.tobytes() for array in arrays)


def _within_free(geometry, point):
    sample = Point(point)
    if geometry.covers(sample):
        return True
    return float(geometry.boundary.distance(sample)) <= ENDPOINT_TOLERANCE_M


def _other_guide_space(domain, guides, index, radius):
    """Free space with every other guide removed. The guide being planned is absent."""
    if radius is None or radius <= 0 or guides is None:
        return domain.free, ()
    disks = []
    for other, point in enumerate(np.asarray(guides, dtype=float)):
        if other == index:
            continue
        disks.append(Point(point).buffer(float(radius), quad_segs=2))
    if not disks:
        return domain.free, ()
    occupied = unary_union(disks)
    free = domain.free.difference(occupied)
    pieces = [occupied] if occupied.geom_type == 'Polygon' else list(getattr(occupied, 'geoms', []))
    nodes = []
    for piece in pieces:
        if piece.geom_type == 'Polygon':
            nodes.extend(np.asarray(piece.exterior.coords[:-1], dtype=float))
    return free, nodes


def _shortest(start, target, free, obstacle, extra_nodes=()):
    direct = LineString([start, target])
    if free.covers(direct) and (obstacle is None or not direct.crosses(obstacle)):
        return np.asarray([start, target], dtype=float), float(direct.length)
    nodes = [np.asarray(start), np.asarray(target)]
    if obstacle is not None and not obstacle.is_empty and obstacle.geom_type == 'Polygon':
        coords = list(obstacle.exterior.coords[:-1])
        nodes += [np.asarray(p) for p in coords]
    nodes.extend(np.asarray(point, dtype=float) for point in extra_nodes)
    n = len(nodes); edges = [[] for _ in nodes]
    for i in range(n):
        for j in range(i):
            segment = LineString([nodes[i], nodes[j]])
            if free.covers(segment) and (obstacle is None or not segment.crosses(obstacle)):
                d = float(np.linalg.norm(nodes[i]-nodes[j])); edges[i].append((j,d)); edges[j].append((i,d))
    dist=[float('inf')]*n; prev=[-1]*n; dist[0]=0.; queue=[(0.,0)]
    while queue:
        d,u=heapq.heappop(queue)
        if d != dist[u]: continue
        for v,w in edges[u]:
            if d+w < dist[v]: dist[v]=d+w; prev[v]=u; heapq.heappush(queue,(dist[v],v))
    if not np.isfinite(dist[1]): return None, float('inf')
    ids=[]; u=1
    while u >= 0: ids.append(u); u=prev[u]
    return np.asarray([nodes[i] for i in ids[::-1]]), dist[1]


def _prepare_start(point, domain, guides=None, guide=None, people=None, limits=None, room=None):
    """Return the visibility-graph start and an optional true-state prefix.

    A point on the numerical boundary is not passed into the graph. A point in
    the planning shell is connected only when the physical segment check passes.
    """
    point = np.asarray(point, dtype=float)
    if domain.free.covers(Point(point)):
        return point, None
    found = _margin_contact(point, domain)
    if found is None:
        return None
    contact, penetration = found
    if penetration > ENDPOINT_TOLERANCE_M:
        if people is None or limits is None or guides is None or guide is None or room is None:
            return None
        end = np.array(np.asarray(guides, dtype=float), copy=True)
        end[guide] = contact
        clearances = path_clearances(np.asarray(guides, dtype=float), end, np.asarray(people, dtype=float), np.asarray(room, dtype=float))
        if not is_safe(clearances, limits):
            return None
    if np.linalg.norm(contact - point) <= 1e-12:
        return contact, None
    return contact, point


def navigate_and_assign(guides, targets, room, wall_margin, crowd_polygon, crowd_margin,
                        people=None, limits=None):
    domain = navigation_domain(room, wall_margin, crowd_polygon, crowd_margin)
    free, obstacle = domain.free, domain.conservative_obstacle
    paths={}; cost=np.full((len(guides),len(targets)), np.inf)
    guides = np.asarray(guides, dtype=float)
    for i,g in enumerate(guides):
        prepared = _prepare_start(g, domain, guides, i, people, limits, room)
        if prepared is None:
            continue
        planner_start, prefix = prepared
        for j,t in enumerate(targets):
            target_ready = _prepare_start(t, domain)
            if target_ready is None:
                continue
            planner_target = target_ready[0]
            # Pairwise assignment keeps the crowd-only graph. Other guides are
            # excluded from that graph; an assigned replan adds them one guide at a time.
            path, length = _shortest(planner_start, planner_target, free, obstacle)
            if path is None:
                continue
            if prefix is not None:
                path = np.vstack([prefix, path])
                length = float(length + np.linalg.norm(path[1] - path[0]))
            paths[i,j], cost[i,j] = path, length
    finite=np.isfinite(cost)
    if not finite.any() or np.any(~finite.any(axis=0)):
        raise ValueError('PATH_UNREACHABLE: target has no path in conservative free space')
    penalty=np.where(finite,cost,1e12)
    rows,cols=linear_sum_assignment(penalty)
    keep=cols < len(targets); rows,cols=rows[keep],cols[keep]
    if len(set(cols)) != len(targets) or np.any(~finite[rows,cols]):
        raise ValueError('PATH_UNREACHABLE: reachable bipartite matching does not exist')
    assignment=np.full(len(guides),-1,dtype=int); assignment[rows]=cols
    ordered=tuple(paths[i,j] for i,j in zip(rows,cols))
    return NavigationResult(assignment,ordered,cost[rows,cols],finite)


def segment_in_free(start, end, domain):
    """True when the segment stays in free space, including the boundary tolerance."""
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    if domain.free.covers(LineString([start, end])):
        return True
    if not domain.physical_free.covers(LineString([start, end])):
        return False
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        sample = (1.0 - fraction) * start + fraction * end
        if domain.signed_free_distance(sample) < -ENDPOINT_TOLERANCE_M:
            return False
    return True


def _boundary_node(point, domain):
    """Closest conservative-obstacle boundary sample that free space accepts.

    The sample is a planner node. The guide state stays at ``point``.
    """
    boundary = domain.conservative_obstacle.boundary
    nearest = np.asarray(boundary.interpolate(boundary.project(Point(point))).coords[0], dtype=float)
    if domain.free.covers(Point(nearest)):
        return nearest
    direction = nearest - np.asarray(point, dtype=float)
    length = float(np.linalg.norm(direction))
    if length <= 1e-15:
        return None
    nudged = nearest + BOUNDARY_NODE_NUDGE_M * direction / length
    if domain.free.covers(Point(nudged)) and domain.physical_free.covers(LineString([point, nudged])):
        return nudged
    return None


def _margin_contact(point, domain):
    """Return a covered contact point when ``point`` is only in the planning shell."""
    geometry = Point(point)
    if domain.free.covers(geometry):
        return None
    if not domain.physical_free.covers(geometry):
        return None
    penetration = float(domain.conservative_obstacle.boundary.distance(geometry))
    if penetration > domain.planning_margin_m + 1e-8:
        return None
    contact = _boundary_node(point, domain)
    if contact is None:
        return None
    segment = LineString([np.asarray(point, dtype=float), contact])
    if not domain.physical_free.covers(segment):
        return None
    return contact, penetration


def route_assigned(guides, targets, assignment, room, wall_margin,
                   crowd_polygon, crowd_margin, people=None, limits=None):
    """Recompute paths for an existing guide-to-target identity mapping.

    A start inside the extra planning margin is not given to the visibility
    graph. When ``people`` and ``limits`` are supplied, a straight connection
    back to the conservative boundary is prefixed only after the same physical
    segment check used for execution. Without that evidence the start is rejected.
    """
    domain = navigation_domain(room, wall_margin, crowd_polygon, crowd_margin)
    free, obstacle = domain.free, domain.conservative_obstacle
    paths = {}
    lengths = {}
    guides = np.asarray(guides, dtype=float)
    for guide in np.flatnonzero(np.asarray(assignment) >= 0):
        target = int(assignment[guide])
        start = guides[guide]
        if target >= len(targets):
            raise ValueError('PATH_UNREACHABLE: assigned endpoint is outside conservative free space')
        target_ready = _prepare_start(targets[target], domain)
        prepared = _prepare_start(start, domain, guides, guide, people, limits, room)
        if target_ready is None or prepared is None:
            found = None if target_ready is None else _margin_contact(start, domain)
            checked = found is not None and found[1] > ENDPOINT_TOLERANCE_M and people is not None and limits is not None
            if checked:
                raise ValueError(
                    'PATH_UNREACHABLE: planning-margin connection fails physical clearance '
                    f'(guide {int(guide)} signed_distance={domain.signed_free_distance(start):.6g})'
                )
            raise ValueError(
                'PATH_UNREACHABLE: assigned endpoint is outside conservative free space '
                f'(guide {int(guide)} signed_distance={domain.signed_free_distance(start):.6g})'
            )
        planner_start, prefix = prepared
        guide_radius = None if limits is None else limits.guide_distance
        free_i, extra = _other_guide_space(domain, guides, int(guide), guide_radius)
        if not _within_free(free_i, planner_start):
            raise ValueError(
                'PATH_UNREACHABLE: assigned endpoint is outside conservative free space '
                f'(guide {int(guide)} signed_distance={domain.signed_free_distance(start):.6g})'
            )
        path, length = _shortest(planner_start, target_ready[0], free_i, obstacle, extra)
        if path is None:
            raise ValueError('PATH_UNREACHABLE: assigned guide-target path does not exist')
        if prefix is not None:
            path = np.vstack([prefix, path])
            length = float(length + np.linalg.norm(path[1] - path[0]))
        paths[int(guide)] = path
        lengths[int(guide)] = length
    return paths, lengths
