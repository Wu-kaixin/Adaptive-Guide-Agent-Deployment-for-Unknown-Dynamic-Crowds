"""Planning diagnostic for prescribed high-curvature and concave curves.

The 300 tasks do not estimate a boundary and do not invent people. Execution is
a later, separate step and only runs when a frozen attachment can place people
inside a curve that already has a feasible target set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import numpy as np
import yaml
from shapely.geometry import Point, Polygon

from udc.boundary import Boundary, occupied_hull
from udc.boundary.prescribed import (
    FROZEN_GEOMETRY, build_prescribed_curve, nominal_ring,
)
from udc.deployment.geometry import (
    classify_geometry, count_rule, high_resolution_check, pair_geometry,
    projection_trace, termination_for, tier_count, unevaluated_execution, uniform_cost,
)
from udc.deployment.planners import BASELINE_METHODS, _interpolate, periodic_gaps, plan_coverage
from udc.deployment.risk_aware import plan_projected_mass
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, reevaluate_result

FAMILIES = ('high_curvature_convex', 'shallow_concave', 'deep_notch')
INTENSITIES = {
    'high_curvature_convex': ('mild', 'strong'),
    'shallow_concave': ('mild', 'strong'),
    'deep_notch': ('wide', 'narrow'),
}
SECOND_INTENSITY = {family: names[1] for family, names in INTENSITIES.items()}
TIERS = ('near', 'margin')
CORE_METHODS = ('equal_arc', 'mass_cvt', 'projected_mass')
ALL_METHODS = CORE_METHODS + ('average_cvt', 'distmesh')


def load_spec(path):
    path = Path(path)
    spec = yaml.safe_load(path.read_text(encoding='utf-8'))
    if spec.get('protocol') != 'prescribed_curve_diagnostic':
        raise ValueError('This runner only accepts the prescribed-curve diagnostic spec')
    if list(spec['development_seeds']) != list(FROZEN_GEOMETRY['seeds']):
        raise ValueError('Diagnostic seeds must be the frozen origin list 15-19')
    if list(spec['methods']) != list(ALL_METHODS):
        raise ValueError(f'Diagnostic methods must be {list(ALL_METHODS)}')
    geometry = spec['geometry']
    for key in ('target_length_m', 'planner_spacing_m', 'fine_spacing_m',
                'gap_limit_m', 'center_separation_m', 'seed_origin_step'):
        if float(geometry[key]) != float(FROZEN_GEOMETRY[key]):
            raise ValueError(f'Spec geometry.{key} does not match the frozen catalog')
    gap = float(spec['planner']['max_gap_limit']) - 2 * float(spec['planner']['arc_tolerance'])
    if abs(gap - float(geometry['gap_limit_m'])) > 1e-12:
        raise ValueError('gap_limit_m must equal max_gap_limit - 2*arc_tolerance')
    for family, intensities in spec['geometry']['families'].items():
        for intensity, payload in intensities.items():
            frozen = FROZEN_GEOMETRY['curves'][(family, intensity)]
            if set(payload) != set(frozen):
                raise ValueError(f'Curve parameters for {family}/{intensity} do not match the frozen catalog')
            for key, value in frozen.items():
                if float(payload[key]) != float(value):
                    raise ValueError(f'{family}/{intensity}.{key} does not match the frozen catalog')
    spec['_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    spec['_path'] = str(path)
    return spec


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Diagnostic records cannot store NaN or infinity')
    return value


def _write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(_jsonable(payload), indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _curve_record(built):
    report = built['report']
    nominal = built['nominal_report']
    return {
        'family': built['family'],
        'intensity': built['intensity'],
        'seed': built['seed'],
        'boundary_source': built['boundary_source'],
        'crowd_field': built['crowd_field'],
        'origin': built['origin'],
        'construction': built['construction'],
        'validity': report,
        'nominal_validity': {
            key: nominal[key] for key in (
                'valid', 'invalid_reasons', 'length_m', 'convexity_defect',
                'max_curvature_per_m', 'min_curvature_per_m', 'concave_sample_fraction',
                'area_m2', 'orientation', 'simple',
            )
        },
        'measured_slot_width_m': report.get('measured_slot_width_m'),
        'points_sha256': hashlib.sha256(np.ascontiguousarray(built['points']).tobytes()).hexdigest(),
    }


def build_catalog(spec):
    """Construct every curve before any planner runs. Invalid curves stay listed."""
    catalog = []
    for family in FAMILIES:
        for intensity in INTENSITIES[family]:
            for seed in spec['development_seeds']:
                built = build_prescribed_curve(
                    family, intensity, seed, float(spec['geometry']['target_length_m']),
                    float(spec['geometry']['planner_spacing_m']),
                )
                catalog.append(built)
    return catalog


def build_tasks(spec, catalog, output):
    tasks = []
    gap = float(spec['geometry']['gap_limit_m'])
    separation = float(spec['geometry']['center_separation_m'])
    for built in catalog:
        length = float(built['boundary'].length)
        rule = count_rule(length, gap, separation)
        for tier in TIERS:
            count, certificate = tier_count(rule, tier)
            for method in spec['methods']:
                name = f'{built["family"]}__{built["intensity"]}__{tier}'
                directory = Path(output) / 'planning' / name / method / f'seed_{built["seed"]}'
                identity = '|'.join([
                    spec['_sha256'], built['points_sha256'] if 'points_sha256' in built else '',
                    hashlib.sha256(np.ascontiguousarray(built['points']).tobytes()).hexdigest(),
                    method, tier, str(count), str(built['seed']),
                    json.dumps(spec['planner'], sort_keys=True),
                ])
                tasks.append({
                    'family': built['family'],
                    'intensity': built['intensity'],
                    'tier': tier,
                    'seed': int(built['seed']),
                    'method': method,
                    'count': int(count),
                    'certificate': certificate,
                    'curve_valid': bool(built['report']['valid']),
                    'length_m': length,
                    'points': np.asarray(built['points'], dtype=float),
                    'count_rule': rule,
                    'output': str(directory),
                    'task_sha256': hashlib.sha256(identity.encode()).hexdigest(),
                    'points_sha256': hashlib.sha256(
                        np.ascontiguousarray(built['points']).tobytes()
                    ).hexdigest(),
                    'planner': spec['planner'],
                })
    return tasks


def _plan_attempt(boundary, method, count, phase, planner, gap, separation):
    density = np.ones(len(boundary.deployment))
    samples = int(planner['quadrature_samples'])
    iterations = int(planner['planner_max_iterations'])
    if method == 'projected_mass':
        return plan_projected_mass(
            boundary, None, None, count, None, gap, separation, phase, samples, iterations,
            density_override=density,
        )
    if method not in BASELINE_METHODS:
        raise ValueError(f'Unknown diagnostic method: {method}')
    return plan_coverage(
        boundary, None, None, count, None, method, phase, samples, iterations,
        density_override=density,
    )


def _search(task):
    from time import perf_counter
    boundary = Boundary(task['points'], task['points'], *_arc(task['points']))
    gap = float(task['planner']['max_gap_limit']) - 2 * float(task['planner']['arc_tolerance'])
    separation = 2 * 0.20 + 0.35
    attempts = []
    chosen = None
    stopped = None
    for phase in task['planner']['phases']:
        started = perf_counter()
        try:
            plan = _plan_attempt(boundary, task['method'], task['count'], phase,
                                 task['planner'], gap, separation)
        except ValueError as error:
            attempts.append({
                'phase': float(phase),
                'planner_exception': str(error),
                'accepted_by_controller_rule': False,
                'arc_limit_ok': False,
                'planner_seconds': perf_counter() - started,
            })
            if str(error).split(':', 1)[0] == 'PLAN_INVALID':
                stopped = 'PLAN_INVALID'
                break
            continue
        geometry = pair_geometry(boundary, plan.arc_positions, gap, separation)
        geometry['phase'] = float(phase)
        geometry['iterations'] = int(plan.iterations)
        geometry['exit_reason'] = plan.exit_reason
        geometry['planner_seconds'] = perf_counter() - started
        geometry['planner_cost_history'] = np.round(plan.cost, 8).tolist()
        geometry['uniform_cost'] = uniform_cost(
            boundary, plan.arc_positions, int(task['planner']['quadrature_samples']),
        )
        geometry['projection'] = projection_trace(boundary, plan)
        geometry['density'] = 'explicit constant curve density'
        attempts.append(geometry)
        if geometry['accepted_by_controller_rule']:
            chosen = plan
            break
    return boundary, attempts, chosen, stopped


def _arc(points):
    segment = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    return np.r_[0.0, np.cumsum(segment[:-1])], float(segment.sum())


def run_task(task):
    from threadpoolctl import threadpool_limits
    directory = Path(task['output'])
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / 'state.json'
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if state.get('task_sha256') == task['task_sha256'] and state.get('status') == 'completed':
            return json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    with threadpool_limits(limits=1):
        if not task['curve_valid']:
            attempts, chosen, stopped = [], None, None
            boundary = None
        else:
            boundary, attempts, chosen, stopped = _search(task)
    geometry_class = classify_geometry(task['curve_valid'], task['certificate'], attempts)
    if stopped == 'PLAN_INVALID':
        geometry_class = 'planner_exception'
    termination, stage = termination_for(geometry_class)
    focus = next((item for item in attempts if item.get('accepted_by_controller_rule')), None)
    if focus is None and attempts:
        focus = attempts[-1]
    fine = None
    if boundary is not None and focus and focus.get('arc_positions'):
        fine = high_resolution_check(
            boundary, focus['arc_positions'],
            float(task['planner']['max_gap_limit']) - 2 * float(task['planner']['arc_tolerance']),
            2 * 0.20 + 0.35,
        )
    public_attempts = []
    for item in attempts:
        copied = dict(item)
        copied.pop('pairwise', None)
        public_attempts.append(copied)
    record = {
        'task_sha256': task['task_sha256'],
        'points_sha256': task['points_sha256'],
        'family': task['family'],
        'intensity': task['intensity'],
        'tier': task['tier'],
        'seed': task['seed'],
        'method': task['method'],
        'count': task['count'],
        'length_m': task['length_m'],
        'count_rule': {
            'near': task['count_rule']['near'],
            'margin': task['count_rule']['margin'],
            'separation_upper_bound': task['count_rule']['separation_upper_bound'],
            'chord_gap_m': task['count_rule']['chord_gap_m'],
            'margin_exceeds_separation_upper': task['count_rule']['margin_exceeds_separation_upper'],
            'assumptions': task['count_rule']['assumptions'],
            'certificate': task['certificate'],
        },
        'boundary_source': 'prescribed_diagnostic',
        'curve_valid': task['curve_valid'],
        'geometry_class': geometry_class,
        'termination_status': termination,
        'stage_diagnosis': stage,
        'deployment_success': False,
        'evaluation': unevaluated_execution(),
        'focus': None if focus is None else {
            key: focus.get(key) for key in (
                'phase', 'iterations', 'exit_reason', 'planner_seconds',
                'min_arc_gap_m', 'max_arc_gap_m', 'seam_arc_gap_m',
                'min_adjacent_euclidean_m', 'min_nonadjacent_euclidean_m',
                'min_euclidean_m', 'max_curve_deviation_m',
                'arc_limit_ok', 'arc_box_ok', 'euclidean_ok',
                'accepted_by_controller_rule', 'worst_pair', 'uniform_cost',
                'adjacent_euclidean_pair', 'nonadjacent_euclidean_pair',
            )
        },
        'high_resolution': fine,
        'attempt_count': len(attempts),
    }
    _write(directory / 'geometry.json', {
        **record,
        'attempts': attempts,
        # Full float64. Rounding Cartesian coordinates to 1e-6 m moves targets off
        # the polyline by enough to fail the 1e-7 m curve check.
        'positions': None if chosen is None else np.asarray(chosen.positions, dtype=float).tolist(),
        'arc_positions': None if chosen is None else np.asarray(chosen.arc_positions, dtype=float).tolist(),
    })
    _write(directory / 'summary.json', record)
    _write(state_path, {'status': 'completed', 'task_sha256': task['task_sha256'],
                        'termination_status': termination})
    return record


def run_tasks(tasks, workers):
    if workers < 1:
        raise ValueError('workers must be positive')
    if workers == 1 or len(tasks) == 1:
        return [run_task(task) for task in tasks]
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_task, task) for task in tasks]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (row['family'], row['intensity'], row['tier'], row['seed'], row['method']))
    return rows


def _unit_key(row):
    return (row['family'], row['intensity'], row['tier'], row['seed'])


def summarize(rows):
    groups = {}
    for row in rows:
        key = (row['family'], row['intensity'], row['tier'], row['method'])
        groups.setdefault(key, []).append(row)
    cells = []
    for key, items in groups.items():
        classes = {}
        for item in items:
            classes[item['geometry_class']] = classes.get(item['geometry_class'], 0) + 1
        feasible = [item for item in items if item['geometry_class'] == 'target_geometry_feasible']
        conflicts = [item for item in items if item['geometry_class'] == 'arc_pass_euclidean_fail']
        margins = []
        adjacent = []
        nonadjacent = []
        seconds = []
        costs = []
        for item in items:
            focus = item.get('focus') or {}
            if focus.get('min_euclidean_m') is not None:
                margins.append(focus['min_euclidean_m'] - 0.75)
            if focus.get('min_adjacent_euclidean_m') is not None:
                adjacent.append(focus['min_adjacent_euclidean_m'])
            if focus.get('min_nonadjacent_euclidean_m') is not None:
                nonadjacent.append(focus['min_nonadjacent_euclidean_m'])
            if focus.get('planner_seconds') is not None:
                seconds.append(focus['planner_seconds'])
            cost = (focus.get('uniform_cost') or {}).get('uniform_weighted_cost_m2')
            if cost is not None:
                costs.append(cost)
        worst_conflict = None
        for item in conflicts:
            pair = (item.get('focus') or {}).get('worst_pair')
            if pair is None:
                continue
            if worst_conflict is None or pair['euclidean_m'] < worst_conflict['euclidean_m']:
                worst_conflict = {**pair, 'seed': item['seed']}
        cells.append({
            'family': key[0], 'intensity': key[1], 'tier': key[2], 'method': key[3],
            'tasks': len(items),
            'curve_valid': sum(bool(item['curve_valid']) for item in items),
            'target_geometry_feasible': len(feasible),
            'classes': classes,
            'adjacent_conflict_tasks': sum(
                1 for item in items
                if (item.get('focus') or {}).get('worst_pair')
                and (item.get('focus') or {})['worst_pair'].get('adjacent')
                and item['geometry_class'] == 'arc_pass_euclidean_fail'
            ),
            'nonadjacent_conflict_tasks': sum(
                1 for item in items
                if (item.get('focus') or {}).get('worst_pair')
                and not (item.get('focus') or {})['worst_pair'].get('adjacent')
                and item['geometry_class'] == 'arc_pass_euclidean_fail'
            ),
            'min_euclidean_margin_m': None if not margins else min(margins),
            'median_min_adjacent_m': None if not adjacent else float(np.median(adjacent)),
            'median_min_nonadjacent_m': None if not nonadjacent else float(np.median(nonadjacent)),
            'median_uniform_cost_m2': None if not costs else float(np.median(costs)),
            'median_planner_seconds': None if not seconds else float(np.median(seconds)),
            'worst_conflict': worst_conflict,
        })
    valid_curves = len({(row['family'], row['intensity'], row['seed']) for row in rows if row['curve_valid']})
    curve_slots = len({(row['family'], row['intensity'], row['seed']) for row in rows})
    return {
        'tasks': len(rows),
        'curve_slots': curve_slots,
        'valid_curve_slots': valid_curves,
        'cells': cells,
        'deployment_successes': sum(bool(row['deployment_success']) for row in rows),
        'unevaluated_execution': sum(not row['evaluation']['execution_evaluated'] for row in rows),
    }


def select_units(rows):
    """Apply the pre-registered display rules. Do not drop a class to improve a rate."""
    by_unit = {}
    for row in rows:
        by_unit.setdefault(_unit_key(row), []).append(row)
    def equal_arc(unit):
        items = [row for row in by_unit[unit] if row['method'] == 'equal_arc']
        if len(items) != 1:
            raise ValueError(f'Missing Equal Arc row for {unit}')
        return items[0]
    def feasible(unit):
        row = equal_arc(unit)
        return row['curve_valid'] and row['geometry_class'] == 'target_geometry_feasible'
    selected = []
    def add(unit, reason, execute):
        if unit in [item['unit'] for item in selected]:
            return
        if len(selected) >= 6:
            return
        selected.append({'unit': unit, 'reason': reason, 'execute': bool(execute and feasible(unit))})
    for family in FAMILIES:
        intensity = SECOND_INTENSITY[family]
        seeds = sorted({unit[3] for unit in by_unit if unit[0] == family and unit[1] == intensity})
        chosen = None
        for tier in TIERS:
            for seed in seeds:
                unit = (family, intensity, tier, seed)
                if unit in by_unit and feasible(unit):
                    chosen = unit
                    break
            if chosen is not None:
                break
        if chosen is not None:
            add(chosen, 'family_representative', True)
    remaining = [unit for unit in by_unit if feasible(unit) and unit not in [item['unit'] for item in selected]]
    if remaining:
        tight = min(remaining, key=lambda unit: (equal_arc(unit)['focus'] or {})['min_euclidean_m'])
        add(tight, 'smallest_euclidean_margin', True)
    failing = [
        unit for unit in by_unit
        if equal_arc(unit)['geometry_class'] == 'arc_pass_euclidean_fail'
    ]
    if failing:
        worst = min(failing, key=lambda unit: (equal_arc(unit)['focus'] or {})['min_euclidean_m'])
        add(worst, 'largest_euclidean_violation', False)
    present = {item['unit'][0] for item in selected}
    for family in FAMILIES:
        if family in present:
            continue
        candidates = [
            unit for unit in by_unit
            if unit[0] == family and unit[2] == 'near' and feasible(unit)
        ]
        if candidates:
            add(min(candidates, key=lambda unit: unit[3]), 'fill_family', True)
    for item in selected:
        item['unit'] = {
            'family': item['unit'][0], 'intensity': item['unit'][1],
            'tier': item['unit'][2], 'seed': item['unit'][3],
        }
        item['execute'] = bool(item['execute'])
    return selected


def _attachment(points, spec):
    rules = spec['execution_attachment']
    room = float(rules['room_m'])
    clearance = float(rules['curve_room_clearance_m'])
    center = np.asarray(points, dtype=float).mean(axis=0)
    shift = np.array([room / 2, room / 2]) - center
    translated = np.asarray(points, dtype=float) + shift
    if np.any(translated < clearance) or np.any(translated > room - clearance):
        return {'attached': False, 'reason': 'curve_outside_room_clearance', 'shift': shift.tolist()}
    polygon = Polygon(translated)
    if not polygon.is_valid:
        return {'attached': False, 'reason': 'translated_curve_invalid', 'shift': shift.tolist()}
    inner = polygon.buffer(-float(rules['inward_offset_m']))
    area = float(inner.area) if not inner.is_empty else 0.0
    if inner.is_empty or area < float(rules['minimum_interior_area_m2']):
        return {
            'attached': False, 'reason': 'interior_clearance_insufficient',
            'interior_area_m2': area, 'shift': shift.tolist(),
        }
    region = max(inner.geoms, key=lambda part: part.area) if inner.geom_type == 'MultiPolygon' else inner
    minx, miny, maxx, maxy = region.bounds
    radius = float(rules['person_radius_m'])
    chosen = []
    x = minx
    while x <= maxx + 1e-9:
        y = miny
        while y <= maxy + 1e-9:
            if region.covers(Point(x, y).buffer(radius)):
                chosen.append([float(x), float(y)])
            if len(chosen) >= int(rules['maximum_people']):
                break
            y += float(rules['grid_spacing_m'])
        if len(chosen) >= int(rules['maximum_people']):
            break
        x += float(rules['grid_spacing_m'])
    if len(chosen) < int(rules['minimum_people']):
        return {
            'attached': False, 'reason': 'fewer_than_three_people',
            'people': len(chosen), 'interior_area_m2': area, 'shift': shift.tolist(),
        }
    return {
        'attached': True, 'reason': None, 'people': chosen, 'curve': translated,
        'shift': shift.tolist(), 'interior_area_m2': area, 'room_m': room,
    }


def _execution_config(directory, people, curve, seed, count, method, spec):
    rules = spec['execution_attachment']
    database = directory / 'people.sqlite'
    directory.mkdir(parents=True, exist_ok=True)
    if database.exists():
        database.unlink()
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE trajectory_data (id INTEGER, frame INTEGER, pos_x REAL, pos_y REAL)'
        )
        connection.executemany(
            'INSERT INTO trajectory_data VALUES (?, 0, ?, ?)',
            [(index, person[0], person[1]) for index, person in enumerate(people)],
        )
    config = {
        'step': 1,
        'scene': {'type': 'square', 'width': float(rules['room_m']),
                  'height': float(rules['room_m']), 'openings': []},
        'crowd': {
            'count': len(people),
            'spawn_vertices': np.round(curve, 6).tolist(),
            'spacing': 0.55,
            'radius': {'mean': 0.20, 'std': 0.0, 'min': 0.20, 'max': 0.20},
            'demand': {'mean': 1.0, 'std': 0.0, 'min': 1.0, 'max': 1.0},
            'source': 'sqlite',
            'trajectory_file': str(database.resolve()),
        },
        'guides': {**rules['guides'], 'count': int(count)},
        'controller': {**rules['controller'], 'method': method},
        'simulation': {**rules['simulation'], 'seed': int(seed)},
    }
    path = directory / 'input.yaml'
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
    return path


def positions_from_arc(boundary, arc_positions):
    """Place targets by arc length on this curve.

    Cartesian rounding is not reused. A point rebuilt from its arc coordinate
    lies on a polyline edge of ``boundary``.
    """
    sites = np.sort(np.asarray(arc_positions, dtype=float) % float(boundary.length))
    return _interpolate(boundary, sites), sites


def _load_plan(planning_dir):
    payload = json.loads((Path(planning_dir) / 'geometry.json').read_text(encoding='utf-8'))
    if payload.get('arc_positions') is None:
        return None, payload
    from udc.deployment.planners import Plan
    sites = np.asarray(payload['arc_positions'], dtype=float)
    focus = payload.get('focus') or {}
    cost = (focus.get('uniform_cost') or {}).get('uniform_weighted_cost_m2') or 0.0
    plan = Plan(
        np.zeros((len(sites), 2)), sites, np.array([cost], dtype=float),
        float(focus['max_arc_gap_m']), payload['method'], int(focus.get('iterations') or 0),
        str(focus.get('exit_reason') or 'SUPPLIED'), 1.0,
    )
    return plan, payload


def execute_selected(spec, output, rows):
    selection = select_units(rows)
    _write(Path(output) / 'execution_selection.json', {
        'rules': spec['selection_rules'],
        'selected': selection,
        'note': 'execute is false for a geometric rejection and for any unit whose people attachment fails',
    })
    results = []
    executed = 0
    for item in selection:
        unit = item['unit']
        planning = Path(output) / 'planning' / (
            f'{unit["family"]}__{unit["intensity"]}__{unit["tier"]}'
        )
        reference = next(
            row for row in rows
            if _unit_key(row) == (unit['family'], unit['intensity'], unit['tier'], unit['seed'])
            and row['method'] == 'equal_arc'
        )
        built = build_prescribed_curve(unit['family'], unit['intensity'], unit['seed'])
        curve = built['points']
        attachment = _attachment(curve, spec)
        item_result = {**item, 'attachment': {key: attachment[key] for key in attachment if key != 'curve'}}
        if not item['execute'] or not attachment['attached']:
            if item['execute'] and not attachment['attached']:
                item_result['execute'] = False
                item_result['execution_skipped'] = attachment['reason']
            results.append(item_result)
            continue
        methods = spec['execution_attachment']['methods']
        item_result['methods'] = []
        for method in methods:
            if executed >= int(spec['execution_attachment']['max_tasks']):
                break
            if reference['geometry_class'] != 'target_geometry_feasible':
                break
            directory = Path(output) / 'execution' / (
                f'{unit["family"]}__{unit["intensity"]}__{unit["tier"]}'
            ) / method / f'seed_{unit["seed"]}'
            plan, payload = _load_plan(planning / method / f'seed_{unit["seed"]}')
            if plan is None or payload['geometry_class'] != 'target_geometry_feasible':
                item_result['methods'].append({
                    'method': method, 'executed': False, 'reason': 'planning_not_feasible',
                })
                continue
            config_path = _execution_config(
                directory, attachment['people'], attachment['curve'], unit['seed'],
                payload['count'], method, spec,
            )
            segment = np.linalg.norm(np.roll(attachment['curve'], -1, axis=0) - attachment['curve'], axis=1)
            deployment = np.asarray(attachment['curve'], dtype=float)
            hull = occupied_hull(np.asarray(attachment['people']), np.full(len(attachment['people']), 0.20))
            boundary = Boundary(
                np.asarray(hull.exterior.coords[:-1]), deployment,
                np.r_[0.0, np.cumsum(segment[:-1])], float(segment.sum()),
            )
            positions, sites = positions_from_arc(boundary, plan.arc_positions)
            metrics = experiment(
                config_path, directory / 'run', seed=unit['seed'], plots=False,
                protocol='fixed_n', fixed_n=int(payload['count']), method=method,
                prescribed_boundary=boundary,
                prescribed_plan=replace(
                    plan, positions=positions, arc_positions=sites,
                    max_gap=float(periodic_gaps(sites, boundary.length).max()),
                ),
            )
            offline = reevaluate_result(directory / 'run')
            item_result['methods'].append({
                'method': method,
                'executed': True,
                'termination_status': metrics['termination_status'],
                'stage_diagnosis': metrics['stage_diagnosis'],
                'criteria': metrics['criteria'],
                'boundary_source': metrics.get('boundary_source'),
                'prescribed_plan_installed': metrics.get('prescribed_plan_installed'),
                'reevaluation_criteria_match': offline['criteria'] == metrics['criteria'],
                'target_geometry_feasible': True,
                'path_reachable': bool(metrics['criteria'].get('path_ok')),
                'deployment_success': metrics['termination_status'] == 'SUCCESS',
            })
            executed += 1
        results.append(item_result)
    _write(Path(output) / 'execution_results.json', {'tasks_executed': executed, 'units': results})
    return results


def parameter_figure(destination):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(2, 3, figsize=(12, 8), constrained_layout=True)
    slots = [(family, intensity) for family in FAMILIES for intensity in INTENSITIES[family]]
    for axis, (family, intensity) in zip(axes.ravel(), slots):
        points, _ = nominal_ring(family, intensity)
        ring = np.vstack([points[:: max(1, len(points) // 800)], points[0]])
        axis.plot(ring[:, 0], ring[:, 1], color='#0f6c8c', lw=1.4)
        axis.set_aspect('equal')
        axis.grid(True, alpha=0.25)
        spec = FROZEN_GEOMETRY['curves'][(family, intensity)]
        axis.set_title(f'{family}\n{intensity} {spec}', fontsize=8)
    figure.savefig(destination / 'prescribed_curves.png', dpi=140)
    plt.close(figure)


def conflict_figures(output, rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    destination = Path(output) / 'figures'
    destination.mkdir(parents=True, exist_ok=True)
    # Map of Equal Arc classes. Other methods are compared in the summary table.
    labels = []
    classes = []
    for family in FAMILIES:
        for intensity in INTENSITIES[family]:
            for tier in TIERS:
                items = [
                    row for row in rows
                    if row['family'] == family and row['intensity'] == intensity
                    and row['tier'] == tier and row['method'] == 'equal_arc'
                ]
                labels.append(f'{family.split("_")[0]}\n{intensity}\n{tier}')
                classes.append(items)
    names = [
        'curve_invalid', 'count_necessary_violated', 'no_arc_feasible_plan',
        'arc_pass_euclidean_fail', 'target_geometry_feasible', 'planner_exception',
    ]
    colors = ['#6b7280', '#b45309', '#1d4ed8', '#b91c1c', '#047857', '#7c3aed']
    figure, axis = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    bottoms = np.zeros(len(classes))
    for name, color in zip(names, colors):
        heights = np.array([sum(item['geometry_class'] == name for item in group) for group in classes])
        if heights.sum() == 0:
            continue
        axis.bar(np.arange(len(classes)), heights, bottom=bottoms, color=color, label=name)
        bottoms += heights
    axis.set_xticks(np.arange(len(labels)))
    axis.set_xticklabels(labels, fontsize=7)
    axis.set_ylabel('Equal Arc alignments')
    axis.set_title('Geometry class by curve, intensity and guide tier')
    axis.legend(fontsize=7, ncol=2)
    figure.savefig(destination / 'geometry_classes.png', dpi=140)
    plt.close(figure)

    shown = 0
    for row in rows:
        focus = row.get('focus') or {}
        pair = focus.get('worst_pair')
        if row['method'] != 'equal_arc' or pair is None:
            continue
        interesting = (
            row['geometry_class'] == 'arc_pass_euclidean_fail'
            or (row['geometry_class'] == 'target_geometry_feasible'
                and focus.get('min_euclidean_m') is not None
                and focus['min_euclidean_m'] - 0.75 <= 0.25)
        )
        forced = (
            row['family'] == 'high_curvature_convex' and row['intensity'] == 'strong'
            and row['tier'] == 'margin' and row['seed'] == 15
        ) or (
            row['family'] == 'shallow_concave' and row['intensity'] == 'strong'
            and row['tier'] == 'margin' and row['seed'] == 15
        ) or (
            row['family'] == 'deep_notch' and row['intensity'] == 'wide'
            and row['tier'] == 'margin' and row['seed'] == 15
        )
        if (not interesting and not forced) or (shown >= 6 and not forced):
            continue
        directory = Path(output) / 'planning' / (
            f'{row["family"]}__{row["intensity"]}__{row["tier"]}'
        ) / row['method'] / f'seed_{row["seed"]}'
        payload = json.loads((directory / 'geometry.json').read_text(encoding='utf-8'))
        attempt = next(
            item for item in payload['attempts']
            if item.get('phase') == focus.get('phase')
        )
        curve = np.asarray(build_prescribed_curve(
            row['family'], row['intensity'], row['seed']
        )['points'])
        targets = np.asarray(attempt['positions'])
        figure, axes = plt.subplots(1, 2, figsize=(10, 4.5), constrained_layout=True)
        ring = np.vstack([curve, curve[0]])
        for axis in axes:
            axis.plot(ring[:, 0], ring[:, 1], color='#0f6c8c', lw=1.2)
            axis.scatter(targets[:, 0], targets[:, 1], c='#111827', s=18, zorder=3)
            axis.set_aspect('equal')
        i, j = pair['i'], pair['j']
        axes[0].plot(
            [targets[i, 0], targets[j, 0]], [targets[i, 1], targets[j, 1]],
            color='#b91c1c', lw=1.5,
        )
        axes[0].set_title(
            f'{row["geometry_class"]}\n{row["family"]} {row["intensity"]} {row["tier"]} seed {row["seed"]}\n'
            f'{pair["euclidean_m"]:.3f} m {"adjacent" if pair["adjacent"] else "non-adjacent"}'
        )
        pad = 1.1
        axes[1].set_xlim(min(targets[i, 0], targets[j, 0]) - pad, max(targets[i, 0], targets[j, 0]) + pad)
        axes[1].set_ylim(min(targets[i, 1], targets[j, 1]) - pad, max(targets[i, 1], targets[j, 1]) + pad)
        axes[1].plot(
            [targets[i, 0], targets[j, 0]], [targets[i, 1], targets[j, 1]],
            color='#b91c1c', lw=1.5,
        )
        axes[1].scatter(targets[[i, j], 0], targets[[i, j], 1], c='#b91c1c', s=28, zorder=3)
        axes[1].set_title('Zoom on the closest target pair')
        figure.savefig(destination / f'conflict_{shown}_{row["family"]}_{row["intensity"]}_{row["tier"]}_seed{row["seed"]}.png', dpi=140)
        plt.close(figure)
        shown += 1


def write_checksums(output, destination):
    output = Path(output)
    entries = []
    for path in sorted(item for item in output.rglob('*') if item.is_file()):
        data = path.read_bytes()
        entries.append({
            'path': path.relative_to(output).as_posix(),
            'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(),
        })
    payload = {'root': str(output), 'files': len(entries), 'entries': entries}
    _write(destination, payload)
    return payload


ADHESION_CASES = (
    ('shallow_concave', 'strong', 'near', 15, 'equal_arc'),
    ('shallow_concave', 'strong', 'near', 15, 'mass_cvt'),
    ('shallow_concave', 'strong', 'near', 15, 'projected_mass'),
)


def _curve_distance(curve, points):
    line = Polygon(np.asarray(curve, dtype=float)).exterior
    return np.array([line.distance(Point(point)) for point in np.asarray(points, dtype=float)])


def diagnose_frozen_adhesion(source):
    """Compare saved targets with the same curve, without rewriting those files."""
    source = Path(source)
    rows = []
    for family, intensity, tier, seed, method in ADHESION_CASES:
        run = source / 'execution' / f'{family}__{intensity}__{tier}' / method / f'seed_{seed}' / 'run'
        planning = source / 'planning' / f'{family}__{intensity}__{tier}' / method / f'seed_{seed}'
        data = np.load(run / 'trajectory.npz', allow_pickle=False)
        metrics = json.loads((run / 'metrics.json').read_text(encoding='utf-8'))
        geometry = json.loads((planning / 'geometry.json').read_text(encoding='utf-8'))
        curve = np.asarray(data['boundary_deployment'], dtype=float)
        targets = np.asarray(data['targets'], dtype=float)
        segment = np.linalg.norm(np.roll(curve, -1, axis=0) - curve, axis=1)
        boundary = Boundary(
            data['boundary_crowd'], curve, np.r_[0.0, np.cumsum(segment[:-1])], float(segment.sum()),
        )
        rebuilt, _ = positions_from_arc(boundary, geometry['arc_positions'])
        final = np.asarray(data['positions'][-1], dtype=float)
        active = np.asarray(data['active_mask'], dtype=bool)
        assignment = np.asarray(data['assignment'], dtype=int)
        tracking = np.linalg.norm(final[active] - targets[assignment[active]], axis=1)
        saved_xy = np.asarray(geometry['positions'], dtype=float)
        rows.append({
            'method': method,
            'plan_ok': metrics['criteria']['plan_ok'],
            'coverage_ok': metrics['criteria']['coverage_ok'],
            'curve_ok': metrics['criteria']['curve_ok'],
            'arrival_ok': metrics['criteria']['arrival_ok'],
            'saved_target_curve_distance_max_m': float(_curve_distance(curve, targets).max()),
            'sample_curve_distance_max_m': float(_curve_distance(curve, curve).max()),
            'arc_reinterpolation_curve_distance_max_m': float(_curve_distance(curve, rebuilt).max()),
            'json_xy_rounded_to_6_decimals': bool(np.max(np.abs(saved_xy - np.round(saved_xy, 6))) == 0.0),
            'tracking_max_m': float(tracking.max()),
            'final_guide_curve_distance_max_m': float(_curve_distance(curve, final[active]).max()),
            'position_tolerance_m': 0.03,
            'curve_tolerance_m': 0.04,
            'plan_curve_limit_m': 1e-7,
        })
    xy_off = all(row['saved_target_curve_distance_max_m'] > 1e-7 for row in rows)
    arc_on = all(row['arc_reinterpolation_curve_distance_max_m'] <= 1e-7 for row in rows)
    samples_on = all(row['sample_curve_distance_max_m'] <= 1e-7 for row in rows)
    tracking_inside = all(row['tracking_max_m'] <= row['position_tolerance_m'] for row in rows)
    guides_inside = all(row['final_guide_curve_distance_max_m'] <= row['curve_tolerance_m'] for row in rows)
    if xy_off and arc_on and samples_on and tracking_inside and guides_inside:
        cause = 'cartesian_rounding'
    elif not tracking_inside:
        cause = 'tracking_deviation'
    else:
        cause = 'unresolved'
    return {
        'source': str(source),
        'cases': rows,
        'cause': cause,
        'threshold_unchanged_m': 1e-7,
        'original_results_modified': False,
    }


def rerun_adhesion(spec, source, destination):
    """Reinstall the frozen arc coordinates on the evaluation curve and execute again.

    The previous execution directory is only read. Targets are not snapped after
    the fact; they are rebuilt from the saved arc coordinates before tracking.
    """
    source = Path(source)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.rglob('metrics.json')):
        raise FileExistsError(f'Adhesion rerun already has metrics: {destination}')
    diagnosis = diagnose_frozen_adhesion(source)
    _write(destination / 'adhesion_diagnosis.json', diagnosis)
    results = []
    for family, intensity, tier, seed, method in ADHESION_CASES:
        built = build_prescribed_curve(family, intensity, seed)
        attachment = _attachment(built['points'], spec)
        if not attachment['attached']:
            results.append({'method': method, 'executed': False, 'reason': attachment['reason']})
            continue
        plan, payload = _load_plan(
            source / 'planning' / f'{family}__{intensity}__{tier}' / method / f'seed_{seed}'
        )
        directory = destination / 'execution' / f'{family}__{intensity}__{tier}' / method / f'seed_{seed}'
        config_path = _execution_config(
            directory, attachment['people'], attachment['curve'], seed, payload['count'], method, spec,
        )
        segment = np.linalg.norm(np.roll(attachment['curve'], -1, axis=0) - attachment['curve'], axis=1)
        deployment = np.asarray(attachment['curve'], dtype=float)
        hull = occupied_hull(np.asarray(attachment['people']), np.full(len(attachment['people']), 0.20))
        boundary = Boundary(
            np.asarray(hull.exterior.coords[:-1]), deployment,
            np.r_[0.0, np.cumsum(segment[:-1])], float(segment.sum()),
        )
        positions, sites = positions_from_arc(boundary, plan.arc_positions)
        metrics = experiment(
            config_path, directory / 'run', seed=seed, plots=False, protocol='fixed_n',
            fixed_n=int(payload['count']), method=method, prescribed_boundary=boundary,
            prescribed_plan=replace(
                plan, positions=positions, arc_positions=sites,
                max_gap=float(periodic_gaps(sites, boundary.length).max()),
            ),
        )
        offline = reevaluate_result(directory / 'run')
        results.append({
            'method': method,
            'executed': True,
            'termination_status': metrics['termination_status'],
            'criteria': metrics['criteria'],
            'success': metrics['success'],
            'reevaluation_criteria_match': offline['criteria'] == metrics['criteria'],
            'max_curve_distance': metrics.get('max_curve_distance'),
            'max_tracking_error': metrics.get('max_tracking_error'),
            'target_curve_distance_max_m': float(_curve_distance(deployment, positions).max()),
        })
    _write(destination / 'adhesion_rerun.json', {
        'diagnosis_cause': diagnosis['cause'],
        'original_source': str(source),
        'original_results_modified': False,
        'results': results,
    })
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description='Prescribed-curve geometry diagnostic')
    parser.add_argument('--spec', default='experiments/step1_general_curve_diagnostics.yaml')
    parser.add_argument('--output', default='runs/step1_general_curve')
    parser.add_argument('--source', default='runs/step1_general_curve')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--figures-only', action='store_true')
    parser.add_argument('--execute-selected', action='store_true')
    parser.add_argument('--diagnose-adhesion', action='store_true')
    parser.add_argument('--rerun-adhesion', action='store_true')
    args = parser.parse_args(argv)
    spec = load_spec(args.spec)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if args.rerun_adhesion or args.diagnose_adhesion:
        if args.rerun_adhesion:
            rerun_adhesion(spec, args.source, output)
        else:
            _write(output / 'adhesion_diagnosis.json', diagnose_frozen_adhesion(args.source))
        return
    parameter_figure(output / 'figures')
    if args.figures_only:
        return
    catalog = build_catalog(spec)
    frozen = []
    for built in catalog:
        frozen.append(_curve_record(built))
    source = provenance({'diagnostic': spec['_sha256']}, None, 'prescribed_curve_diagnostic', None)
    _write(output / 'frozen_curves.json', {
        'spec_sha256': spec['_sha256'],
        'seeds': spec['development_seeds'],
        'seed_role': 'parameter origin rotation; shape is not redrawn',
        'count_formula': {
            'near': 'max(3, ceil(L / 1.94))',
            'margin': 'max(near, ceil(L / (0.75 * pi / 2)))',
            'separation_upper_bound': 'floor(L / 0.75)',
            'not_reduced_after_planning': True,
        },
        'provenance': source,
        'curves': frozen,
    })
    if args.execute_selected:
        rows = []
        for path in sorted((output / 'planning').rglob('summary.json')):
            rows.append(json.loads(path.read_text(encoding='utf-8')))
        execute_selected(spec, output, rows)
        return
    tasks = build_tasks(spec, catalog, output)
    if len(tasks) != 300:
        raise ValueError(f'Expected 300 planning tasks, got {len(tasks)}')
    rows = run_tasks(tasks, args.workers)
    summary = summarize(rows)
    summary['spec_sha256'] = spec['_sha256']
    summary['provenance'] = source
    _write(output / 'planning_summary.json', summary)
    _write(output / 'planning_rows.json', rows)
    conflict_figures(output, rows)
    print(json.dumps({
        'tasks': summary['tasks'],
        'valid_curve_slots': summary['valid_curve_slots'],
        'deployment_successes': summary['deployment_successes'],
    }, indent=2))


if __name__ == '__main__':
    main()
