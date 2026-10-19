"""Static visibility-graph navigation and reachable assignment."""
from dataclasses import dataclass
import hashlib
import heapq
import weakref

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


class NavigationFailure(ValueError):
    """Expected query failure with evidence, never an unqualified no-path proof."""
    def __init__(self, kind, message, **evidence):
        super().__init__('PATH_UNREACHABLE: '+message)
        self.diagnosis={'kind':kind,**evidence}


def _component(geometry, point):
    parts=[geometry] if geometry.geom_type=='Polygon' else getattr(geometry,'geoms',[])
    for index,part in enumerate(parts):
        if part.geom_type=='Polygon' and part.covers(Point(point)):
            return index
    return None


def _occupation(guides, point, exclude, radius):
    if radius is None:
        return []
    return [{'guide':int(i),'center_distance_m':float(np.linalg.norm(g-point)),
             'threshold_m':float(radius)} for i,g in enumerate(guides)
            if i!=exclude and np.linalg.norm(g-point)<radius]


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


class TargetDomainFailure(ValueError):
    """Static target rejection, distinct from a failed path search."""

    def __init__(self, report):
        super().__init__('TARGET_DOMAIN_INVALID: target outside static conservative navigation domain')
        self.diagnosis = {'kind': 'static_target_invalid', **report}


def target_domain_check(domain, targets, arc_positions=None):
    """One target contract for deployment acceptance and navigation entrances.

    Other guides are deliberately absent. The existing endpoint tolerance only
    admits numerical contact with the conservative boundary, inside physical
    free space. It never admits a physically invalid endpoint.
    """
    targets = np.asarray(targets, dtype=float)
    if targets.ndim != 2 or targets.shape[1] != 2 or not np.isfinite(targets).all():
        raise ValueError('PLAN_INVALID: targets must be finite Nx2 coordinates')
    arcs = None if arc_positions is None else np.asarray(arc_positions, dtype=float)
    if arcs is not None and (arcs.shape != (len(targets),) or not np.isfinite(arcs).all()):
        raise ValueError('PLAN_INVALID: target arc coordinates must be finite and paired')
    records = []
    for index, target in enumerate(targets):
        point = Point(target)
        physical = bool(domain.physical_free.covers(point))
        signed = domain.signed_free_distance(target)
        covered = bool(domain.free.covers(point))
        accepted = physical and (covered or signed >= -ENDPOINT_TOLERANCE_M)
        records.append({
            'target': index, 'coordinates': target.tolist(),
            'arc_m': None if arcs is None else float(arcs[index]),
            'signed_free_distance_m': signed, 'accepted': accepted,
            'reason': ('covered' if covered and accepted else 'numerical_boundary_contact' if accepted
                       else 'outside_physical_free' if not physical
                       else 'inside_conservative_obstacle'),
        })
    invalid = [record['target'] for record in records if not record['accepted']]
    return {'accepted': not invalid, 'invalid_target_indices': invalid,
            'query_domain': 'static_conservative_navigation',
            'geometry_sha256': domain.geometry_sha256,
            'endpoint_tolerance_m': ENDPOINT_TOLERANCE_M, 'targets': records}


def require_target_domain(domain, targets, arc_positions=None):
    report = target_domain_check(domain, targets, arc_positions)
    if not report['accepted']:
        raise TargetDomainFailure(report)
    return report


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


def _visibility_graph(free, obstacle, extra_nodes=()):
    """Build static edges once for all guide–target queries on this exact domain."""
    nodes=[]
    if obstacle is not None and not obstacle.is_empty and obstacle.geom_type == 'Polygon':
        nodes.extend(np.asarray(p) for p in obstacle.exterior.coords[:-1])
    nodes.extend(np.asarray(point, dtype=float) for point in extra_nodes)
    edges=[[] for _ in nodes]
    for i in range(len(nodes)):
        for j in range(i):
            segment=LineString([nodes[i],nodes[j]])
            if free.covers(segment) and (obstacle is None or not segment.crosses(obstacle)):
                length=float(np.linalg.norm(nodes[i]-nodes[j]))
                edges[i].append((j,length)); edges[j].append((i,length))
    return nodes,edges


def _shortest(start, target, free, obstacle, extra_nodes=(), graph=None):
    direct = LineString([start, target])
    if free.covers(direct) and (obstacle is None or not direct.crosses(obstacle)):
        return np.asarray([start, target], dtype=float), float(direct.length)
    base_nodes,base_edges=(_visibility_graph(free,obstacle,extra_nodes) if graph is None else graph)
    nodes = [np.asarray(start), np.asarray(target), *base_nodes]
    n = len(nodes); edges = [[] for _ in nodes]
    for i,neighbors in enumerate(base_edges):
        edges[i+2]=[(j+2,length) for j,length in neighbors]
    for i in range(2,n):
        for j in (0,1):
            segment=LineString([nodes[i],nodes[j]])
            if free.covers(segment) and (obstacle is None or not segment.crosses(obstacle)):
                d=float(np.linalg.norm(nodes[i]-nodes[j])); edges[i].append((j,d)); edges[j].append((i,d))
    for neighbors in edges:
        neighbors.sort(key=lambda edge:edge[0])
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

    A point ``free.covers`` rejects but that lies on the boundary sample is
    connected through the existing 1e-8 m planner node. The executed state stays
    at ``point``. A point in the planning shell is connected only when the
    physical segment check passes.
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
    # A covered sample a few ulps from the true state is a planner node only.
    # The executed path still begins at ``point``; dropping it makes installation
    # reject an otherwise valid route.
    if float(np.linalg.norm(contact - point)) <= 1e-15:
        return np.asarray(point, dtype=float), None
    return contact, np.asarray(point, dtype=float)


def navigate_and_assign(guides, targets, room, wall_margin, crowd_polygon, crowd_margin,
                        people=None, limits=None):
    domain = navigation_domain(room, wall_margin, crowd_polygon, crowd_margin)
    require_target_domain(domain, targets)
    free, obstacle = domain.free, domain.conservative_obstacle
    # Call-local reuse: geometry changes cannot accidentally reuse stale edges.
    graph = _visibility_graph(free, obstacle)
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
            path, length = _shortest(planner_start, planner_target, free, obstacle, graph=graph)
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


def segment_in_free_sampled(start, end, domain):
    """Historical five-sample predicate.

    Retained so a penetrating chord can be shown to pass the old test, and so
    the vertex-arrival ablation can run without the complete check. It is not
    the predicate used for control.
    """
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


_EDGE_CACHE = weakref.WeakKeyDictionary()


def _boundary_edges(obstacle):
    """Edge endpoints of a polygonal obstacle, including holes. No buffer."""
    if obstacle is None or obstacle.is_empty:
        return np.zeros((0, 2)), np.zeros((0, 2))
    polygons = []
    if obstacle.geom_type == 'Polygon':
        polygons = [obstacle]
    elif obstacle.geom_type == 'MultiPolygon':
        polygons = list(obstacle.geoms)
    else:
        polygons = [piece for piece in getattr(obstacle, 'geoms', []) if piece.geom_type == 'Polygon']
    starts, ends = [], []
    for polygon in polygons:
        for ring in (polygon.exterior, *list(polygon.interiors)):
            coords = np.asarray(ring.coords, dtype=float)
            for first, second in zip(coords[:-1], coords[1:]):
                if float(np.linalg.norm(second - first)) > 1e-15:
                    starts.append(first)
                    ends.append(second)
    if not starts:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return np.asarray(starts, dtype=float), np.asarray(ends, dtype=float)


def _obstacle_edges(domain):
    """Reuse one domain's boundary edges across steps. This is not a visibility graph."""
    try:
        cached = _EDGE_CACHE.get(domain)
    except TypeError:
        cached = None
    if cached is None:
        cached = _boundary_edges(domain.conservative_obstacle)
        try:
            _EDGE_CACHE[domain] = cached
        except TypeError:
            pass
    return cached


def _point_edge_distances(point, starts, ends):
    if len(starts) == 0:
        return np.zeros(0)
    point = np.asarray(point, dtype=float)
    edges = ends - starts
    length2 = np.sum(edges * edges, axis=1)
    scale = np.clip(np.sum((point - starts) * edges, axis=1) / np.maximum(length2, 1e-30), 0.0, 1.0)
    closest = starts + scale[:, None] * edges
    return np.linalg.norm(closest - point, axis=1)


def _flatten_intersection(geometry):
    if geometry.is_empty:
        return
    kind = geometry.geom_type
    if kind in ('Point', 'LineString', 'Polygon'):
        yield geometry
        return
    for piece in getattr(geometry, 'geoms', ()):
        yield from _flatten_intersection(piece)


def _portion_within_tolerance(start, end, starts, ends, tolerance):
    """Whether every point of a straight piece inside the obstacle is within tolerance.

    Distance to one edge is convex, so an edge that covers both endpoints covers
    the piece. Otherwise the 1-Lipschitz bound or bisection certifies the claim.
    ``witness`` is a lower bound on the deepest point found.
    """
    stack = [(np.asarray(start, dtype=float), np.asarray(end, dtype=float))]
    witness = 0.0
    visits = 0
    while stack:
        first, second = stack.pop()
        distance_first = _point_edge_distances(first, starts, ends)
        distance_second = _point_edge_distances(second, starts, ends)
        if len(distance_first) == 0:
            return False, np.inf
        pen_first = float(distance_first.min())
        pen_second = float(distance_second.min())
        witness = max(witness, pen_first, pen_second)
        if pen_first > tolerance or pen_second > tolerance:
            return False, witness
        if np.any((distance_first <= tolerance) & (distance_second <= tolerance)):
            continue
        length = float(np.linalg.norm(second - first))
        if (pen_first + pen_second + length) / 2.0 <= tolerance:
            continue
        midpoint = (first + second) * 0.5
        pen_mid = float(_point_edge_distances(midpoint, starts, ends).min())
        witness = max(witness, pen_mid)
        if pen_mid > tolerance:
            return False, witness
        visits += 1
        if visits > 8192:
            return False, max(witness, tolerance + length / 2.0)
        stack.append((first, midpoint))
        stack.append((midpoint, second))
    return True, witness


def _penetration_within_tolerance(segment, domain):
    """Complete tolerance test for a segment already known to lie in physical free space."""
    inside = segment.intersection(domain.conservative_obstacle)
    if inside.is_empty:
        return True, 0.0
    starts, ends = _obstacle_edges(domain)
    witness = 0.0
    for piece in _flatten_intersection(inside):
        if piece.geom_type == 'Point':
            pen = float(_point_edge_distances(np.asarray(piece.coords[0]), starts, ends).min()) if len(starts) else np.inf
            witness = max(witness, pen)
            if pen > ENDPOINT_TOLERANCE_M:
                return False, witness
        elif piece.geom_type == 'LineString':
            coords = np.asarray(piece.coords, dtype=float)
            for first, second in zip(coords[:-1], coords[1:]):
                accepted, pen = _portion_within_tolerance(first, second, starts, ends, ENDPOINT_TOLERANCE_M)
                witness = max(witness, pen)
                if not accepted:
                    return False, witness
        else:
            return False, np.inf
    return True, witness


def segment_in_free(start, end, domain, audit=None):
    """True when the whole segment stays in free space, up to the existing 1e-7 m tolerance.

    ``free.covers`` accepts a segment on the boundary. A segment that leaves
    physical free space is rejected. The remaining case is contact with the
    conservative obstacle: the old five-point sample is not used. No extra
    free-space buffer is added. ``audit``, when callable, receives disagreements
    with the five-point predicate and does not change the returned decision.
    """
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    segment = LineString([start, end])
    if domain.free.covers(segment):
        return True
    if not domain.physical_free.covers(segment):
        return False
    accepted, witness = _penetration_within_tolerance(segment, domain)
    if audit is not None:
        sampled = segment_in_free_sampled(start, end, domain)
        if bool(sampled) != bool(accepted):
            worst_sample = 0.0
            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
                sample = (1.0 - fraction) * start + fraction * end
                worst_sample = max(worst_sample, max(0.0, -float(domain.signed_free_distance(sample))))
            if sampled and not accepted:
                reason = 'five_samples_within_tolerance_but_segment_penetrates'
            else:
                reason = 'sample_deeper_than_tolerance_but_segment_within_tolerance'
            audit({
                'start': start.tolist(), 'end': end.tolist(),
                'sampled': bool(sampled), 'complete': bool(accepted),
                'sample_penetration_m': float(worst_sample),
                'witness_penetration_m': None if not np.isfinite(witness) else float(witness),
                'tolerance_m': ENDPOINT_TOLERANCE_M,
                'reason': reason,
            })
    return bool(accepted)


def _outward_free_direction(nearest, domain):
    """Unit step from an obstacle-boundary sample into conservative free space.

    Used only when the guide is already numerically on that sample, so the
    vector from the guide to the sample has no direction. The executed state
    is not moved.
    """
    boundary = domain.conservative_obstacle.boundary
    pieces = [boundary] if boundary.geom_type == 'LineString' else list(getattr(boundary, 'geoms', []))
    nearest = np.asarray(nearest, dtype=float)
    best = None
    best_distance = np.inf
    for piece in pieces:
        coords = np.asarray(piece.coords, dtype=float)
        for start, end in zip(coords[:-1], coords[1:]):
            edge = end - start
            length = float(np.linalg.norm(edge))
            if length <= 1e-12:
                continue
            tangent = edge / length
            projected = float(np.clip(np.dot(nearest - start, tangent), 0.0, length))
            closest = start + projected * tangent
            distance = float(np.linalg.norm(closest - nearest))
            if distance < best_distance:
                best_distance = distance
                best = np.array([tangent[1], -tangent[0]], dtype=float)
    if best is None:
        return None
    if domain.conservative_obstacle.covers(Point(nearest + 1e-4 * best)):
        best = -best
    norm = float(np.linalg.norm(best))
    if norm <= 1e-15:
        return None
    return best / norm


def _boundary_node(point, domain):
    """Closest conservative-obstacle boundary sample that free space accepts.

    The sample is a planner node. The guide state stays at ``point``.
    A sample that coincides with ``point`` is stepped 1e-8 m into free space;
    ``free.covers`` can reject that exact boundary coordinate.
    """
    boundary = domain.conservative_obstacle.boundary
    nearest = np.asarray(boundary.interpolate(boundary.project(Point(point))).coords[0], dtype=float)
    if domain.free.covers(Point(nearest)):
        return nearest
    point = np.asarray(point, dtype=float)
    direction = nearest - point
    length = float(np.linalg.norm(direction))
    if length <= 1e-15:
        direction = _outward_free_direction(nearest, domain)
        if direction is None:
            return None
        nudged = nearest + BOUNDARY_NODE_NUDGE_M * direction
    else:
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
    require_target_domain(domain, targets)
    free, obstacle = domain.free, domain.conservative_obstacle
    paths = {}
    lengths = {}
    guides = np.asarray(guides, dtype=float)
    for guide in np.flatnonzero(np.asarray(assignment) >= 0):
        target = int(assignment[guide])
        start = guides[guide]
        if target >= len(targets):
            raise NavigationFailure('static_endpoint_invalid','assigned endpoint is outside conservative free space',
                                    guide=int(guide),target=target,query_domain='static')
        target_ready = _prepare_start(targets[target], domain)
        prepared = _prepare_start(start, domain, guides, guide, people, limits, room)
        if target_ready is None or prepared is None:
            found = None if target_ready is None else _margin_contact(start, domain)
            checked = found is not None and found[1] > ENDPOINT_TOLERANCE_M and people is not None and limits is not None
            if checked:
                raise NavigationFailure(
                    'unsafe_connection','planning-margin connection fails physical clearance '
                    f'(guide {int(guide)} signed_distance={domain.signed_free_distance(start):.6g})',
                    guide=int(guide),target=target,query_domain='static',
                )
            raise NavigationFailure(
                'static_endpoint_invalid','assigned endpoint is outside conservative free space '
                f'(guide {int(guide)} signed_distance={domain.signed_free_distance(start):.6g})',
                guide=int(guide),target=target,query_domain='static',
                start=start.tolist(),end=np.asarray(targets[target]).tolist(),
                start_signed_m=domain.signed_free_distance(start),
                target_signed_m=domain.signed_free_distance(targets[target]),
            )
        planner_start, prefix = prepared
        static_components=[domain.component_index(planner_start),domain.component_index(target_ready[0])]
        if static_components[0]!=static_components[1]:
            raise NavigationFailure('static_components_disconnected',
                'assigned endpoints belong to different static planning components',
                guide=int(guide),target=target,query_domain='static',components=static_components,
                geometry_sha256=domain.geometry_sha256,proof_scope='current conservative planning domain only')
        guide_radius = None if limits is None else limits.guide_distance
        free_i, extra = _other_guide_space(domain, guides, int(guide), guide_radius)
        if not _within_free(free_i, planner_start):
            raise NavigationFailure(
                'current_guide_occupation','assigned start is currently occupied by other guides',
                guide=int(guide),target=target,query_domain='static_with_current_guides',endpoint='start',
                occupants=_occupation(guides,planner_start,guide,guide_radius),release_guaranteed=False,
            )
        if not _within_free(free_i, target_ready[0]):
            raise NavigationFailure(
                'current_guide_occupation','assigned target temporarily occupied by other guides '
                f'(guide {int(guide)} target {target})',
                guide=int(guide),target=target,query_domain='static_with_current_guides',endpoint='target',
                occupants=_occupation(guides,target_ready[0],guide,guide_radius),release_guaranteed=False,
            )
        dynamic_components=[_component(free_i,planner_start),_component(free_i,target_ready[0])]
        if dynamic_components[0] is not None and dynamic_components[1] is not None and dynamic_components[0]!=dynamic_components[1]:
            raise NavigationFailure('current_guide_barrier',
                'current guide obstacles separate assigned endpoints in the temporary domain',
                guide=int(guide),target=target,query_domain='static_with_current_guides',
                components=dynamic_components,release_guaranteed=False)
        path, length = _shortest(planner_start, target_ready[0], free_i, obstacle, extra)
        if path is None:
            raise NavigationFailure('search_not_found','assigned path search found no route in current conservative free space',
                                    guide=int(guide),target=target,query_domain='static_with_current_guides' if limits else 'static',
                                    infeasibility_proven=False)
        if prefix is not None:
            path = np.vstack([prefix, path])
            length = float(length + np.linalg.norm(path[1] - path[0]))
        paths[int(guide)] = path
        lengths[int(guide)] = length
    return paths, lengths
