"""Verified finite-graph routing and reversible serial assembly.

Only observations, guide poses and known geometry enter this module. The old
executor and all frozen outcomes remain separate. A failed search is not an
infeasibility certificate.
"""
import heapq

import numpy as np
import shapely
from scipy.optimize import linear_sum_assignment
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from .execution import Execution
from .geometry import outer_disk


def tangent_polygon(center, radius, endpoints, sides=16, pad=1e-9):
    """Outer disk approximation with supporting normals facing both endpoints."""
    center = np.asarray(center, float)
    extra = np.asarray(endpoints, float).reshape(-1, 2) - center
    angles = np.r_[np.arange(sides) * 2 * np.pi / sides,
                   np.arctan2(extra[:, 1], extra[:, 0]) % (2 * np.pi)]
    angles = np.unique(angles)
    angles.sort()
    nxt = np.r_[angles[1:], angles[0] + 2 * np.pi]
    mid, half = (angles + nxt) / 2, (nxt - angles) / 2
    vertices = center + ((radius + pad) / np.cos(half))[:, None] * np.c_[np.cos(mid), np.sin(mid)]
    return Polygon(vertices)


def _vertices(geometry):
    pieces = [geometry] if geometry.geom_type == 'Polygon' else list(getattr(geometry, 'geoms', ()))
    arrays = []
    for piece in pieces:
        if piece.geom_type == 'Polygon':
            arrays.append(np.asarray(piece.exterior.coords[:-1]))
            arrays.extend(np.asarray(ring.coords[:-1]) for ring in piece.interiors)
    return np.vstack(arrays) if arrays else np.empty((0, 2))


def _clear_segments(start, ends, free, stationary, measurement, room, config):
    ends = np.asarray(ends, float).reshape(-1, 2)
    if not len(ends):
        return np.zeros(0, bool)
    start = np.asarray(start, float)
    move = ends - start
    denominator = np.maximum(np.sum(move * move, axis=1), 1e-30)
    okay = np.minimum(np.minimum(start, ends), np.asarray(room) - np.maximum(start, ends)).min(axis=1) >= config['wall_distance_m']
    sets = [(stationary, np.full(len(stationary), config['guide_distance_m']))]
    if measurement is not None:
        obs = measurement.observation
        sets.append((obs.positions, obs.radii + measurement.position_error_bound_m + config['guide_radius_m'] + config['clearance_m']))
    for positions, radii in sets:
        if not len(positions):
            continue
        relative = start - positions
        fraction = np.clip(-np.einsum('md,nd->mn', move, relative) / denominator[:, None], 0., 1.)
        closest = relative[None] + fraction[:, :, None] * move[:, None]
        okay &= np.all(np.linalg.norm(closest, axis=2) >= radii[None], axis=1)
    indices = np.flatnonzero(okay)
    if len(indices):
        lines = shapely.linestrings(np.stack([np.broadcast_to(start, (len(indices), 2)), ends[indices]], axis=1))
        valid = shapely.covers(free, lines)
        candidates = np.flatnonzero(valid)
        if len(candidates):
            valid[candidates] &= shapely.is_empty(shapely.difference(lines[candidates], free))
        okay[indices] = valid
    return okay


def find_path(start, target, current, moving, domain, room, config, measurement=None, adaptive=True):
    """A* on the declared finite graph, validating every candidate edge."""
    stationary = np.delete(current, moving, axis=0)
    start, target = np.asarray(start, float), np.asarray(target, float)
    levels = config.get('routing_sides', [16, 32, 64]) if adaptive else [16]
    attempts = []
    for sides in levels:
        disks = ([tangent_polygon(p, config['guide_distance_m'], [start, target], sides,
                                  config.get('routing_pad_m', 1e-9)) for p in stationary] if adaptive else
                 [outer_disk(p, config['guide_distance_m'], quad_segs=4) for p in stationary])
        occupied = unary_union(disks)
        margin = config.get('routing_interior_margin_m', 1e-6)
        if not np.isfinite(margin) or margin <= 0:
            raise ValueError('A strictly positive route construction margin is required')
        static_free = domain.free.buffer(-margin, quad_segs=16)
        free = static_free.difference(occupied) if disks else static_free
        attempt = {'sides': sides, 'adaptive_supporting_normals': adaptive}
        attempts.append(attempt)
        if not free.covers(Point(start)) or not free.covers(Point(target)):
            attempt['result'] = 'endpoint_polygon_rejection'
            continue
        if free.geom_type == 'MultiPolygon':
            parts = list(free.geoms)
            starts = {i for i,p in enumerate(parts) if p.covers(Point(start))}
            ends = {i for i,p in enumerate(parts) if p.covers(Point(target))}
            connected = set(starts)
            while True:
                more = {j for i in connected for j,p in enumerate(parts) if parts[i].intersects(p)}
                if more <= connected:
                    break
                connected |= more
            if not connected & ends:
                attempt['result'] = 'separated_planning_components'
                continue
            free = unary_union([parts[i] for i in sorted(connected)])
        if not (_clear_segments(start, [start], free, stationary, measurement, room, config)[0]
                and _clear_segments(target, [target], free, stationary, measurement, room, config)[0]):
            attempt['result'] = 'analytic_endpoint_rejection'
            continue
        shapely.prepare(free)
        if _clear_segments(start, [target], free, stationary, measurement, room, config)[0]:
            return np.asarray([start, target]), {'attempts': attempts, 'direct': True, 'expanded': 0}
        vertices = _vertices(free)
        if len(vertices):
            vertices = vertices[shapely.covers(free, shapely.points(vertices))]
            vertices = np.unique(vertices, axis=0)
        nodes = np.vstack([start, target, vertices])
        limit = config.get('routing_max_vertices', 20000)
        if len(nodes) > limit:
            attempt.update(result='vertex_budget', vertices=len(nodes))
            continue
        distances = np.full(len(nodes), np.inf)
        previous = np.full(len(nodes), -1, int)
        distances[0] = 0.
        heuristic = np.linalg.norm(nodes - target, axis=1)
        queue = [(float(heuristic[0]), 0., 0)]
        expanded = 0
        while queue:
            _, distance, vertex = heapq.heappop(queue)
            if distance != distances[vertex]:
                continue
            if vertex == 1:
                break
            weights = np.linalg.norm(nodes - nodes[vertex], axis=1)
            possible = (distance + weights < distances) & (distance + weights + heuristic <= distances[1])
            possible[vertex] = False
            indices = np.flatnonzero(possible)
            valid = _clear_segments(nodes[vertex], nodes[indices], free, stationary, measurement, room, config)
            expanded += 1
            for neighbor in indices[valid]:
                candidate = distance + weights[neighbor]
                if candidate < distances[neighbor]:
                    distances[neighbor] = candidate
                    previous[neighbor] = vertex
                    heapq.heappush(queue, (float(candidate + heuristic[neighbor]), float(candidate), int(neighbor)))
        attempt.update(vertices=len(nodes), expanded=expanded)
        if np.isfinite(distances[1]):
            order, vertex = [], 1
            while vertex >= 0:
                order.append(vertex)
                vertex = previous[vertex]
            path = nodes[order[::-1]]
            # Final full-path check is separate from the graph relaxation.
            if not all(_clear_segments(a, [b], free, stationary, measurement, room, config)[0] for a, b in zip(path[:-1], path[1:])):
                raise RuntimeError('Independent path validation disagrees with graph')
            attempt['result'] = 'path'
            return path, {'attempts': attempts, 'direct': False, 'expanded': expanded}
        attempt['result'] = 'finite_graph_exhausted'
    return None, {'attempts': attempts, 'reason': 'bounded_verified_graph_search_failed'}


def _initial_ok(current, domain, room, config, measurement):
    if not np.isfinite(current).all() or not all(domain.free.covers(Point(p)) for p in current):
        return False
    # Analytic clearance also checks every observation, rather than relying on
    # the same boolean predicate that admitted the original unsafe edge.
    for i, point in enumerate(current):
        if not _clear_segments(point, [point], domain.free, np.delete(current, i, axis=0), measurement, room, config)[0]:
            return False
    return True


def execute(guides, targets, domain, room, config, measurement=None, policy='adaptive_reverse'):
    if policy not in ('verified_q4_forward', 'adaptive_forward', 'adaptive_reverse'):
        raise ValueError('Unknown verified routing policy')
    current, targets = np.array(guides, float, copy=True), np.asarray(targets, float).reshape(-1, 2)
    if len(targets) > len(current) or not np.isfinite(targets).all():
        raise ValueError('Invalid target array or resource budget')
    assignment = np.full(len(current), -1, int)
    if len(targets):
        first, second = linear_sum_assignment(np.linalg.norm(current[:, None] - targets[None], axis=2))
        assignment[first] = second
    trajectory, paths = [current.copy()], []
    def finish(status, evidence):
        positions = np.asarray(trajectory)
        return Execution(positions, np.diff(positions, axis=0) / config['dt_s'], assignment, status,
                         tuple(paths), {'routing_policy': policy, **evidence})
    if not _initial_ok(current, domain, room, config, measurement):
        return finish('INITIALIZATION_INVALID', {'reason': 'independent_observed_initial_clearance'})
    active = np.flatnonzero(assignment >= 0).tolist()
    if np.array_equal(current[assignment >= 0], targets[assignment[assignment >= 0]]):
        if config['hold_steps'] > int(np.floor(config['deadline_s'] / config['dt_s'])):
            return finish('TIMEOUT', {'reason': 'terminal_hold'})
        trajectory.extend(current.copy() for _ in range(config['hold_steps']))
        return finish('ARRIVED', {'held_exact_existing_layout': True, 'queries': []})
    schedule, diagnostics = [], []
    if policy == 'adaptive_reverse':
        reverse = current.copy()
        reverse[assignment >= 0] = targets[assignment[assignment >= 0]]
        if not _initial_ok(reverse, domain, room, config, measurement):
            return finish('TARGET_CONFIGURATION_INVALID', {'reason': 'independent_observed_goal_clearance'})
        pending = active.copy()
        while pending:
            accepted = False
            for guide in pending:
                path, evidence = find_path(reverse[guide], guides[guide], reverse, guide, domain, room, config, measurement, adaptive=True)
                diagnostics.append({'guide': guide, 'reverse': True, **evidence})
                if path is not None:
                    schedule.append((guide, path[::-1].copy()))
                    reverse[guide] = guides[guide]
                    pending.remove(guide)
                    accepted = True
                    break
            if not accepted:
                return finish('SERIAL_ASSEMBLY_UNRESOLVED', {'pending_guides': pending, 'queries': diagnostics})
        schedule.reverse()
    else:
        schedule = [(guide, None) for guide in active]
    maximum = int(np.floor(config['deadline_s'] / config['dt_s']))
    for guide, planned in schedule:
        if planned is None:
            path, evidence = find_path(current[guide], targets[assignment[guide]], current, guide,
                                      domain, room, config, measurement, adaptive=policy != 'verified_q4_forward')
            diagnostics.append({'guide': guide, 'reverse': False, **evidence})
            if path is None:
                return finish('PATH_NOT_FOUND', {'guide': guide, 'queries': diagnostics})
        else:
            path = planned.copy()
            path[0] = current[guide]
            path[-1] = targets[assignment[guide]]
        stationary = np.delete(current, guide, axis=0)
        # Reversal changes the arithmetic of interpolation, so recheck all
        # complete edges against the actual current state before moving.
        if not all(_clear_segments(a, [b], domain.free, stationary, measurement, room, config)[0] for a, b in zip(path[:-1], path[1:])):
            return finish('PATH_SAFETY_REJECTED', {'guide': guide, 'reason': 'forward_final_edge_validation', 'queries': diagnostics})
        paths.append({'guide': guide, 'target': int(assignment[guide]), 'points': path.tolist(),
                      'length_m': float(np.linalg.norm(np.diff(path, axis=0), axis=1).sum())})
        for a, b in zip(path[:-1], path[1:]):
            steps = max(1, int(np.ceil(np.linalg.norm(b - a) / (config['max_speed_m_s'] * config['dt_s']))))
            for step in range(1, steps + 1):
                if len(trajectory) - 1 >= maximum:
                    return finish('TIMEOUT', {'guide': guide, 'queries': diagnostics})
                following = a + (b - a) * (step / steps)
                if not _clear_segments(current[guide], [following], domain.free, stationary, measurement, room, config)[0]:
                    line = LineString([current[guide], following])
                    return finish('PATH_SAFETY_REJECTED', {'guide': guide, 'reason': 'actual_interpolated_interval',
                        'start': current[guide].tolist(), 'following': following.tolist(),
                        'domain_covers': bool(domain.free.covers(line)),
                        'outside_domain_length_m': float(line.difference(domain.free).length), 'queries': diagnostics})
                current[guide] = following
                trajectory.append(current.copy())
    for _ in range(config['hold_steps']):
        if len(trajectory) - 1 >= maximum:
            return finish('TIMEOUT', {'reason': 'terminal_hold', 'queries': diagnostics})
        trajectory.append(current.copy())
    return finish('ARRIVED', {'queries': diagnostics})
