"""One joint progress solve used only after the projection cuts an along-path step.

The linear rows match ``safe_velocity``. A returned velocity has already passed
the speed limit, those rows, and the executed-segment checks. Solver success
alone is not accepted, and a failed or unsafe solve installs nothing.
"""
from time import perf_counter

import numpy as np
from scipy.optimize import minimize

from udc.safety.filter import (
    SafetyResult, _obstacle_support, _planning_progress_ok, is_safe, path_clearances,
)

# Fixed before looking at task results. Not increased when a solve is short.
MAXITER = 80


def joint_progress_velocity(positions, nominal, people, room, dt, limits, obstacle,
                            priority, tangent, minimum_progress):
    """Closest joint velocity to ``nominal`` that keeps the priority progress.

    ``nominal`` is already the along-path request with other guides at zero.
    Returns None when the fixed iteration budget does not produce a velocity
    that independently satisfies the linear rows, the speed limit, and the
    executed segment.
    """
    positions = np.asarray(positions, float)
    nominal = np.asarray(nominal, float)
    people = np.asarray(people, float)
    room = np.asarray(room, float)
    tangent = np.asarray(tangent, float)
    priority = int(priority)
    matrix, bounds = _rows(positions, people, room, dt, limits, obstacle)
    x0 = nominal.ravel().copy()
    n = len(positions)
    constraints = [{
        'type': 'ineq',
        'fun': lambda vector, row=row, bound=bound: float(row @ vector - bound),
        'jac': lambda vector, row=row: row,
    } for row, bound in zip(matrix, bounds)]
    for guide in range(n):
        constraints.append({
            'type': 'ineq',
            'fun': lambda vector, guide=guide: float(
                limits.max_speed ** 2 - vector[2 * guide:2 * guide + 2] @ vector[2 * guide:2 * guide + 2]),
            'jac': lambda vector, guide=guide: _speed_jac(vector, guide),
        })
    progress_row = np.zeros(2 * n)
    progress_row[2 * priority:2 * priority + 2] = tangent
    constraints.append({
        'type': 'ineq',
        'fun': lambda vector, row=progress_row, level=float(minimum_progress): float(row @ vector - level),
        'jac': lambda vector, row=progress_row: row,
    })
    started = perf_counter()
    try:
        solved = minimize(
            lambda vector: 0.5 * float(np.sum((vector - x0) ** 2)),
            x0, jac=lambda vector: vector - x0, constraints=constraints, method='SLSQP',
            options={'maxiter': MAXITER, 'ftol': 1e-12, 'disp': False},
        )
    except (ValueError, FloatingPointError, np.linalg.LinAlgError):
        return None, perf_counter() - started
    elapsed = perf_counter() - started
    velocity = np.asarray(solved.x, float).reshape(n, 2)
    if not np.isfinite(velocity).all():
        return None, elapsed
    # The 1e-8 m pair margin can ask for a speed a few ulps above the cap.
    # Clip back onto the speed ball, then accept only if the checks still hold.
    speeds = np.linalg.norm(velocity, axis=1)
    for guide, speed in enumerate(speeds):
        if speed > limits.max_speed:
            velocity[guide] *= limits.max_speed / speed
    residual = float(np.max(bounds - matrix @ velocity.ravel(), initial=0.0)) if len(bounds) else 0.0
    progress = float(tangent @ velocity[priority])
    speeds = np.linalg.norm(velocity, axis=1)
    if (residual > 1e-6 or progress + 1e-4 < float(minimum_progress)
            or float(speeds.max(initial=0.0)) > limits.max_speed + 1e-8):
        return None, elapsed
    end = positions + float(dt) * velocity
    checked = path_clearances(positions, end, people, room)
    if not is_safe(checked, limits):
        return None, elapsed
    if obstacle is not None and not obstacle.is_empty:
        if not all(_planning_progress_ok(positions[i], end[i], obstacle) for i in range(n)):
            return None, elapsed
    clearance = float(min(value for value in checked.values() if value is not None))
    return SafetyResult(
        velocity, 'SOLVED', residual, int(solved.nit),
        float(np.linalg.norm(velocity - nominal)), clearance, elapsed, False,
    ), elapsed


def _speed_jac(vector, guide):
    jac = np.zeros_like(vector)
    jac[2 * guide:2 * guide + 2] = -2.0 * vector[2 * guide:2 * guide + 2]
    return jac


def _rows(positions, people, room, dt, limits, obstacle):
    n = len(positions)
    rows, bounds = [], []

    def add(i, normal, bound, j=None):
        row = np.zeros(2 * n)
        row[2 * i:2 * i + 2] = normal
        if j is not None:
            row[2 * j:2 * j + 2] = -normal
        rows.append(row)
        bounds.append(float(bound))

    for i in range(n):
        for j in range(i):
            delta = positions[i] - positions[j]
            distance = float(np.linalg.norm(delta))
            if distance < 1e-12:
                continue
            bound = (limits.guide_distance + 1e-8 - distance) / dt
            if bound > -2 * limits.max_speed:
                add(i, delta / distance, bound, j)
        deltas = positions[i] - people
        distances = np.linalg.norm(deltas, axis=1)
        for delta, distance in zip(deltas, distances):
            distance = float(distance)
            if distance < 1e-12:
                continue
            bound = (limits.crowd_distance + 1e-8 - distance) / dt
            if bound > -limits.max_speed:
                add(i, delta / distance, bound)
        for axis in range(2):
            normal = np.eye(2)[axis]
            for direction, gap in ((normal, positions[i, axis]), (-normal, room[axis] - positions[i, axis])):
                bound = (limits.wall_distance + 1e-8 - float(gap)) / dt
                if bound > -limits.max_speed:
                    add(i, direction, bound)
        if obstacle is not None and not obstacle.is_empty:
            normal, signed = _obstacle_support(positions[i], obstacle)
            bound = -float(signed) / dt
            if bound > -limits.max_speed:
                add(i, normal, bound)
    if not rows:
        return np.zeros((0, 2 * n)), np.zeros(0)
    return np.vstack(rows), np.asarray(bounds, float)
