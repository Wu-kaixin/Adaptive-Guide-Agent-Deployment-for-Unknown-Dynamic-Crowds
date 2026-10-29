"""Explicit synchronous candidate for static, observed multi-robot deployment.

This is a finite straight-line candidate, not a complete multi-robot planner.
It accepts no evaluator truth and never relaxes the serial executor's distances.
Every moving pair is checked in relative coordinates over the full interval.
"""
import numpy as np
from scipy.optimize import linear_sum_assignment

from .execution import Execution
from .safe_routing import _clear_segments
from .safe_routing import find_path


def interval_audit(start, end, domain, room, config, measurement=None):
    """Audit a joint linear interval, including robots without a target."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    if (start.ndim != 2 or start.shape[1:] != (2,) or end.shape != start.shape
            or not len(start) or not np.isfinite(start).all() or not np.isfinite(end).all()):
        raise ValueError('Expected equally sized nonempty finite robot arrays')
    static_ok = all(_clear_segments(a, [b], domain.free, np.empty((0, 2)),
                                   measurement, room, config)[0] for a, b in zip(start, end))
    first, second = np.triu_indices(len(start), 1)
    relative = start[first] - start[second]
    movement = (end - start)[first] - (end - start)[second]
    denominator = np.sum(movement * movement, axis=1)
    time = np.clip(-np.sum(relative * movement, axis=1) / np.maximum(denominator, 1e-30), 0., 1.)
    distances = np.linalg.norm(relative + time[:, None] * movement, axis=1)
    minimum = float(distances.min()) if len(distances) else None
    pair_ok = minimum is None or minimum >= config['guide_distance_m']
    witness = int(np.argmin(distances)) if len(distances) else None
    return {'safe': bool(static_ok and pair_ok), 'static_segments_ok': bool(static_ok),
            'moving_pair_clearance_ok': bool(pair_ok), 'minimum_pair_distance_m': minimum,
            'closest_pair': [int(first[witness]), int(second[witness])] if witness is not None else None,
            'closest_pair_interval_fraction': float(time[witness]) if witness is not None else None}


def execute(guides, targets, domain, room, config, measurement=None, *, fixed_assignment=None):
    """Execute only an entirely verified synchronous Hungarian candidate."""
    guides, targets = np.asarray(guides, float), np.asarray(targets, float).reshape(-1, 2)
    if (guides.ndim != 2 or guides.shape[1:] != (2,) or not len(guides)
            or len(targets) > len(guides) or not np.isfinite(guides).all()
            or not np.isfinite(targets).all()):
        raise ValueError('Invalid guide or target arrays')
    assignment = np.full(len(guides), -1, int)
    terminal = guides.copy()
    if fixed_assignment is not None:
        candidate = np.asarray(fixed_assignment)
        if (candidate.shape != assignment.shape or not np.issubdtype(candidate.dtype, np.integer)
                or not np.array_equal(np.sort(candidate[candidate >= 0]), np.arange(len(targets)))
                or np.any(candidate < -1)):
            raise ValueError('Assignment must use each target exactly once')
        assignment = candidate.astype(int, copy=True)
        active = assignment >= 0
        terminal[active] = targets[assignment[active]]
    elif len(targets):
        robots, slots = linear_sum_assignment(np.linalg.norm(guides[:, None] - targets[None], axis=2))
        assignment[robots] = slots
        terminal[robots] = targets[slots]
    evidence = {'routing_policy': 'verified_synchronous_linear_v1'}

    def finish(status, positions):
        positions = np.asarray(positions)
        return Execution(positions, np.diff(positions, axis=0) / config['dt_s'], assignment,
                         status, (), evidence.copy())

    initial = interval_audit(guides, guides, domain, room, config, measurement)
    evidence['initial_audit'] = initial
    if not initial['safe']:
        return finish('INITIALIZATION_INVALID', [guides])
    whole = interval_audit(guides, terminal, domain, room, config, measurement)
    evidence['whole_interval_audit'] = whole
    if not whole['safe']:
        return finish('JOINT_CANDIDATE_REJECTED', [guides])
    distance = float(np.linalg.norm(terminal - guides, axis=1).max())
    steps = int(np.ceil(distance / (config['max_speed_m_s'] * config['dt_s'])))
    maximum = int(np.floor(config['deadline_s'] / config['dt_s']))
    if steps + config['hold_steps'] > maximum:
        evidence['required_intervals'] = steps + config['hold_steps']
        return finish('TIMEOUT', [guides])
    positions = [guides.copy()]
    for step in range(1, steps + 1):
        following = terminal.copy() if step == steps else guides + (terminal - guides) * (step / steps)
        audit = interval_audit(positions[-1], following, domain, room, config, measurement)
        if not audit['safe']:
            # Preflight the complete discretization before releasing any motion.
            evidence['rejected_discretized_interval'] = {'step': step, **audit}
            return finish('JOINT_CANDIDATE_REJECTED', [guides])
        positions.append(following)
    positions.extend(terminal.copy() for _ in range(config['hold_steps']))
    evidence.update(executed_movement_intervals=steps, idle_robots_checked=int(np.sum(assignment < 0)))
    return finish('ARRIVED', positions)


def execute_reassigned(guides, targets, domain, room, config, measurement=None):
    """Finite static-feasible assignment search with moving-pair witnesses.

    Changes identities, never targets, counts or physical distances. This first
    version explicitly requires every robot to be assigned. Partial allocation
    remains supported by the linear candidate and the separate serial policy.
    """
    first = execute(guides, targets, domain, room, config, measurement)
    if first.status != 'JOINT_CANDIDATE_REJECTED' or len(guides) != len(targets):
        return first
    guides, targets = np.asarray(guides, float), np.asarray(targets, float)
    cost = np.linalg.norm(guides[:, None] - targets[None], axis=2)
    for robot, point in enumerate(guides):
        possible = _clear_segments(point, targets, domain.free, np.empty((0, 2)), measurement, room, config)
        cost[robot, ~possible] = np.inf
    attempts, queue, seen = [], [frozenset()], set()
    limit = int(config.get('joint_assignment_candidates', 64))
    if limit < 1:
        raise ValueError('Positive finite assignment search budget required')
    while queue and len(attempts) < limit:
        banned = queue.pop(0)
        if banned in seen:
            continue
        seen.add(banned)
        matrix = cost.copy()
        for robot, target in banned:
            matrix[robot, target] = np.inf
        try:
            robots, slots = linear_sum_assignment(matrix)
        except ValueError:
            attempts.append({'banned': sorted(banned), 'result': 'no_static_feasible_matching'})
            continue
        assignment = np.full(len(guides), -1, int)
        assignment[robots] = slots
        result = execute(guides, targets, domain, room, config, measurement, fixed_assignment=assignment)
        attempts.append({'banned': sorted(banned), 'result': result.status,
                         'audit': result.evidence.get('whole_interval_audit')})
        if result.status == 'ARRIVED':
            return Execution(result.positions, result.velocities, result.assignment, result.status, result.paths,
                {**result.evidence, 'routing_policy': 'verified_synchronous_reassigned_v3', 'assignment_attempts': attempts})
        audit = result.evidence.get('whole_interval_audit', {})
        pair = audit.get('closest_pair') if not audit.get('moving_pair_clearance_ok', True) else None
        if pair:
            for robot in pair:
                queue.append(banned | {(robot, int(assignment[robot]))})
    return Execution(first.positions, first.velocities, first.assignment, 'JOINT_ASSIGNMENT_UNRESOLVED', (),
        {'routing_policy': 'verified_synchronous_reassigned_v3', 'assignment_attempts': attempts,
         'remaining_search_nodes': len(queue), 'reason': 'finite_candidate_search_exhausted'})


def execute_hybrid(guides, targets, domain, room, config, measurement=None):
    """Explicit new policy: synchronous assignment search, then reverse serial.

    Candidate rejection is a planning result; it has released zero movement.
    Safety/initialization errors never trigger a bypass. All target and physical
    contracts are unchanged and the selected policy is recorded in evidence.
    """
    result = execute_reassigned(guides, targets, domain, room, config, measurement)
    if result.status in ('JOINT_CANDIDATE_REJECTED', 'JOINT_ASSIGNMENT_UNRESOLVED'):
        from .safe_routing import execute as serial
        fallback = serial(guides, targets, domain, room, config, measurement, 'adaptive_reverse')
        return Execution(fallback.positions, fallback.velocities, fallback.assignment,
            fallback.status, fallback.paths, {**fallback.evidence,
                'execution_policy': 'joint_reassigned_then_reverse_v1',
                'joint_candidate_status': result.status, 'joint_candidate_evidence': result.evidence})
    return Execution(result.positions, result.velocities, result.assignment, result.status, result.paths,
        {**result.evidence, 'execution_policy': 'joint_reassigned_then_reverse_v1'})


def execute_geodesic(guides, targets, domain, room, config, measurement=None):
    """Try finitely many synchronized progress laws on verified static paths.

    The geometric paths ignore other robots only during candidate construction.
    The complete joint candidate, including idle robots, must pass interval_audit
    before any movement is released. No time or physical distance is relaxed.
    """
    guides, targets = np.asarray(guides, float), np.asarray(targets, float).reshape(-1, 2)
    # Reuse validation and exactly the same Hungarian target correspondence.
    linear = execute(guides, targets, domain, room, config, measurement)
    if linear.status != 'JOINT_CANDIDATE_REJECTED':
        return linear
    assignment = linear.assignment.copy()
    paths, lengths, diagnostics = [], [], []
    for robot, position in enumerate(guides):
        if assignment[robot] < 0:
            path = np.asarray([position])
        else:
            path, report = find_path(position, targets[assignment[robot]], np.asarray([position]),
                                     0, domain, room, config, measurement, adaptive=True)
            diagnostics.append({'guide': robot, **report})
            if path is None:
                return Execution(np.asarray([guides]), np.empty((0, len(guides), 2)), assignment,
                    'JOINT_STATIC_PATH_NOT_FOUND', (), {'routing_policy': 'verified_synchronous_geodesic_v2', 'queries': diagnostics})
        edge_lengths = np.linalg.norm(np.diff(path, axis=0), axis=1)
        keep = np.r_[True, edge_lengths > 0]
        path = path[keep]
        paths.append(path)
        lengths.append(np.r_[0., np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))])
    distances = np.array([length[-1] for length in lengths])
    maximum = int(np.floor(config['deadline_s'] / config['dt_s']))
    attempts = []
    # Different speeds are method candidates, not retries of an experiment.
    for exponent in (0., .5, 1.):
        # T_i proportional to length**exponent; exponent 0 synchronizes arrival.
        longest = float(distances.max())
        steps = max(1, int(np.ceil(longest / (config['max_speed_m_s'] * config['dt_s']))))
        durations = np.maximum(1, np.ceil(steps * np.maximum(distances / max(longest, 1e-30), 1e-30) ** exponent).astype(int))
        if int(durations.max()) + config['hold_steps'] > maximum:
            attempts.append({'exponent': exponent, 'reason': 'deadline'})
            continue
        positions = [guides.copy()]
        rejected = None
        for step in range(1, int(durations.max()) + 1):
            following = []
            for robot, (path, arc) in enumerate(zip(paths, lengths)):
                # A time step must split at every path corner. Otherwise the
                # chord cuts a static obstacle. Such candidates are rejected;
                # the later event-synchronized planner can refine this grid.
                distance = min(step / durations[robot], 1.) * arc[-1]
                if len(path) == 1:
                    point = path[0]
                else:
                    edge = min(int(np.searchsorted(arc, distance, side='right')) - 1, len(path) - 2)
                    fraction = (distance - arc[edge]) / (arc[edge + 1] - arc[edge])
                    point = path[edge] + fraction * (path[edge + 1] - path[edge])
                    if step >= durations[robot]:
                        point = path[-1].copy()
                following.append(point)
            following = np.asarray(following)
            report = interval_audit(positions[-1], following, domain, room, config, measurement)
            if not report['safe']:
                rejected = {'step': step, **report}
                break
            positions.append(following)
        if rejected:
            attempts.append({'exponent': exponent, 'rejected': rejected})
            continue
        terminal = positions[-1]
        positions.extend(terminal.copy() for _ in range(config['hold_steps']))
        positions = np.asarray(positions)
        return Execution(positions, np.diff(positions, axis=0) / config['dt_s'], assignment, 'ARRIVED',
            tuple({'guide': i, 'points': path.tolist()} for i, path in enumerate(paths)),
            {'routing_policy': 'verified_synchronous_geodesic_v2', 'exponent': exponent, 'attempts': attempts, 'queries': diagnostics})
    return Execution(np.asarray([guides]), np.empty((0, len(guides), 2)), assignment,
        'JOINT_CANDIDATE_REJECTED', (), {'routing_policy': 'verified_synchronous_geodesic_v2', 'attempts': attempts, 'queries': diagnostics})
