"""Whether a valid static crowd can produce a narrow deployment curve.

The disk-union estimator sees positions and radii only. Convex hull results are
reported beside it and are not described as non-convex boundaries. The prescribed
narrow slot is an oracle reference and is not passed to the estimator.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import pstats
import sqlite3
from pathlib import Path
from time import perf_counter, process_time

import numpy as np
import yaml
from shapely.geometry import Point, Polygon

from udc.boundary import estimate_boundary, estimate_disk_union
from udc.boundary.prescribed import build_prescribed_curve, curve_report
from udc.deployment.constrained import optimize_pairwise
from udc.deployment.geometry import pair_geometry
from udc.deployment.planners import coverage_statistics, demand_profile, plan_coverage, quadrature_mass
from udc.deployment.risk_aware import plan_projected_mass
from udc.experiments.curve_diagnostics import _jsonable, _write, write_checksums
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, reevaluate_result
from udc.evaluation.acceptance import evaluate
from udc.navigation import navigate_and_assign
from udc.safety import Safety, path_clearances, safe_velocity


def load_spec(path):
    path = Path(path)
    spec = yaml.safe_load(path.read_text(encoding='utf-8'))
    if spec.get('protocol') != 'scene_closure_diagnostic':
        raise ValueError('Unsupported scene-closure spec')
    spec['_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return spec


def scene_people(scene, radius):
    if scene['kind'] == 'grid':
        origin = np.asarray(scene['origin'], dtype=float)
        spacing = np.asarray(scene['spacing'], dtype=float)
        points = [
            origin + np.array([column * spacing[0], row * spacing[1]])
            for row in range(int(scene['rows'])) for column in range(int(scene['columns']))
        ]
    elif scene['kind'] == 'u':
        origin = np.asarray(scene['origin'], dtype=float)
        spacing = float(scene['arm_spacing'])
        left = float(origin[0])
        right = left + 2 * float(radius) + float(scene['body_gap'])
        heights = np.arange(0.0, float(scene['arm_length']) + 1e-9, spacing)
        widths = np.arange(spacing, (right - left) - 1e-9, spacing)
        points = [[left, float(origin[1] + height)] for height in heights]
        points += [[right, float(origin[1] + height)] for height in heights]
        points += [[left + float(width), float(origin[1])] for width in widths]
    else:
        raise ValueError(f"Unknown scene kind {scene['kind']}")
    coordinates = np.asarray(points, dtype=float)
    return coordinates, np.full(len(coordinates), float(radius))


def _people_report(points, radii, room, minimum):
    distance = np.linalg.norm(points[:, None, :] - points[None, :, :], axis=2)
    np.fill_diagonal(distance, np.inf)
    margin = float(radii.max())
    return {
        'count': int(len(points)),
        'radius_m': float(radii.max()),
        'min_center_distance_m': float(distance.min()),
        'bodies_overlap': bool(distance.min() < 2 * float(radii.max()) - 1e-9),
        'meets_stated_separation': bool(distance.min() + 1e-12 >= float(minimum)),
        'inside_room': bool(np.all(points >= margin) and np.all(points <= float(room) - margin)),
    }


def _self_approach(boundary, skip=0.50):
    points = np.asarray(boundary.deployment, dtype=float)
    arcs = np.asarray(boundary.arc, dtype=float)
    length = float(boundary.length)
    best = np.inf
    pair = None
    for index, point in enumerate(points):
        delta = (arcs - arcs[index] + length / 2) % length - length / 2
        far = np.abs(delta) > skip
        if not np.any(far):
            continue
        distances = np.linalg.norm(points[far] - point, axis=1)
        local = float(distances.min())
        if local < best:
            best = local
            pair = (int(index), int(np.flatnonzero(far)[int(np.argmin(distances))]))
    return None if not np.isfinite(best) else float(best), pair


def _curve_record(boundary, error=None):
    if error is not None:
        return {'valid': False, 'error': str(error), 'evaluated': True}
    report = curve_report(boundary.deployment)
    approach, pair = _self_approach(boundary, 0.50)
    far_approach, far_pair = _self_approach(boundary, 2.00)
    report['self_approach_beyond_0_50_m'] = approach
    report['self_approach_pair'] = pair
    report['self_approach_beyond_2_00_m'] = far_approach
    report['self_approach_beyond_2_00_m_pair'] = far_pair
    report['length_m'] = float(boundary.length)
    return report


def _estimate_all(spec, points, radii):
    room = np.array([float(spec['room_m']), float(spec['room_m'])])
    wall = float(spec['wall_distance_m'])
    crowd = float(spec['crowd_distance_m'])
    spacing = float(spec['sample_spacing_m'])
    records = {}
    try:
        convex = estimate_boundary(
            points, float(spec['primary_offset_m']), spacing, room, wall, crowd, radii,
        )
        records['convex_offset'] = _curve_record(convex)
        records['convex_offset']['boundary'] = convex
    except ValueError as error:
        records['convex_offset'] = _curve_record(None, error)
    for name, offset in (
        ('disk_union_primary', spec['primary_offset_m']),
        ('disk_union_safety_floor', spec['safety_floor_offset_m']),
    ):
        try:
            estimated = estimate_disk_union(
                points, float(offset), spacing, room, wall, crowd, radii,
                closing_radius=float(spec['closing_radius_m']),
                quad_segs=int(spec['quad_segs']),
            )
            records[name] = _curve_record(estimated)
            records[name]['offset_m'] = float(offset)
            records[name]['boundary'] = estimated
        except ValueError as error:
            records[name] = _curve_record(None, error)
            records[name]['offset_m'] = float(offset)
    return records


def _select(spec, scenes):
    """Apply the frozen selection rule. Planner results are not an input."""
    chosen = []
    for scene in scenes:
        primary = scene['estimates']['disk_union_primary']
        if primary.get('valid'):
            chosen.append({**scene, 'estimate_name': 'disk_union_primary', 'boundary': primary['boundary']})
        if len(chosen) >= int(spec['max_execution_curves']):
            return chosen
    for scene in scenes:
        primary = scene['estimates']['disk_union_primary']
        defect = primary.get('convexity_defect')
        needs_floor = (not primary.get('valid')) or (defect is not None and defect < 0.001)
        floor = scene['estimates']['disk_union_safety_floor']
        if needs_floor and floor.get('valid'):
            chosen.append({
                **scene, 'estimate_name': 'disk_union_safety_floor', 'boundary': floor['boundary'],
            })
        if len(chosen) >= int(spec['max_execution_curves']):
            break
    return chosen


def _near_count(length, gap):
    return max(3, int(np.ceil(float(length) / float(gap) - 1e-12)))


def _plan_methods(spec, boundary):
    gap = float(spec['gap_limit_m'])
    separation = float(spec['center_separation_m'])
    count = _near_count(boundary.length, gap)
    samples = int(spec['quadrature_samples'])
    density = np.ones(len(boundary.deployment))
    quadrature, mass, length = quadrature_mass(boundary, density, samples)
    results = {}
    equal = None
    projected = None
    for phase in spec['legacy_phases']:
        if equal is None:
            plan = plan_coverage(
                boundary, None, None, count, None, 'equal_arc', float(phase), samples,
                int(spec['legacy_max_iterations']), density_override=density,
            )
            geometry = pair_geometry(boundary, plan.arc_positions, gap, separation)
            if geometry['accepted_by_controller_rule']:
                equal = (plan, geometry)
        if projected is None:
            plan = plan_projected_mass(
                boundary, None, None, count, None, gap, separation, float(phase), samples,
                int(spec['legacy_max_iterations']), density_override=density,
            )
            geometry = pair_geometry(boundary, plan.arc_positions, gap, separation)
            if geometry['accepted_by_controller_rule']:
                projected = (plan, geometry)
        if equal is not None and projected is not None:
            break
    if equal is None:
        plan = plan_coverage(
            boundary, None, None, count, None, 'equal_arc', 0.0, samples,
            int(spec['legacy_max_iterations']), density_override=density,
        )
        equal = (plan, pair_geometry(boundary, plan.arc_positions, gap, separation))
    if projected is None:
        plan = plan_projected_mass(
            boundary, None, None, count, None, gap, separation, 0.0, samples,
            int(spec['legacy_max_iterations']), density_override=density,
        )
        projected = (plan, pair_geometry(boundary, plan.arc_positions, gap, separation))
    results['equal_arc'] = _plan_public('equal_arc', equal[0], equal[1], quadrature, mass, length, separation)
    results['projected_mass'] = _plan_public(
        'projected_mass', projected[0], projected[1], quadrature, mass, length, separation,
    )
    phases = [float(fraction) * length for fraction in spec['phase_fractions']]
    starts = [
        ('projected_mass_phase0', projected[0].arc_positions),
        ('equal_arc_phase0', equal[0].arc_positions),
    ]
    for fraction in spec['phase_fractions']:
        if float(fraction) == 0.0:
            continue
        starts.append((
            f'projected_mass_rotate_{float(fraction):.2f}L',
            (np.asarray(projected[0].arc_positions) + float(fraction) * length) % length,
        ))
    solved = optimize_pairwise(
        boundary, starts, quadrature, mass, gap, separation,
        int(spec['solver_maxiter']), float(spec['per_start_timeout_s']),
    )
    plan = solved.pop('plan')
    solved['arc_positions'] = None if plan is None else plan.arc_positions.tolist()
    solved['positions'] = None if plan is None else plan.positions.tolist()
    solved['plan_object'] = plan
    results['pairwise_euclidean'] = solved
    results['equal_arc']['plan_object'] = equal[0] if results['equal_arc']['accepted'] else None
    results['projected_mass']['plan_object'] = projected[0] if results['projected_mass']['accepted'] else None
    return count, results


def _plan_public(method, plan, geometry, quadrature, mass, length, separation):
    cost = coverage_statistics(quadrature, mass, plan.arc_positions, length, np.inf)['weighted_cost']
    accepted = bool(geometry['arc_box_ok'] and geometry['euclidean_ok'])
    return {
        'method': method,
        'accepted': accepted,
        'outcome': 'feasible_witness' if accepted else 'no_plan_found',
        'best_cost': cost,
        'min_euclidean_m': geometry['min_euclidean_m'],
        'min_nonadjacent_euclidean_m': geometry['min_nonadjacent_euclidean_m'],
        'max_arc_gap_m': geometry['max_arc_gap_m'],
        'worst_pair': geometry['worst_pair'],
        'all_pairs_checked': True,
        'pair_count': len(geometry['pairwise']),
        'max_euclidean_violation_m': float(max(
            0.0, separation - geometry['min_euclidean_m']
        )),
        'global_optimum': False,
        'arc_positions': np.asarray(plan.arc_positions, dtype=float).tolist() if accepted else None,
        'positions': np.asarray(plan.positions, dtype=float).tolist() if accepted else None,
        'unevaluated_until_execution': ['path', 'safety_filter', 'terminal_acceptance'],
    }


def _execution_config(directory, people, curve, count, seed, spec):
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / 'people.sqlite'
    if database.exists():
        database.unlink()
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE trajectory_data (id INTEGER, frame INTEGER, pos_x REAL, pos_y REAL)'
        )
        connection.executemany(
            'INSERT INTO trajectory_data VALUES (?, 0, ?, ?)',
            [(index, float(point[0]), float(point[1])) for index, point in enumerate(people)],
        )
    config = {
        'step': 1,
        'scene': {'type': 'square', 'width': float(spec['room_m']),
                  'height': float(spec['room_m']), 'openings': []},
        'crowd': {
            'count': int(len(people)),
            # Schema container only. Positions come from the sqlite snapshot.
            # The controller receives the estimated curve through prepare_prescribed.
            'spawn_vertices': np.asarray(curve, dtype=float).tolist(),
            'spacing': 0.55,
            'radius': {'mean': 0.20, 'std': 0.0, 'min': 0.20, 'max': 0.20},
            'demand': {'mean': 1.0, 'std': 0.0, 'min': 1.0, 'max': 1.0},
            'source': 'sqlite',
            'trajectory_file': str(database.resolve()),
        },
        'guides': {**spec['guides'], 'count': int(count)},
        'controller': {**spec['execution_controller'], 'method': 'projected_mass'},
        'simulation': {**spec['simulation'], 'seed': int(seed)},
    }
    path = directory / 'input.yaml'
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
    return path


def _public_estimate(record):
    return {key: value for key, value in record.items() if key != 'boundary'}


def _stage_timings(boundary, people, radii, output):
    """One fixed case, timed without cProfile. Not comparable to profiled runs."""
    room = np.array([30.0, 30.0])
    demand = np.ones(len(people))
    gap, separation = 1.94, 0.75
    count = _near_count(boundary.length, gap)
    timings = {}

    def measure(name, function):
        started = perf_counter()
        cpu = process_time()
        try:
            value = function()
            error = None
        except (ValueError, RuntimeError) as exc:
            value = None
            error = str(exc)
        timings[name] = {
            'wall_seconds': perf_counter() - started,
            'cpu_seconds': process_time() - cpu,
            'error': error,
        }
        return value

    measure('demand_density', lambda: demand_profile(boundary, people, demand, 1.2, 'mass_cvt'))
    measure('curve_report', lambda: curve_report(boundary.deployment))
    plan = measure('equal_arc_plan', lambda: plan_coverage(
        boundary, people, demand, count, 1.2, 'equal_arc', 0.0, 2048, 300,
    ))
    if plan is not None:
        measure('pairwise_check', lambda: pair_geometry(boundary, plan.arc_positions, gap, separation))
        inset = 1.0
        line = Polygon([(inset, inset), (30 - inset, inset), (30 - inset, 30 - inset), (inset, 30 - inset)]).exterior
        guides = np.array([
            line.interpolate((index / count) * line.length).coords[0] for index in range(count)
        ])
        crowd = Polygon(boundary.crowd)
        navigation = measure(
            'assignment_and_paths',
            lambda: navigate_and_assign(guides, plan.positions, room, 0.55, crowd, 0.55),
        )
        measure('execution_interval_check', lambda: path_clearances(guides, guides, people, room))
        limits = Safety(0.75, 0.75, 0.55, 1.0, 1e-7)
        measure(
            'safety_filter',
            lambda: safe_velocity(guides, np.zeros_like(guides), people, room, 0.1, limits),
        )
        if navigation is not None:
            assignment = navigation.assignment
            active = assignment >= 0
            trajectory = np.stack([guides, plan.positions])
            velocities = np.zeros((1, count, 2))
            measure('independent_evaluation', lambda: evaluate(
                trajectory, velocities, active, plan.positions, assignment, boundary, limits,
                {'hold_steps': 1, 'position_tolerance': 0.03, 'speed_tolerance': 0.03,
                 'curve_tolerance': 0.04, 'max_gap_limit': 2.0, 'arc_tolerance': 0.03,
                 'demand_bandwidth': 1.2, '_people': people, '_radii': radii, '_room': room,
                 '_demand': demand},
            ))
    measure('json_write', lambda: _write(output / 'stage_touch.json', {'ok': True}))
    timings['curve_samples'] = int(len(boundary.deployment))
    timings['people'] = int(len(people))
    timings['guides'] = int(count)
    timings['pair_constraints'] = int(count * (count - 1) / 2)
    timings['cprofile_included'] = False
    _write(output / 'stage_timings.json', timings)
    return timings


def _profile_case(spec, boundary, people, radii, output):
    import tracemalloc
    from udc.deployment.planners import periodic_gaps
    gap = float(spec['gap_limit_m'])
    separation = float(spec['center_separation_m'])
    count = _near_count(boundary.length, gap)
    density = np.ones(len(boundary.deployment))
    quadrature, mass, length = quadrature_mass(boundary, density, int(spec['quadrature_samples']))
    tracemalloc.start()
    started = perf_counter()
    cpu = process_time()
    solved = optimize_pairwise(
        boundary,
        [('equal_arc_phase0', np.arange(count) * length / count)],
        quadrature, mass, gap, separation, int(spec['solver_maxiter']), float(spec['per_start_timeout_s']),
    )
    elapsed = perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    plan = solved.pop('plan')
    profile = cProfile.Profile()
    profile.enable()
    if plan is not None:
        pair_geometry(boundary, plan.arc_positions, gap, separation)
        periodic_gaps(plan.arc_positions, length)
    profile.disable()
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats(15)
    _write(output / 'profile_pairwise.json', {
        'separate_from_execution_timings': True,
        'profiler_overhead_not_for_comparison': True,
        'wall_seconds': elapsed,
        'cpu_seconds': process_time() - cpu,
        'tracemalloc_peak_bytes': int(peak),
        'count': count,
        'curve_samples': int(len(boundary.deployment)),
        'pair_constraints': int(count * (count - 1) / 2),
        'solver_maxiter': int(spec['solver_maxiter']),
        'accepted': plan is not None,
        'cprofile_top': stream.getvalue(),
    })


def main(argv=None):
    parser = argparse.ArgumentParser(description='Scene realizability of narrow deployment curves')
    parser.add_argument('--spec', default='experiments/step1_scene_closure.yaml')
    parser.add_argument('--output', default='runs/step1_scene_closure')
    args = parser.parse_args(argv)
    spec = load_spec(args.spec)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    source = provenance({'spec': spec['_sha256']}, None, 'scene_closure_diagnostic', None)
    oracle = build_prescribed_curve('deep_notch', 'narrow', 15)
    scenes = []
    for scene in spec['scenes']:
        points, radii = scene_people(scene, spec['person_radius_m'])
        people = _people_report(points, radii, spec['room_m'], spec['minimum_center_distance_m'])
        estimates = _estimate_all(spec, points, radii)
        scenes.append({
            'scene': scene, 'people': points, 'radii': radii,
            'people_report': people, 'estimates': estimates,
        })
    chosen = _select(spec, scenes)
    _write(output / 'selection.json', {
        'spec_sha256': spec['_sha256'],
        'provenance': source,
        'formal_validation': False,
        'selection_rules': spec['selection_rules'],
        'selected': [
            {'scene': item['scene']['name'], 'estimate': item['estimate_name']} for item in chosen
        ],
        'oracle_narrow_slot': {
            'boundary_source': 'prescribed_diagnostic',
            'passed_to_estimator': False,
            'passed_to_controller': False,
            'measured_slot_width_m': oracle['report'].get('measured_slot_width_m'),
            'valid': oracle['report']['valid'],
        },
        'inward_1_6_m': 'not used; it was a prescribed-curve attachment inset, not a safety distance',
    })
    estimation = []
    for scene in scenes:
        estimation.append({
            'name': scene['scene']['name'],
            'people': scene['people_report'],
            'estimates': {name: _public_estimate(record) for name, record in scene['estimates'].items()},
        })
    _write(output / 'estimation.json', estimation)
    executed = 0
    execution_rows = []
    profile_boundary = None
    profile_people = None
    for item in chosen:
        boundary = item['boundary']
        count, plans = _plan_methods(spec, boundary)
        for method in spec['execution_methods']:
            plan = plans[method]
            row = {
                'scene': item['scene']['name'],
                'estimate': item['estimate_name'],
                'method': method,
                'count': count,
                'curve_valid': True,
                'plan_outcome': plan['outcome'],
                'plan_accepted': bool(plan['accepted']),
                'best_cost': plan.get('best_cost'),
                'min_euclidean_m': None,
                'executed': False,
            }
            focus = plan.get('attempts', [plan])[-1] if plan.get('attempts') else plan
            row['min_euclidean_m'] = plan.get('min_euclidean_m', focus.get('min_euclidean_m') if isinstance(focus, dict) else None)
            if not plan['accepted'] or plan.get('plan_object') is None:
                row['execution'] = 'not_run_plan_not_accepted'
                execution_rows.append(row)
                continue
            if executed >= int(spec['max_execution_tasks']):
                row['execution'] = 'not_run_task_cap'
                execution_rows.append(row)
                continue
            directory = output / 'execution' / item['scene']['name'] / item['estimate_name'] / method
            config_path = _execution_config(
                directory, item['people'], boundary.deployment, count, spec['guide_seed'], spec,
            )
            metrics = experiment(
                config_path, directory / 'run', seed=int(spec['guide_seed']), plots=False,
                protocol='fixed_n', fixed_n=int(count), method='projected_mass',
                method_label=method, prescribed_boundary=boundary,
                prescribed_plan=plan['plan_object'], boundary_source='estimated_disk_union',
            )
            offline = reevaluate_result(directory / 'run')
            row.update({
                'executed': True,
                'termination_status': metrics['termination_status'],
                'criteria': metrics['criteria'],
                'success': metrics['success'],
                'reevaluation_match': offline['criteria'] == metrics['criteria'],
                'path_reachable': bool(metrics['criteria'].get('path_ok')),
                'minimum_net_clearances': metrics.get('minimum_net_clearances'),
                'wall_seconds': metrics.get('wall_time'),
            })
            execution_rows.append(row)
            executed += 1
            if profile_boundary is None and method == 'equal_arc':
                profile_boundary = boundary
                profile_people = (item['people'], item['radii'])
        for method in plans:
            plans[method].pop('plan_object', None)
        _write(output / 'plans' / f"{item['scene']['name']}__{item['estimate_name']}.json", {
            'scene': item['scene']['name'], 'estimate': item['estimate_name'], 'count': count,
            'methods': plans,
        })
    _write(output / 'execution.json', {'tasks_executed': executed, 'rows': execution_rows})
    profile_dir = output / 'profile'
    profile_dir.mkdir(parents=True, exist_ok=True)
    if profile_boundary is not None:
        _stage_timings(profile_boundary, profile_people[0], profile_people[1], profile_dir)
        _profile_case(spec, profile_boundary, profile_people[0], profile_people[1], profile_dir)
    elif chosen:
        _stage_timings(chosen[0]['boundary'], chosen[0]['people'], chosen[0]['radii'], profile_dir)
        _profile_case(spec, chosen[0]['boundary'], chosen[0]['people'], chosen[0]['radii'], profile_dir)
    print(json.dumps({
        'selected': [item['scene']['name'] + ':' + item['estimate_name'] for item in chosen],
        'executed': executed,
    }, indent=2))


if __name__ == '__main__':
    main()
