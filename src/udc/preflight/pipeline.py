"""Frozen two-group input through static path preflight. No guide motion."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import traceback
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from udc.allocation import (
    AllocationInputError,
    StaticGroup,
    allocate,
    floor_from_curve,
    validate_member_partition,
)
from udc.boundary import estimate_boundary
from udc.deployment import feasibility_bounds, infeasibility_certificate, plan_coverage
from udc.navigation import navigation_domain
from udc.navigation.paths import (
    ENDPOINT_TOLERANCE_M,
    PLANNING_MARGIN_M,
    SIMPLIFY_TOLERANCE_M,
    assign_within_groups,
    target_domain_check,
)
from udc.execution.config import assert_same_config, load_wiring, working_tree_provenance
from udc.safety import Safety

SCHEMA = 'step2a_preflight_v1'
PREFLIGHT_ACCEPTED = 'PREFLIGHT_ACCEPTED'
MEMBERSHIP_CODES = frozenset({
    'INCOMPLETE_PARTITION', 'OVERLAPPING_MEMBERS', 'DUPLICATE_MEMBER',
    'DUPLICATE_POPULATION_ID', 'EMPTY_GROUP', 'DUPLICATE_GROUP',
})

# Values come from configs/step2a/static_two_groups.yaml. Do not keep a second copy.
WIRING_BASELINE = load_wiring()


class FrozenInput:
    """Handwritten crowd and guides. Arrays are copies and are not writable."""

    def __init__(self, room, person_ids, positions, radii, demand, groups,
                 guide_ids, guide_positions, *, strategy='floor_then_size',
                 floor_policy='curve_ceil', declared_floors=None, mapping=None):
        self.room = (float(room[0]), float(room[1]))
        self.person_ids = tuple(int(value) for value in person_ids)
        self.positions = _frozen_array(positions, (len(self.person_ids), 2))
        self.radii = _frozen_array(radii, (len(self.person_ids),))
        self.demand = _frozen_array(demand, (len(self.person_ids),))
        self.groups = tuple(
            StaticGroup(group.group_id, tuple(int(member) for member in group.member_ids))
            for group in groups
        )
        self.guide_ids = tuple(int(value) for value in guide_ids)
        self.guide_positions = _frozen_array(guide_positions, (len(self.guide_ids), 2))
        self.strategy = strategy
        self.floor_policy = floor_policy
        self.declared_floors = declared_floors
        self.mapping = None if mapping is None else tuple((int(guide), group) for guide, group in mapping)

    def public_record(self):
        return {
            'room_m': list(self.room),
            'person_ids': list(self.person_ids),
            'positions': self.positions.tolist(),
            'radii': self.radii.tolist(),
            'demand': self.demand.tolist(),
            'groups': [{'group_id': group.group_id, 'member_ids': list(group.member_ids)}
                       for group in self.groups],
            'guide_ids': list(self.guide_ids),
            'guide_positions': self.guide_positions.tolist(),
            'strategy': self.strategy,
            'floor_policy': self.floor_policy,
            'declared_floors': None if self.declared_floors is None else [{
                'group_id': item.group_id,
                'minimum_guides': item.minimum_guides,
                'policy': item.policy,
                'curve_length_m': item.curve_length_m,
                'planning_gap_m': item.planning_gap_m,
            } for item in self.declared_floors],
            'mapping': None if self.mapping is None else [list(pair) for pair in self.mapping],
        }


def separated_demo_input():
    """Two separated handwritten blobs and one guide per curve-ceil seat.

    Guide starts for group ``a`` lie on the far side of group ``b``.
    """
    people_a, ids_a = _blob((8.0, 18.0), 1000)
    people_b, ids_b = _blob((24.0, 18.0), 2000)
    positions = np.vstack([people_a, people_b])
    ids = ids_a + ids_b
    radii = np.full(len(ids), 0.20)
    demand = np.ones(len(ids))
    groups = (
        StaticGroup('a', tuple(ids_a)),
        StaticGroup('b', tuple(ids_b)),
    )
    bare = FrozenInput((40.0, 36.0), ids, positions, radii, demand, groups, (), np.zeros((0, 2)))
    floors = _preview_curve_floors(bare)
    guides = []
    guide_ids = []
    next_id = 10
    for group_id, floor in floors:
        count = floor.minimum_guides
        xs = 36.0 if group_id == 'a' else 3.0
        ys = np.linspace(4.0, 32.0, count)
        for y in ys:
            guide_ids.append(next_id)
            guides.append((xs, float(y)))
            next_id += 1
    return FrozenInput(
        bare.room, ids, positions, radii, demand, groups, guide_ids, guides,
        strategy='floor_then_size', floor_policy='curve_ceil',
    )


def compact_execution_input():
    """Separated blobs whose guides start a few metres from their own curves."""
    people_a, ids_a = _blob((6.0, 6.0), 3000)
    people_b, ids_b = _blob((16.0, 6.0), 4000)
    positions = np.vstack([people_a, people_b])
    ids = ids_a + ids_b
    groups = (StaticGroup('a', tuple(ids_a)), StaticGroup('b', tuple(ids_b)))
    bare = FrozenInput((28.0, 18.0), ids, positions, np.full(len(ids), 0.20), np.ones(len(ids)),
                       groups, (), np.zeros((0, 2)))
    centers = {'a': np.array([7.35, 6.90]), 'b': np.array([17.35, 6.90])}
    guide_ids = []
    guides = []
    next_id = 50
    for group_id, floor in _preview_curve_floors(bare):
        angles = np.linspace(0.0, 2.0 * np.pi, floor.minimum_guides, endpoint=False)
        for angle in angles:
            point = centers[group_id] + 4.5 * np.array([np.cos(angle), np.sin(angle)])
            guide_ids.append(next_id)
            guides.append((float(point[0]), float(point[1])))
            next_id += 3
    return FrozenInput(
        bare.room, ids, positions, bare.radii, bare.demand, groups, guide_ids, guides,
        strategy='floor_then_size', floor_policy='curve_ceil',
    )


def run_preflight(request, output):
    """Register one preflight directory and keep every artifact already written."""
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'Use an empty output directory: {output}')
    output.mkdir(parents=True, exist_ok=True)
    record = _blank_record()
    try:
        assert_same_config(WIRING_BASELINE, load_wiring(), 'wiring baseline')
        _atomic_json(output / 'effective_config.json', {
            'schema': 'step2a_effective_config_v1',
            'role': 'preflight',
            'wiring': WIRING_BASELINE,
            'provenance': working_tree_provenance(),
        })
        _atomic_json(output / 'request.json', request.public_record())
        _atomic_json(output / 'parameters.json', dict(WIRING_BASELINE))
        _execute(request, output, record)
    except Exception:
        record['status'] = 'EXPERIMENT_ERROR'
        record['preflight_accepted'] = False
        record['error'] = traceback.format_exc().strip().splitlines()[-1]
        _atomic_text(output / 'preflight_error.txt', traceback.format_exc())
    record['input_sha256'] = _sha256_file(output / 'request.json')
    record['artifact_sha256'] = _artifact_hashes(output)
    _atomic_json(output / 'preflight.json', record)
    return record


def _execute(request, output, record):
    _check_wiring()
    if len(request.person_ids) != len(set(request.person_ids)) or len(request.guide_ids) != len(set(request.guide_ids)):
        record['status'] = 'INPUT_REJECTED'
        record['failure_stage'] = 'identity'
        record['failure_code'] = 'DUPLICATE_ID'
        return
    if not _arrays_ok(request):
        record['status'] = 'INPUT_REJECTED'
        record['failure_stage'] = 'identity'
        record['failure_code'] = 'NONFINITE_INPUT'
        return
    try:
        index = _person_index(request.person_ids)
        _atomic_json(output / 'identity_map.json', {str(key): value for key, value in index.items()})
        validate_member_partition(request.person_ids, request.groups)
    except AllocationInputError as exc:
        record['status'] = 'MEMBERSHIP_INVALID' if exc.code in MEMBERSHIP_CODES else 'INPUT_REJECTED'
        record['failure_stage'] = 'membership'
        record['failure_code'] = exc.code
        record['failure_reason'] = str(exc)
        return
    record['stages']['membership'] = 'accepted'
    try:
        boundaries = _boundaries(request, index, output, record)
    except _StageStop:
        return
    record['stages']['geometry'] = 'accepted'
    try:
        floors = _floors(request, boundaries, output, record)
    except _StageStop:
        return
    record['stages']['floors'] = 'accepted'
    try:
        allocation = allocate(
            request.groups, floors, request.guide_ids,
            strategy=request.strategy, mapping=request.mapping,
        )
    except AllocationInputError as exc:
        record['status'] = 'INPUT_REJECTED'
        record['failure_stage'] = 'allocation'
        record['failure_code'] = exc.code
        record['failure_reason'] = str(exc)
        return
    record['allocation_feasible'] = bool(allocation.feasible)
    _atomic_json(output / 'allocation.json', {
        'strategy': allocation.strategy,
        'floor_policy': allocation.floor_policy,
        'feasible': allocation.feasible,
        'reason': allocation.reason,
        'assignment': [list(pair) for pair in allocation.assignment],
    })
    if not allocation.feasible:
        record['status'] = allocation.reason
        record['failure_stage'] = 'allocation'
        record['preflight_accepted'] = False
        return
    record['stages']['allocation'] = 'accepted'
    try:
        plans = _plans(request, index, boundaries, allocation, output, record)
    except _StageStop:
        return
    record['stages']['planning'] = 'accepted'
    _global_and_routes(request, index, boundaries, allocation, plans, output, record)


def _boundaries(request, index, output, record):
    parameters = WIRING_BASELINE
    found = {}
    for group in sorted(request.groups, key=lambda item: item.group_id):
        rows = [index[member] for member in group.member_ids]
        try:
            boundary = estimate_boundary(
                request.positions[rows], parameters['offset_m'], parameters['sample_spacing_m'],
                request.room, parameters['wall_margin_m'], parameters['crowd_distance_m'],
                request.radii[rows],
            )
        except ValueError as exc:
            code = str(exc).split(':', 1)[0]
            record['status'] = 'GEOMETRY_REJECTED'
            record['failure_stage'] = 'geometry'
            record['failure_code'] = code
            record['failure_group'] = group.group_id
            record['failure_reason'] = str(exc)
            raise _StageStop from exc
        directory = output / 'groups' / group.group_id
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_npz(
            directory / 'boundary.npz',
            crowd=boundary.crowd, deployment=boundary.deployment,
            arc=boundary.arc, length=np.array(boundary.length),
            rows=np.asarray(rows, dtype=int),
            member_ids=np.asarray(group.member_ids, dtype=int),
        )
        found[group.group_id] = boundary
    return found


def _floors(request, boundaries, output, record):
    floors = []
    for group in sorted(request.groups, key=lambda item: item.group_id):
        boundary = boundaries[group.group_id]
        if request.floor_policy == 'curve_ceil':
            floor = floor_from_curve(group.group_id, boundary.length, WIRING_BASELINE['planning_gap_m'])
        elif request.floor_policy == 'declared':
            supplied = {item.group_id: item for item in request.declared_floors or ()}
            if group.group_id not in supplied:
                record['status'] = 'INPUT_REJECTED'
                record['failure_stage'] = 'floors'
                record['failure_code'] = 'MISSING_FLOOR'
                raise _StageStop
            floor = supplied[group.group_id]
        else:
            record['status'] = 'INPUT_REJECTED'
            record['failure_stage'] = 'floors'
            record['failure_code'] = 'UNKNOWN_FLOOR_POLICY'
            raise _StageStop
        payload = {
            'group_id': floor.group_id,
            'minimum_guides': floor.minimum_guides,
            'policy': floor.policy,
            'curve_length_m': None if floor.curve_length_m is None else floor.curve_length_m,
            'planning_gap_m': None if floor.planning_gap_m is None else floor.planning_gap_m,
            'saved_curve_length_m': boundary.length,
        }
        _atomic_json(output / 'groups' / group.group_id / 'floor.json', payload)
        floors.append(floor)
    record['floor_policy'] = floors[0].policy
    return tuple(floors)


def _plans(request, index, boundaries, allocation, output, record):
    parameters = WIRING_BASELINE
    plans = {}
    for group in sorted(request.groups, key=lambda item: item.group_id):
        rows = [index[member] for member in group.member_ids]
        assigned = [guide for guide, name in allocation.assignment if name == group.group_id]
        count = len(assigned)
        boundary = boundaries[group.group_id]
        bounds = feasibility_bounds(
            boundary.length, parameters['planning_gap_m'], parameters['guide_separation_m'],
        )
        certificate = infeasibility_certificate(bounds, count)
        try:
            plan = plan_coverage(
                boundary, request.positions[rows], request.demand[rows], count,
                parameters['demand_bandwidth_m'], parameters['deployment_method'],
                parameters['phase'], parameters['quadrature_samples'],
                parameters['planner_max_iterations'],
            )
        except ValueError as exc:
            record['status'] = 'PLAN_REJECTED'
            record['failure_stage'] = 'planning'
            record['failure_group'] = group.group_id
            record['failure_reason'] = str(exc)
            record['infeasibility_certificate'] = certificate
            raise _StageStop from exc
        distances = np.linalg.norm(plan.positions[:, None] - plan.positions[None, :], axis=2)
        np.fill_diagonal(distances, np.inf)
        separation = float(distances.min()) if len(plan.positions) else 0.0
        accepted = (plan.max_gap <= parameters['planning_gap_m'] + 1e-9
                    and separation >= parameters['guide_separation_m']
                    and certificate is None)
        _atomic_npz(
            output / 'groups' / group.group_id / 'targets.npz',
            positions=plan.positions, arc=plan.arc_positions,
            local_target_id=np.arange(len(plan.positions)),
            member_rows=np.asarray(rows, dtype=int),
        )
        if not accepted:
            record['status'] = 'PLAN_REJECTED'
            record['failure_stage'] = 'planning'
            record['failure_group'] = group.group_id
            record['failure_reason'] = 'equal-arc targets miss the arc, separation, or count condition'
            record['infeasibility_certificate'] = certificate
            record['planned_max_gap_m'] = plan.max_gap
            record['planned_min_separation_m'] = separation
            raise _StageStop
        plans[group.group_id] = plan
    return plans


def _global_and_routes(request, index, boundaries, allocation, plans, output, record):
    parameters = WIRING_BASELINE
    order = [group.group_id for group in sorted(request.groups, key=lambda item: item.group_id)]
    coordinates = []
    arcs = []
    labels = []
    local_ids = []
    for group_id in order:
        plan = plans[group_id]
        for local, (point, arc) in enumerate(zip(plan.positions, plan.arc_positions)):
            coordinates.append(point)
            arcs.append(arc)
            labels.append(group_id)
            local_ids.append(local)
    coordinates = np.asarray(coordinates, dtype=float)
    _atomic_npz(
        output / 'targets.npz',
        positions=coordinates, arc=np.asarray(arcs, dtype=float),
        local_target_id=np.asarray(local_ids, dtype=int),
    )
    _atomic_json(output / 'target_labels.json', {
        'order': 'groups by group_id, then local_target_id',
        'group_id': labels,
        'local_target_id': local_ids,
    })
    polygons = [Polygon(boundaries[group_id].crowd) for group_id in order]
    merged = unary_union(polygons)
    domain = navigation_domain(
        request.room, parameters['wall_margin_m'], merged, parameters['crowd_margin_m'],
        simplify_m=parameters['simplify_m'], planning_margin_m=parameters['planning_margin_m'],
    )
    (output / 'domain.wkb').write_bytes(merged.wkb)
    _atomic_json(output / 'domain.json', {
        'merge': 'unary_union of per-group occupied hulls',
        'joint_convex_hull': False,
        'occupied_type': merged.geom_type,
        'component_count': 1 if merged.geom_type == 'Polygon' else len(getattr(merged, 'geoms', ())),
        'simplify_m': SIMPLIFY_TOLERANCE_M,
        'planning_margin_m': PLANNING_MARGIN_M,
        'endpoint_tolerance_m': ENDPOINT_TOLERANCE_M,
        'geometry_sha256': domain.geometry_sha256,
        'occupied_wkb_sha256': hashlib.sha256(merged.wkb).hexdigest(),
    })
    admission = target_domain_check(domain, coordinates, np.asarray(arcs, dtype=float))
    _atomic_json(output / 'target_domain.json', _jsonable(admission))
    if not admission['accepted']:
        record['status'] = 'TARGET_DOMAIN_INVALID'
        record['failure_stage'] = 'global_targets'
        record['preflight_accepted'] = False
        return
    separation = _pairwise(coordinates, parameters['guide_separation_m'])
    _atomic_json(output / 'target_separation.json', separation)
    if not separation['accepted']:
        record['status'] = 'TARGET_SEPARATION_INVALID'
        record['failure_stage'] = 'global_targets'
        record['preflight_accepted'] = False
        return
    guide_order = sorted(allocation.assignment)
    guide_index = {guide_id: row for row, guide_id in enumerate(request.guide_ids)}
    guide_rows = [guide_index[guide_id] for guide_id, _group in guide_order]
    guide_positions = request.guide_positions[guide_rows]
    guide_groups = [group for _guide, group in guide_order]
    limits = _limits(request)
    routed = assign_within_groups(
        guide_positions, guide_groups, coordinates, labels, domain, request.room,
        request.positions, limits,
    )
    pairs = []
    paths = []
    for group in routed['groups']:
        for pair in group['pairs']:
            guide_id = guide_order[pair['guide_row']][0]
            pairs.append({
                'guide_id': guide_id,
                'guide_row': int(guide_rows[pair['guide_row']]),
                'group_id': pair['group_id'],
                'local_target_id': int(local_ids[pair['target_row']]),
                'target_row': int(pair['target_row']),
                'length_m': pair['length_m'],
            })
            paths.append(pair['path'].tolist())
    _atomic_json(output / 'assignment.json', {
        'index_note': 'guide_row is the frozen guide array row; target_row is the concatenated target row',
        'matched': all(group['matched'] for group in routed['groups']),
        'groups': [{key: value for key, value in group.items() if key != 'pairs'} | {
            'pair_count': len(group['pairs']),
        } for group in routed['groups']],
        'pairs': pairs,
    })
    _atomic_json(output / 'paths.json', {'paths': paths, 'pairs': pairs})
    if not all(group['matched'] for group in routed['groups']):
        record['status'] = 'PATH_UNREACHABLE'
        record['failure_stage'] = 'routing'
        record['preflight_accepted'] = False
        return
    record['status'] = PREFLIGHT_ACCEPTED
    record['failure_stage'] = None
    record['preflight_accepted'] = True
    record['stages']['routing'] = 'accepted'


def _blank_record():
    return {
        'schema': SCHEMA,
        'status': None,
        'preflight_accepted': False,
        'allocation_feasible': None,
        'deployment_success': None,
        'motion_executed': False,
        'arrival_evaluated': None,
        'hold_evaluated': None,
        'dynamic_safety_evaluated': None,
        'executed_path_length_m': None,
        'stages': {},
        'wiring_baseline': WIRING_BASELINE['deployment_method'],
        'boundary_policy': WIRING_BASELINE['boundary_policy'],
    }


def _check_wiring():
    gap = WIRING_BASELINE['max_gap_limit_m'] - 2 * WIRING_BASELINE['arc_tolerance_m']
    if abs(gap - WIRING_BASELINE['planning_gap_m']) > 1e-12:
        raise RuntimeError('wiring baseline planning gap does not match max_gap_limit - 2*arc_tolerance')


def _arrays_ok(request):
    if len(request.person_ids) != len(set(request.person_ids)):
        return False
    if len(request.guide_ids) != len(set(request.guide_ids)):
        return False
    arrays = (request.positions, request.radii, request.demand, request.guide_positions)
    return all(np.isfinite(array).all() for array in arrays) and np.all(request.radii >= 0) and np.all(request.demand > 0)


def _person_index(ids):
    if len(ids) != len(set(ids)):
        raise AllocationInputError('DUPLICATE_POPULATION_ID', 'frozen person IDs must be unique')
    return {person_id: row for row, person_id in enumerate(ids)}


def _preview_curve_floors(request):
    index = _person_index(request.person_ids)
    floors = []
    for group in sorted(request.groups, key=lambda item: item.group_id):
        rows = [index[member] for member in group.member_ids]
        boundary = estimate_boundary(
            request.positions[rows], WIRING_BASELINE['offset_m'], WIRING_BASELINE['sample_spacing_m'],
            request.room, WIRING_BASELINE['wall_margin_m'], WIRING_BASELINE['crowd_distance_m'],
            request.radii[rows],
        )
        floors.append((group.group_id, floor_from_curve(
            group.group_id, boundary.length, WIRING_BASELINE['planning_gap_m'],
        )))
    return floors


def _limits(request):
    parameters = WIRING_BASELINE
    return Safety(
        parameters['guide_separation_m'],
        parameters['guide_radius_m'] + float(request.radii.max()) + parameters['clearance_m'],
        parameters['wall_margin_m'],
        1.0,
        parameters['numeric_tolerance_m'],
    )


def _pairwise(points, minimum):
    if len(points) < 2:
        return {'accepted': False, 'minimum_m': None, 'violations': []}
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    violations = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            if distances[i, j] < minimum:
                violations.append({'i': i, 'j': j, 'distance_m': float(distances[i, j])})
    return {
        'accepted': not violations,
        'minimum_m': float(distances.min()),
        'required_m': minimum,
        'violations': violations,
    }


def _blob(origin, first_id):
    points = []
    ids = []
    number = first_id
    for row in range(3):
        for column in range(4):
            points.append((origin[0] + 0.9 * column, origin[1] + 0.9 * row))
            ids.append(number)
            number += 2
    return np.asarray(points, dtype=float), ids


def _frozen_array(values, shape):
    array = np.array(values, dtype=float, copy=True)
    if array.shape != shape:
        raise ValueError(f'frozen array shape {array.shape} != {shape}')
    array.setflags(write=False)
    return array


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _atomic_json(path, payload):
    _atomic_text(path, json.dumps(_jsonable(payload), indent=2, allow_nan=False) + '\n')


def _atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', dir=path.parent, delete=False) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _atomic_npz(path, **arrays):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False) as stream:
        np.savez_compressed(stream, **arrays)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _artifact_hashes(output):
    hashes = {}
    for path in sorted(Path(output).rglob('*')):
        if not path.is_file() or path.name in {'preflight.json', 'preflight_reevaluation.json', 'preflight_error.txt'}:
            continue
        hashes[path.relative_to(output).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


class _StageStop(Exception):
    """A scientific preflight stage has already filled the record."""
