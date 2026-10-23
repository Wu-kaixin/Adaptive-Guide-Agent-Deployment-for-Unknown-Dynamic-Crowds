"""Group and system acceptance from a saved trajectory. No planning and no controller."""
from __future__ import annotations

import numpy as np
from shapely.geometry import Point, Polygon

from udc.navigation import segment_in_free
from udc.safety import is_safe, path_clearances


def evaluate_execution(positions, velocities, context):
    """Judge one saved execution.

    ``positions`` has one more frame than ``velocities``. Group criteria and
    the system window both use the final ``hold_steps`` frames of this same
    trajectory. An earlier window for one group is not reused.
    """
    positions = np.asarray(positions, dtype=float)
    velocities = np.asarray(velocities, dtype=float)
    hold = int(context['hold_steps'])
    groups = {}
    for group_id, group in context['groups'].items():
        groups[group_id] = _group_report(positions, velocities, group, context, hold)
    system = _system_report(positions, velocities, context, groups, hold)
    deployment_success = bool(
        context.get('orchestration_status') == 'OK'
        and system['passed']
        and all(item['passed'] for item in groups.values())
    )
    return {
        'group_results': groups,
        'system_acceptance': system,
        'deployment_success': deployment_success,
        'terminal_window': [max(0, len(positions) - hold), len(positions)],
    }


def window_passes(positions, velocities, group, context, start, stop):
    """Whether one group meets the hold criteria on an explicit frame slice."""
    return _group_report(
        positions, velocities, group, context, stop - start, slice(start, stop),
    )['passed']


def _group_report(positions, velocities, group, context, hold, frames=None):
    rows = np.asarray(group['guide_rows'], dtype=int)
    targets = np.asarray(group['targets'], dtype=float)
    if frames is None:
        pos = positions[-hold:]
        vel = velocities[-hold:]
        enough = len(positions) >= hold + 1 and len(velocities) >= hold
    else:
        pos = positions[frames]
        vel = velocities[frames]
        enough = len(pos) >= hold and len(vel) >= hold
    reasons = []
    geometry = _covers_members(group)
    if not geometry:
        reasons.append('member_geometry')
    identity = _group_identity(group)
    if not identity:
        reasons.append('assignment_identity')
    errors = np.linalg.norm(pos[:, rows] - targets[None, :, :], axis=2) if len(pos) and len(rows) else np.zeros((0, 0))
    speeds = np.linalg.norm(vel[:, rows], axis=2) if len(vel) and len(rows) else np.zeros((0, 0))
    curve = _curve_distance(pos[:, rows], group['deployment']) if len(pos) and len(rows) else np.zeros((0, 0))
    gap = _max_gap(pos[:, rows], group['deployment']) if len(pos) and len(rows) else np.inf
    arrived = bool(enough and np.all(errors <= context['position_tolerance_m']))
    slow = bool(enough and np.all(speeds <= context['speed_tolerance_m_s']))
    on_curve = bool(enough and np.all(curve <= context['curve_tolerance_m']))
    gap_ok = bool(enough and np.all(gap <= context['max_gap_limit_m']))
    if not arrived:
        reasons.append('arrival')
    if not slow:
        reasons.append('speed')
    if not on_curve:
        reasons.append('curve')
    if not gap_ok:
        reasons.append('gap')
    passed = bool(geometry and identity and arrived and slow and on_curve and gap_ok)
    return {
        'passed': passed,
        'reasons': reasons,
        'geometry_ok': geometry,
        'identity_ok': identity,
        'arrival_ok': arrived,
        'speed_ok': slow,
        'curve_ok': on_curve,
        'gap_ok': gap_ok,
        'hold_ok': passed,
    }


def _system_report(positions, velocities, context, groups, hold):
    reasons = []
    identity = _system_identity(context)
    if not identity:
        reasons.append('identity')
    intervals = _intervals_safe(positions, velocities, context)
    if not intervals:
        reasons.append('interval_safety')
    domain = _domain_motion(positions, context)
    if not domain:
        reasons.append('shared_domain')
    speeds = np.linalg.norm(velocities, axis=2) if len(velocities) else np.zeros((0, 0))
    speed_ok = bool(len(speeds) == 0 or np.all(speeds <= context['max_speed_m_s'] + 1e-9))
    if not speed_ok:
        reasons.append('speed_limit')
    same_window = all(item['passed'] for item in groups.values()) and len(positions) >= hold + 1
    if not same_window:
        reasons.append('common_hold_window')
    passed = bool(identity and intervals and domain and speed_ok and same_window)
    return {
        'passed': passed,
        'reasons': reasons,
        'identity_ok': identity,
        'interval_safety_ok': intervals,
        'shared_domain_ok': domain,
        'speed_limit_ok': speed_ok,
        'common_hold_window_ok': same_window,
    }


def _covers_members(group):
    crowd = Polygon(np.asarray(group['crowd'], dtype=float))
    return all(
        crowd.covers(Point(point).buffer(float(radius), quad_segs=16))
        for point, radius in zip(group['member_positions'], group['member_radii'])
    )


def _group_identity(group):
    guides = list(group['guide_ids'])
    return len(guides) == len(set(guides)) == len(group['targets']) == len(group['guide_rows'])


def _system_identity(context):
    pairs = context['pairs']
    guides = [pair['guide_id'] for pair in pairs]
    if len(guides) != len(set(guides)) or set(guides) != set(context['guide_ids']):
        return False
    labels = context['target_groups']
    return all(pair['group_id'] == labels[pair['target_row']] for pair in pairs)


def _curve_distance(samples, deployment):
    line = Polygon(np.asarray(deployment, dtype=float)).exterior
    return np.array([[line.distance(Point(point)) for point in frame] for frame in samples])


def _max_gap(samples, deployment):
    line = Polygon(np.asarray(deployment, dtype=float)).exterior
    gaps = []
    for frame in samples:
        arcs = np.sort([line.project(Point(point)) for point in frame])
        gaps.append(float(np.diff(np.r_[arcs, arcs[0] + line.length]).max()))
    return np.asarray(gaps, dtype=float)


def _intervals_safe(positions, velocities, context):
    if len(velocities) == 0:
        return True
    if not np.allclose(positions[1:], positions[:-1] + context['dt'] * velocities, atol=1e-8):
        return False
    people = np.asarray(context['people'], dtype=float)
    room = np.asarray(context['room'], dtype=float)
    for start, end in zip(positions[:-1], positions[1:]):
        if not is_safe(path_clearances(start, end, people, room), context['limits']):
            return False
    return True


def _domain_motion(positions, context):
    domain = context['domain']
    for start, end in zip(positions[:-1], positions[1:]):
        for guide in range(len(start)):
            if not segment_in_free(start[guide], end[guide], domain):
                return False
    return True
