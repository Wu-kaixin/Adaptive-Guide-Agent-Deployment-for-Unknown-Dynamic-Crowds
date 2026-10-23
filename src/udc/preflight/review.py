"""Recompute preflight checks from saved files. Does not plan or run a controller."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from udc.allocation import StaticGroup, validate_member_partition
from udc.navigation import navigation_domain, segment_in_free
from udc.navigation.paths import target_domain_check
from udc.preflight.pipeline import _artifact_hashes, _atomic_json, _pairwise

REQUIRED_WHEN_ACCEPTED = (
    'request.json', 'parameters.json', 'effective_config.json', 'identity_map.json',
    'allocation.json', 'targets.npz', 'target_labels.json', 'assignment.json',
    'paths.json', 'domain.json', 'domain.wkb',
)


def reevaluate_preflight(directory, *, write=True):
    """Check a preflight in memory. Accepted runs require every listed file.

    Missing evidence stays unevaluated and cannot make ``consistent`` true.
    Thresholds come from the saved snapshot, not from the current module dict.
    """
    directory = Path(directory)
    original = (directory / 'preflight.json').read_bytes()
    record = json.loads(original)
    checks = {}
    wiring = _saved_wiring(directory, checks)
    request = _read_json(directory / 'request.json', checks, 'request')
    accepted = record.get('status') == 'PREFLIGHT_ACCEPTED'
    if accepted:
        checks['required_files'] = _required(directory, request)
        checks['artifact_hash'] = record.get('artifact_sha256') == _artifact_hashes(directory)
    else:
        checks['required_files'] = True
        checks['artifact_hash'] = True
    if request is not None and wiring is not None:
        checks['identity_map'] = _identity_ok(directory, request)
        checks['membership'] = _membership_ok(request)
        checks['finite_input'] = _finite_request(request)
    if accepted and request is not None and wiring is not None and checks.get('required_files'):
        checks['floors'] = _floors_ok(directory, request, wiring)
        checks['allocation'] = _allocation_ok(directory, request)
        checks['targets'] = _targets_ok(directory, request, wiring)
        checks['paths'] = _paths_ok(directory, request, wiring)
    checks['no_executed_motion'] = record.get('motion_executed') is False and record.get('executed_path_length_m') is None
    checks['not_deployment_success'] = record.get('deployment_success') is None
    if record.get('status') in {'BUDGET_SHORTFALL', 'FROZEN_FLOOR_VIOLATION'}:
        allocation = _read_json(directory / 'allocation.json', checks, 'allocation_file')
        checks['failure_evidence'] = bool(
            allocation and allocation.get('reason') == record['status'] and allocation.get('feasible') is False
            and allocation.get('assignment') == []
        )
    consistent = _consistent(checks, accepted)
    report = {
        'schema': 'step2a_preflight_reevaluation_v1',
        'source_status': record.get('status'),
        'checks': checks,
        'consistent': consistent,
        'completeness': checks.get('required_files') is True and checks.get('artifact_hash') is True,
        'replanned': False,
        'used_live_defaults': False,
    }
    if write:
        _atomic_json(directory / 'preflight_reevaluation.json', report)
    if (directory / 'preflight.json').read_bytes() != original:
        raise RuntimeError('reevaluation rewrote preflight.json')
    return report


def _consistent(checks, accepted):
    if any(value is None for value in checks.values()):
        return False
    if accepted:
        return all(checks.values())
    needed = ('no_executed_motion', 'not_deployment_success')
    if 'failure_evidence' in checks:
        needed = needed + ('failure_evidence', 'identity_map')
    return all(checks.get(name) is True for name in needed)


def _saved_wiring(directory, checks):
    document = _read_json(directory / 'effective_config.json', checks, 'effective_config')
    if not isinstance(document, dict) or not isinstance(document.get('wiring'), dict):
        checks['effective_config'] = None
        return None
    checks['effective_config'] = True
    return document['wiring']


def _required(directory, request):
    if request is None:
        return False
    paths = [directory / name for name in REQUIRED_WHEN_ACCEPTED]
    for group in request.get('groups', []):
        root = directory / 'groups' / group['group_id']
        paths.extend((root / 'boundary.npz', root / 'floor.json', root / 'targets.npz'))
    return all(path.is_file() for path in paths)


def _identity_ok(directory, request):
    saved = _read_json(directory / 'identity_map.json', {}, 'identity')
    if saved is None:
        return False
    expected = {str(person_id): row for row, person_id in enumerate(request['person_ids'])}
    return saved == expected


def _membership_ok(request):
    try:
        groups = tuple(StaticGroup(item['group_id'], tuple(item['member_ids'])) for item in request['groups'])
        validate_member_partition(tuple(request['person_ids']), groups)
    except Exception:
        return False
    return True


def _finite_request(request):
    arrays = [np.asarray(request['positions'], dtype=float), np.asarray(request['radii'], dtype=float),
              np.asarray(request['demand'], dtype=float), np.asarray(request['guide_positions'], dtype=float)]
    return all(np.isfinite(array).all() for array in arrays)


def _floors_ok(directory, request, wiring):
    allocation = _read_json(directory / 'allocation.json', {}, 'allocation')
    if allocation is None or not allocation.get('feasible'):
        return False
    counts = {}
    guides = []
    for guide, group in allocation['assignment']:
        guides.append(guide)
        counts[group] = counts.get(group, 0) + 1
    if len(guides) != len(set(guides)) or set(guides) != set(request['guide_ids']):
        return False
    for group in request['groups']:
        floor = _read_json(directory / 'groups' / group['group_id'] / 'floor.json', {}, 'floor')
        boundary = _load_npz(directory / 'groups' / group['group_id'] / 'boundary.npz')
        if floor is None or boundary is None:
            return False
        if floor['policy'] == 'curve_ceil':
            expected = math.ceil(float(boundary['length']) / float(wiring['planning_gap_m']))
            if int(floor['minimum_guides']) != expected:
                return False
            if abs(float(floor['curve_length_m']) - float(boundary['length'])) > 1e-9:
                return False
        if counts.get(group['group_id'], 0) < int(floor['minimum_guides']):
            return False
    return True


def _allocation_ok(directory, request):
    allocation = _read_json(directory / 'allocation.json', {}, 'allocation')
    labels = _read_json(directory / 'target_labels.json', {}, 'labels')
    payload = _read_json(directory / 'paths.json', {}, 'paths')
    if None in (allocation, labels, payload):
        return False
    pairs = payload['pairs']
    if len(pairs) != len(payload['paths']) or len(pairs) != len(allocation['assignment']):
        return False
    assigned = {}
    for guide, group in allocation['assignment']:
        if guide in assigned:
            return False
        assigned[guide] = group
    seen_targets = set()
    for pair in pairs:
        if pair['guide_id'] not in assigned or assigned[pair['guide_id']] != pair['group_id']:
            return False
        if pair['target_row'] in seen_targets or pair['target_row'] >= len(labels['group_id']):
            return False
        if labels['group_id'][pair['target_row']] != pair['group_id']:
            return False
        seen_targets.add(pair['target_row'])
    return set(assigned) == set(request['guide_ids']) and len(seen_targets) == len(labels['group_id'])


def _targets_ok(directory, request, wiring):
    data = _load_npz(directory / 'targets.npz')
    labels = _read_json(directory / 'target_labels.json', {}, 'labels')
    if data is None or labels is None or len(labels['group_id']) != len(data['positions']):
        return False
    if data['positions'].ndim != 2 or data['positions'].shape[1] != 2 or not np.isfinite(data['positions']).all():
        return False
    polygons = []
    for group in sorted(request['groups'], key=lambda item: item['group_id']):
        boundary = _load_npz(directory / 'groups' / group['group_id'] / 'boundary.npz')
        if boundary is None:
            return False
        polygons.append(Polygon(boundary['crowd']))
    domain = navigation_domain(
        request['room_m'], wiring['wall_margin_m'], unary_union(polygons), wiring['crowd_margin_m'],
        simplify_m=wiring['simplify_m'], planning_margin_m=wiring['planning_margin_m'],
    )
    admission = target_domain_check(domain, data['positions'], data['arc'])
    separation = _pairwise(data['positions'], wiring['guide_separation_m'])
    saved = _read_json(directory / 'domain.json', {}, 'domain')
    return bool(admission['accepted'] and separation['accepted'] and saved
                and saved.get('geometry_sha256') == domain.geometry_sha256
                and saved.get('joint_convex_hull') is False)


def _paths_ok(directory, request, wiring):
    payload = _read_json(directory / 'paths.json', {}, 'paths')
    labels = _read_json(directory / 'target_labels.json', {}, 'labels')
    targets = _load_npz(directory / 'targets.npz')
    if None in (payload, labels, targets) or len(payload['paths']) != len(payload['pairs']):
        return False
    polygons = []
    for group in sorted(request['groups'], key=lambda item: item['group_id']):
        boundary = _load_npz(directory / 'groups' / group['group_id'] / 'boundary.npz')
        polygons.append(Polygon(boundary['crowd']))
    domain = navigation_domain(
        request['room_m'], wiring['wall_margin_m'], unary_union(polygons), wiring['crowd_margin_m'],
        simplify_m=wiring['simplify_m'], planning_margin_m=wiring['planning_margin_m'],
    )
    guide_index = {guide_id: row for row, guide_id in enumerate(request['guide_ids'])}
    for path, pair in zip(payload['paths'], payload['pairs']):
        points = np.asarray(path, dtype=float)
        if points.ndim != 2 or len(points) < 2 or not np.isfinite(points).all():
            return False
        origin = np.asarray(request['guide_positions'][guide_index[pair['guide_id']]], dtype=float)
        target = targets['positions'][pair['target_row']]
        if np.linalg.norm(points[0] - origin) > wiring['path_start_atol_m']:
            return False
        if np.linalg.norm(points[-1] - target) > wiring['path_end_atol_m']:
            return False
        if labels['group_id'][pair['target_row']] != pair['group_id']:
            return False
        if not all(segment_in_free(points[i], points[i + 1], domain) for i in range(len(points) - 1)):
            return False
    return True


def _read_json(path, checks, name):
    path = Path(path)
    if not path.is_file():
        checks[name] = None
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def _load_npz(path):
    path = Path(path)
    if not path.is_file():
        return None
    return np.load(path, allow_pickle=False)
