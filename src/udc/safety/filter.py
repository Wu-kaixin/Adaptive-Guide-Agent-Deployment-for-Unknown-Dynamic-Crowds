"""Velocity projection and independent checks of the executed straight segments."""
from dataclasses import dataclass
from time import perf_counter

import numpy as np
from shapely.geometry import Point


@dataclass(frozen=True)
class Safety:
    guide_distance: float
    crowd_distance: float
    wall_distance: float
    max_speed: float
    numeric_tolerance: float = 1e-7


@dataclass(frozen=True)
class SafetyResult:
    velocity: np.ndarray
    status: str
    residual: float
    iterations: int
    correction_norm: float
    min_predicted_clearance: float
    solve_time: float
    fallback_used: bool


def path_clearances(start, end, people, room):
    """Exact closest approach on a constant-velocity execution interval."""
    def closest(relative, motion):
        denominator = np.sum(motion**2, axis=-1)
        t = np.clip(-np.sum(relative*motion, axis=-1)/np.maximum(denominator, 1e-30), 0, 1)
        return float(np.linalg.norm(relative+t[..., None]*motion, axis=-1).min())

    motion = end-start
    crowd = closest(start[:, None]-people[None, :], motion[:, None])
    i, j = np.triu_indices(len(start), 1)
    pair = closest(start[i]-start[j], motion[i]-motion[j]) if len(i) else None
    wall = float(np.minimum(np.stack([start, end]), np.asarray(room)-np.stack([start, end])).min())
    return {'crowd': crowd, 'guides': pair, 'walls': wall}


def is_safe(clearances, limits):
    tol = limits.numeric_tolerance
    return (clearances['crowd'] >= limits.crowd_distance-tol
            and clearances['walls'] >= limits.wall_distance-tol
            and (clearances['guides'] is None or clearances['guides'] >= limits.guide_distance-tol))


def _obstacle_support(point, obstacle):
    """Outward unit normal and signed clearance of a conservative obstacle.

    Positive clearance is outside the obstacle. Zero is the boundary. Negative
    clearance is penetration into the planning obstacle, not a physical overlap.
    """
    point = np.asarray(point, dtype=float)
    geometry = Point(point)
    boundary = obstacle.boundary
    distance = float(boundary.distance(geometry))
    nearest = np.asarray(boundary.interpolate(boundary.project(geometry)).coords[0], dtype=float)
    offset = point - nearest
    norm = float(np.linalg.norm(offset))
    inside = bool(obstacle.covers(geometry) and distance > 1e-9)
    if norm > 1e-9:
        normal = (-offset if inside else offset) / norm
    else:
        normal = _outward_edge_normal(boundary, nearest, obstacle)
    return normal, (-distance if inside else distance)


def _outward_edge_normal(boundary, nearest, obstacle):
    pieces = [boundary] if boundary.geom_type == 'LineString' else list(boundary.geoms)
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
                best = np.array([tangent[1], -tangent[0]])
    if best is None:
        return np.array([1.0, 0.0])
    probe = nearest + 1e-4 * best
    if obstacle.covers(Point(probe)):
        best = -best
    return best / max(float(np.linalg.norm(best)), 1e-30)


def _penetration(point, obstacle):
    geometry = Point(point)
    if not obstacle.covers(geometry):
        return 0.0
    return float(obstacle.boundary.distance(geometry))


def _planning_progress_ok(start, end, obstacle):
    """The step may ride the boundary but must not penetrate any farther."""
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    start_penetration = _penetration(start, obstacle)
    worst = start_penetration
    for fraction in np.linspace(0.0, 1.0, 9):
        sample = (1.0 - fraction) * start + fraction * end
        worst = max(worst, _penetration(sample, obstacle))
    return worst <= start_penetration + 1e-7


def _project_velocity(nominal, matrix, bounds, limits):
    n = len(nominal)
    vector = nominal.ravel().copy()
    corrections = np.zeros((len(bounds) + n, 2 * n))
    for iteration in range(1, 201):
        for k, (row, bound) in enumerate(zip(matrix, bounds)):
            shifted = vector + corrections[k]
            projected = shifted + max(0., bound - row @ shifted) / (row @ row) * row
            corrections[k] = shifted - projected
            vector = projected
        for i in range(n):
            k = len(bounds) + i
            shifted = vector + corrections[k]
            projected = shifted.copy()
            block = projected[2 * i:2 * i + 2]
            block *= min(1., limits.max_speed / max(np.linalg.norm(block), 1e-30))
            corrections[k] = shifted - projected
            vector = projected
        residual = np.max(bounds - matrix @ vector, initial=0.)
        if residual <= 1e-9:
            return vector.reshape(n, 2), float(residual), iteration, True
    return vector.reshape(n, 2), float(np.max(bounds - matrix @ vector, initial=0.)), 200, False


def safe_velocity(positions, nominal, people, room, dt, limits, navigation_obstacle=None):
    """Project onto separation half-spaces and per-guide speed balls.

    The filter is a numerical feasibility method. If convergence is not reached,
    the caller stops the episode explicitly. Initial safety is checked separately.
    """
    started = perf_counter(); n = len(positions)
    initial = path_clearances(positions, positions, people, room)
    if not is_safe(initial, limits):
        raise ValueError('INITIALIZATION_INVALID: current state violates safety distances')
    rows, bounds = [], []

    def add(i, normal, bound, j=None):
        row = np.zeros((n, 2))
        row[i] = normal
        if j is not None:
            row[j] = -normal
        rows.append(row.ravel())
        bounds.append(bound)

    for i in range(n):
        for j in range(i):
            # Linear separation h = ||p_i-p_j|| - d_min, not a squared barrier.
            # n = (p_i-p_j)/||p_i-p_j|| gives n·(v_i-v_j) >= (d_min+1e-8-||p_i-p_j||)/dt.
            # The row is (n, -n), so a violated pair is split equally. Neither
            # guide's velocity is held fixed.
            delta = positions[i]-positions[j]
            distance = np.linalg.norm(delta)
            if distance < 1e-12:
                raise ValueError('SAFETY_INFEASIBLE: coincident guides')
            bound = (limits.guide_distance+1e-8-distance)/dt
            if bound > -2*limits.max_speed:
                add(i, delta/distance, bound, j)
        deltas = positions[i]-people
        distances = np.linalg.norm(deltas, axis=1)
        for delta, distance in zip(deltas, distances):
            if distance < 1e-12:
                raise ValueError('SAFETY_INFEASIBLE: guide coincides with a pedestrian')
            bound = (limits.crowd_distance+1e-8-distance)/dt
            if bound > -limits.max_speed:
                add(i, delta/distance, bound)
        for axis in range(2):
            normal = np.eye(2)[axis]
            for direction, gap in [(normal, positions[i, axis]), (-normal, room[axis]-positions[i, axis])]:
                bound = (limits.wall_distance+1e-8-gap)/dt
                if bound > -limits.max_speed:
                    add(i, direction, bound)
        if navigation_obstacle is not None and not navigation_obstacle.is_empty:
            # Keep the next position on the free side of the local planning boundary.
            # This does not change guide, crowd, or wall distances.
            normal, signed = _obstacle_support(positions[i], navigation_obstacle)
            bound = -signed / dt
            if bound > -limits.max_speed:
                add(i, normal, bound)

    residual = 0.0
    for _attempt in range(4):
        matrix = np.asarray(rows, dtype=float).reshape(-1, 2 * n)
        bound_array = np.asarray(bounds, dtype=float)
        velocity, residual, iteration, converged = _project_velocity(nominal, matrix, bound_array, limits)
        if not converged:
            break
        checked = path_clearances(positions, positions + dt * velocity, people, room)
        planning_ok = navigation_obstacle is None or all(
            _planning_progress_ok(positions[i], positions[i] + dt * velocity[i], navigation_obstacle)
            for i in range(n)
        )
        if is_safe(checked, limits) and planning_ok:
            return SafetyResult(velocity, 'SOLVED', float(residual), iteration,
                float(np.linalg.norm(velocity - nominal)), float(min(v for v in checked.values() if v is not None)),
                perf_counter() - started, False)
        if navigation_obstacle is None or not is_safe(checked, limits):
            break
        for i in range(n):
            end = positions[i] + dt * velocity[i]
            if _planning_progress_ok(positions[i], end, navigation_obstacle):
                continue
            normal, _signed = _obstacle_support(end, navigation_obstacle)
            add(i, normal, 0.0)
    # A stopped single-integrator is safe whenever the current state is safe.
    stopped = np.zeros_like(nominal); checked = path_clearances(positions, positions, people, room)
    if is_safe(checked, limits):
        return SafetyResult(stopped, 'NUMERICAL_FALLBACK', float(residual),
            200, float(np.linalg.norm(nominal)), float(min(v for v in checked.values() if v is not None)),
            perf_counter()-started, True)
    raise ValueError('NUMERICAL_FAILURE: projection failed and stopped fallback is unsafe')


def binding_constraints(positions, velocity, people, room, dt, limits, navigation_obstacle=None, slack=1e-5):
    """Name inequalities that are tight for an already chosen velocity.

    This rebuilds the same half-planes the filter uses and does not project or
    replace ``velocity``. A constraint is reported when its slack is below
    ``slack``. The physical interval check stays in ``path_clearances``.
    """
    positions = np.asarray(positions, dtype=float)
    velocity = np.asarray(velocity, dtype=float)
    people = np.asarray(people, dtype=float)
    room = np.asarray(room, dtype=float)
    n = len(positions)
    named = []

    def add(kind, guide, normal, bound, other=None):
        row = np.zeros(2 * n)
        row[2 * guide:2 * guide + 2] = normal
        if other is not None:
            row[2 * other:2 * other + 2] = -normal
        named.append((kind, guide, other, row, float(bound)))

    for i in range(n):
        for j in range(i):
            delta = positions[i] - positions[j]
            distance = float(np.linalg.norm(delta))
            if distance < 1e-12:
                continue
            bound = (limits.guide_distance + 1e-8 - distance) / dt
            if bound > -2 * limits.max_speed:
                add('guide_guide', i, delta / distance, bound, j)
        deltas = positions[i] - people
        distances = np.linalg.norm(deltas, axis=1)
        for delta, distance in zip(deltas, distances):
            distance = float(distance)
            if distance < 1e-12:
                continue
            bound = (limits.crowd_distance + 1e-8 - distance) / dt
            if bound > -limits.max_speed:
                add('guide_human', i, delta / distance, bound)
        for axis in range(2):
            normal = np.eye(2)[axis]
            for direction, gap in ((normal, positions[i, axis]), (-normal, room[axis] - positions[i, axis])):
                bound = (limits.wall_distance + 1e-8 - float(gap)) / dt
                if bound > -limits.max_speed:
                    add('guide_wall', i, direction, bound)
        if navigation_obstacle is not None and not navigation_obstacle.is_empty:
            normal, signed = _obstacle_support(positions[i], navigation_obstacle)
            bound = -signed / dt
            if bound > -limits.max_speed:
                add('planning_boundary', i, normal, bound)
    flat = velocity.ravel()
    active = []
    for kind, guide, other, row, bound in named:
        gap = float(row @ flat - bound)
        if gap < slack:
            active.append({
                'kind': kind, 'guide': int(guide),
                'other_guide': None if other is None else int(other),
                'slack_m_s': gap, 'bound_m_s': bound,
            })
    return active
