"""A deliberately simple serial deployment policy using measurements only.

Assignment uses Euclidean Hungarian cost; fixed assignment and guide-ID order
are not complete multi-robot path planning. Other robots, including idle ones,
are stationary obstacles. Every saved interval has constant velocity.
"""
from dataclasses import dataclass

import numpy as np
import shapely
from scipy.optimize import linear_sum_assignment
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from udc.navigation.paths import _shortest
from .geometry import ScientificFailure, outer_disk


@dataclass(frozen=True)
class Execution:
    positions: np.ndarray
    velocities: np.ndarray
    assignment: np.ndarray
    status: str
    paths: tuple
    evidence: dict


def _graph(free, obstacle):
    """Visibility nodes for ALL components; vectorized exact segment predicates."""
    pieces = [obstacle] if obstacle.geom_type == 'Polygon' else list(getattr(obstacle, 'geoms', ()))
    nodes = []
    for piece in pieces:
        if piece.geom_type == 'Polygon':
            nodes.extend(piece.exterior.coords[:-1])
            for ring in piece.interiors:
                nodes.extend(ring.coords[:-1])
    nodes = np.asarray(nodes, dtype=float).reshape(-1, 2)
    if len(nodes):
        nodes = nodes[shapely.covers(free, shapely.points(nodes))]
    first, second = np.tril_indices(len(nodes), -1)
    edges = [[] for _ in nodes]
    # Bound temporary geometry allocations on large guide sets.
    for offset in range(0, len(first), 8192):
        i, j = first[offset:offset + 8192], second[offset:offset + 8192]
        segments = shapely.linestrings(np.stack([nodes[i], nodes[j]], axis=1))
        valid = shapely.covers(free, segments)
        for a, b, length in zip(i[valid], j[valid], np.linalg.norm(nodes[i[valid]] - nodes[j[valid]], axis=1)):
            edges[a].append((int(b), float(length)))
            edges[b].append((int(a), float(length)))
    return list(nodes), edges


def execute(guides, targets, domain, room, config):
    """No truth or evaluator output can influence assignment or motion."""
    current = np.array(guides, dtype=float, copy=True)
    targets = np.asarray(targets, dtype=float)
    assignment = np.full(len(current), -1, dtype=int)
    trajectory, paths = [current.copy()], []
    dt, speed = config['dt_s'], config['max_speed_m_s']
    maximum_intervals = int(np.floor(config['deadline_s'] / dt))
    if len(targets) > len(current):
        raise ValueError('More targets than available robots')
    if len(targets):
        rows, columns = linear_sum_assignment(np.linalg.norm(current[:, None] - targets[None, :], axis=2))
        assignment[rows] = columns
    # Initial robots must be safe against all bounded observations, not truth.
    for i, point in enumerate(current):
        if not domain.free.covers(Point(point)):
            return Execution(np.asarray(trajectory), np.empty((0, len(current), 2)), assignment,
                             'INITIALIZATION_INVALID', (), {'guide': i, 'reason': 'outside_observed_free_space'})
    if len(current) > 1:
        pair = np.linalg.norm(current[:, None] - current[None, :], axis=2)
        pair[np.diag_indices(len(current))] = np.inf
        if pair.min() < config['guide_distance_m']:
            return Execution(np.asarray(trajectory), np.empty((0, len(current), 2)), assignment,
                             'INITIALIZATION_INVALID', (), {'reason': 'guide_initial_separation'})
    status, evidence = 'ARRIVED', {}
    for guide in np.flatnonzero(assignment >= 0):
        stationary = [outer_disk(point, config['guide_distance_m'], quad_segs=4)
                      for j, point in enumerate(current) if j != guide]
        obstacle = unary_union([domain.conservative_obstacle, *stationary])
        free = domain.free.difference(unary_union(stationary)) if stationary else domain.free
        target = targets[assignment[guide]]
        if not free.covers(Point(current[guide])) or not free.covers(Point(target)):
            status, evidence = 'PATH_NOT_FOUND', {'guide': int(guide), 'reason': 'fixed_endpoint_occupied'}
            break
        direct = LineString([current[guide], target])
        graph = None if free.covers(direct) else _graph(free, obstacle)
        path, length = _shortest(current[guide], target, free, obstacle, graph=graph)
        if path is None:
            status, evidence = 'PATH_NOT_FOUND', {'guide': int(guide), 'reason': 'finite_visibility_graph'}
            break
        # Reject complete illegal edges; no interpolation can cut a corner.
        if not all(free.covers(LineString([a, b])) for a, b in zip(path[:-1], path[1:])):
            raise ValueError('Visibility graph returned a segment outside its free space')
        if config.get('independent_segment_gate', False):
            from .path_gate import segment_gate
            rejected = next((gate_evidence for a, b in zip(path[:-1], path[1:])
                             if (gate_evidence := segment_gate(a, b, current, guide, free, room, config)) is not None), None)
            if rejected is not None:
                status, evidence = 'PATH_SAFETY_REJECTED', {'guide': int(guide), **rejected}
                break
        paths.append({'guide': int(guide), 'target': int(assignment[guide]),
                      'points': path.tolist(), 'length_m': float(length)})
        for a, b in zip(path[:-1], path[1:]):
            distance = float(np.linalg.norm(b - a))
            steps = max(1, int(np.ceil(distance / (speed * dt))))
            for step in range(1, steps + 1):
                if len(trajectory) - 1 >= maximum_intervals:
                    status, evidence = 'TIMEOUT', {'guide': int(guide), 'deadline_s': config['deadline_s']}
                    break
                current[guide] = a + (b - a) * (step / steps)
                trajectory.append(current.copy())
            if status == 'TIMEOUT':
                break
        if status == 'TIMEOUT':
            break
    if status == 'ARRIVED':
        for _ in range(config['hold_steps']):
            if len(trajectory) - 1 >= maximum_intervals:
                status, evidence = 'TIMEOUT', {'reason': 'terminal_hold', 'deadline_s': config['deadline_s']}
                break
            trajectory.append(current.copy())
    positions = np.asarray(trajectory)
    velocities = np.diff(positions, axis=0) / dt
    return Execution(positions, velocities, assignment, status, tuple(paths), evidence)
