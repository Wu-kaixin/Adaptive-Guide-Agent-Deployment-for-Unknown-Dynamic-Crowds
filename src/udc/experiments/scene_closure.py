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


def _plan_methods(spec, boundary, count=None):
    gap = float(spec['gap_limit_m'])
    separation = float(spec['center_separation_m'])
    if count is None:
        count = _near_count(boundary.length, gap)
    else:
        count = int(count)
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


FROZEN_PROBE_SEEDS = (20, 21, 22, 23, 24)
USED_DEVELOPMENT_SEEDS = set(range(0, 20))
RESERVED_FORMAL_SEEDS = set(range(5000, 5030))


def load_probe_spec(path):
    path = Path(path)
    spec = yaml.safe_load(path.read_text(encoding='utf-8'))
    if spec.get('protocol') != 'scene_closure_seed_probe':
        raise ValueError('Unsupported scene-closure seed probe')
    if tuple(int(seed) for seed in spec['seeds']) != FROZEN_PROBE_SEEDS:
        raise ValueError('The frozen probe seeds are 20, 21, 22, 23 and 24')
    spec['_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    spec['_path'] = str(path)
    return spec


def resolve_probe_seeds(spec, requested):
    frozen = [int(seed) for seed in spec['seeds']]
    if requested is None:
        return frozen
    requested = [int(seed) for seed in requested]
    unknown = sorted(set(requested) - set(frozen))
    if unknown:
        raise ValueError(f'Seed is outside the frozen probe list: {unknown}')
    blocked = sorted(set(requested) & (USED_DEVELOPMENT_SEEDS | RESERVED_FORMAL_SEEDS))
    if blocked:
        raise ValueError(f'Seed collides with a used or reserved seed: {blocked}')
    return [seed for seed in frozen if seed in set(requested)]


def _probe_streams(seed):
    return {
        'people': [20261017, int(seed), 1],
        'guides': [20261017, int(seed), 2],
        'people_distribution': 'independent gaussian, one draw, no redraw',
        'guide_distribution': 'independent uniform arc shift on the inset perimeter, one draw',
    }


def perturb_people(points, seed, sigma):
    """One reproducible displacement. The caller checks physics and does not redraw."""
    rng = np.random.default_rng(np.random.SeedSequence([20261017, int(seed), 1]))
    return np.asarray(points, dtype=float) + rng.normal(0.0, float(sigma), size=np.shape(points))


def perturb_guides(room, inset, count, seed, amplitude):
    """Move each guide along the same inset perimeter used by Environment."""
    polygon = Polygon([(0.0, 0.0), (room, 0.0), (room, room), (0.0, room)])
    line = polygon.buffer(-float(inset), join_style='mitre').exterior
    rng = np.random.default_rng(np.random.SeedSequence([20261017, int(seed), 2]))
    shifts = rng.uniform(-float(amplitude), float(amplitude), size=int(count))
    length = float(line.length)
    positions = [
        line.interpolate(((index / count + 0.012) * length + float(shift)) % length).coords[0]
        for index, shift in enumerate(shifts)
    ]
    return np.asarray(positions, dtype=float), np.asarray(shifts, dtype=float)


def _input_hash(points, radii, guides):
    raw = np.ascontiguousarray(np.c_[points, radii]).tobytes()
    raw += np.ascontiguousarray(guides).tobytes()
    return hashlib.sha256(raw).hexdigest()


def _blank_row(scene, seed, method, count, stage, detail):
    return {
        'scene': scene, 'seed': int(seed), 'method': method, 'count': count,
        'stage': stage, 'detail': detail, 'executed': False, 'success': False,
        'termination_status': None, 'program_error': stage == 'program_error',
        'input_generation_failed': stage == 'input_generation_failed',
        'boundary_invalid': stage == 'boundary_invalid',
        'necessary_count_met': None, 'plan_accepted': False,
        'input_sha256': None,
        'planned_demand_weighted_cost': None,
        'actual_demand_weighted_cost': None,
        'actual_max_gap': None,
        'target_min_euclidean_distance': None,
        'minimum_net_clearances': None,
        'minimum_center_distances': None,
        'max_tracking_error': None,
        'max_curve_distance': None,
        'simulation_time': None,
        'trajectory_length_m': None,
        'path_length': None,
        'replan_count': None,
        'plan_wall_seconds': None,
        'execution_wall_seconds': None,
        'pairwise_attempt_seconds': None,
    }


def _metrics_fields(row, metrics, offline):
    trajectory = None
    online = metrics.get('criteria')
    row.update({
        'executed': True,
        'termination_status': metrics.get('termination_status'),
        'success': bool(metrics.get('success')),
        'stage': 'success' if metrics.get('success') else 'execution_or_acceptance_failed',
        'criteria': online,
        'reevaluation_criteria': None if offline is None else offline.get('criteria'),
        'reevaluation_match': None if offline is None else offline.get('criteria') == online,
        'planned_demand_weighted_cost': metrics.get('planned_demand_weighted_cost'),
        'actual_demand_weighted_cost': metrics.get('actual_demand_weighted_cost'),
        'actual_max_gap': metrics.get('actual_max_gap'),
        'target_min_euclidean_distance': metrics.get('target_min_euclidean_distance'),
        'minimum_net_clearances': metrics.get('minimum_net_clearances'),
        'minimum_center_distances': metrics.get('minimum_center_distances'),
        'max_tracking_error': metrics.get('max_tracking_error'),
        'max_curve_distance': metrics.get('max_curve_distance'),
        'simulation_time': metrics.get('simulation_time'),
        'path_length': metrics.get('path_length'),
        'replan_count': metrics.get('replan_count'),
        'execution_wall_seconds': metrics.get('wall_time'),
        'failure_reason': metrics.get('failure_reason'),
        'stage_diagnosis': metrics.get('stage_diagnosis'),
    })
    return trajectory


def _trajectory_length(directory):
    path = Path(directory) / 'trajectory.npz'
    if not path.is_file():
        return None
    with np.load(path) as data:
        if 'positions' not in data:
            return None
        positions = np.asarray(data['positions'], dtype=float)
    if positions.ndim != 3 or len(positions) < 2:
        return None
    return float(np.linalg.norm(np.diff(positions, axis=0), axis=2).sum())


def _probe_figures(output, rows):
    import matplotlib
    matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt
    figures = output / 'figures'
    figures.mkdir(parents=True, exist_ok=True)
    chosen = []
    for row in rows:
        if row.get('executed') and (row.get('success') or row.get('stage') != 'success'):
            chosen.append(row)
    # One panel per executed scene-seed, preferring equal_arc when it ran.
    panels = {}
    for row in rows:
        if not row.get('executed'):
            continue
        key = (row['scene'], row['seed'])
        panels.setdefault(key, row)
        if row['method'] == 'equal_arc':
            panels[key] = row
    for (scene, seed), row in panels.items():
        directory = output / 'execution' / scene / f'seed_{seed}' / row['method'] / 'run'
        trajectory = directory / 'trajectory.npz'
        if not trajectory.is_file():
            continue
        with np.load(trajectory) as data:
            people = np.asarray(data['crowd_positions'])
            guides = np.asarray(data['positions'])
            targets = np.asarray(data['targets']) if 'targets' in data else None
            curve = np.asarray(data['boundary_deployment']) if 'boundary_deployment' in data else None
        figure, axis = plt.subplots(figsize=(6, 6))
        if curve is not None and len(curve):
            closed = np.vstack([curve, curve[0]])
            axis.plot(closed[:, 0], closed[:, 1], color='#159a8c', lw=1)
        axis.scatter(people[:, 0], people[:, 1], s=12, c='#24344d', label='people')
        if guides.ndim == 3 and len(guides):
            for agent in range(guides.shape[1]):
                axis.plot(guides[:, agent, 0], guides[:, agent, 1], color='#c0392b', lw=0.8, alpha=0.8)
        if targets is not None:
            axis.scatter(targets[:, 0], targets[:, 1], marker='+', c='#1d4ed8', label='targets')
        axis.set_aspect('equal')
        axis.set_title(f'{scene} seed {seed} {row["method"]} {row.get("termination_status")}')
        figure.savefig(figures / f'{scene}_seed_{seed}.png', dpi=120)
        plt.close(figure)
    return str(figures)


def run_seed_probe(spec_path, output, requested_seeds=None):
    spec = load_probe_spec(spec_path)
    base = load_spec(spec['base_spec'])
    seeds = resolve_probe_seeds(spec, requested_seeds)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    scenes = {scene['name']: scene for scene in base['scenes']}
    order = list(spec['scenes'])
    counts = {name: int(value) for name, value in spec['guide_counts'].items()}
    if set(order) != set(counts):
        raise ValueError('Probe scenes and guide counts do not match')
    sigma = float(spec['perturbation']['people_sigma_m'])
    amplitude = float(spec['perturbation']['guide_amplitude_m'])
    if int(spec['perturbation']['draws']) != 1 or spec['perturbation'].get('resample_on_failure'):
        raise ValueError('The probe draws each stream once and does not resample')
    source = provenance({'spec': spec['_sha256'], 'base_spec': base['_sha256']}, None,
                        'scene_closure_seed_probe', None)
    rows = []
    for seed in seeds:
        for name in order:
            scene = scenes[name]
            count = counts[name]
            points, radii = scene_people(scene, base['person_radius_m'])
            perturbed = perturb_people(points, seed, sigma)
            report = _people_report(
                perturbed, radii, base['room_m'], base['minimum_center_distance_m'],
            )
            guides, shifts = perturb_guides(
                float(base['room_m']), float(base['guides']['initial_inset']), count, seed, amplitude,
            )
            valid_people = bool(report['meets_stated_separation'] and report['inside_room']
                                and not report['bodies_overlap'])
            input_dir = output / 'inputs' / name / f'seed_{seed}'
            input_dir.mkdir(parents=True, exist_ok=True)
            np.savez(
                input_dir / 'input.npz',
                base_positions=points, positions=perturbed, radii=radii,
                guides=guides, guide_arc_shifts=shifts,
                demand=np.ones(len(perturbed)),
            )
            shared = {
                'input_sha256': _input_hash(perturbed, radii, guides) if valid_people else None,
                'people_report': report,
                'guide_shift_min_m': float(shifts.min()),
                'guide_shift_max_m': float(shifts.max()),
                'streams': _probe_streams(seed),
            }
            if not valid_people:
                for method in spec['methods']:
                    row = _blank_row(name, seed, method, count, 'input_generation_failed',
                                     'one perturbation draw violated separation or the room')
                    row.update(shared)
                    rows.append(row)
                continue
            started = perf_counter()
            estimates = _estimate_all(base, perturbed, radii)
            estimate_seconds = perf_counter() - started
            primary = estimates['disk_union_primary']
            _write(input_dir / 'estimates.json', {
                name: _public_estimate(record) for name, record in estimates.items()
            })
            if not primary.get('valid'):
                for method in spec['methods']:
                    row = _blank_row(name, seed, method, count, 'boundary_invalid',
                                     primary.get('error', 'disk-union primary curve was rejected'))
                    row.update(shared)
                    row['estimate_wall_seconds'] = estimate_seconds
                    rows.append(row)
                continue
            boundary = primary['boundary']
            from udc.deployment.geometry import count_rule
            rule = count_rule(boundary.length, base['gap_limit_m'], base['center_separation_m'])
            necessary = int(rule['near'])
            necessary_met = count >= necessary
            plan_started = perf_counter()
            fixed_count, plans = _plan_methods(base, boundary, count=count)
            plan_seconds = perf_counter() - plan_started
            if fixed_count != count:
                raise RuntimeError('Probe guide count changed during planning')
            public_plans = {
                method: {key: value for key, value in plan.items() if key != 'plan_object'}
                for method, plan in plans.items()
            }
            _write(output / 'plans' / f'{name}__seed_{seed}.json', {
                'scene': name, 'seed': seed, 'count': count,
                'necessary_min_count': necessary, 'necessary_count_met': necessary_met,
                'curve_length_m': float(boundary.length),
                'plan_wall_seconds': plan_seconds,
                'methods': public_plans,
            })
            for method in spec['methods']:
                plan = plans[method]
                row = _blank_row(name, seed, method, count, 'planned', '')
                row.update(shared)
                row['necessary_count_met'] = necessary_met
                row['necessary_min_count'] = necessary
                row['curve_length_m'] = float(boundary.length)
                row['plan_accepted'] = bool(plan.get('accepted'))
                row['plan_outcome'] = plan.get('outcome')
                row['planned_demand_weighted_cost'] = plan.get('best_cost')
                row['target_min_euclidean_distance'] = plan.get('min_euclidean_m')
                row['plan_wall_seconds'] = plan_seconds
                row['estimate_wall_seconds'] = estimate_seconds
                attempts = plan.get('attempts') or []
                row['pairwise_attempt_seconds'] = [
                    attempt.get('solver_seconds')
                    for attempt in attempts if isinstance(attempt, dict)
                ] or None
                if not plan.get('accepted') or plan.get('plan_object') is None:
                    row['stage'] = 'necessary_count_violation' if not necessary_met else 'plan_not_accepted'
                    row['detail'] = (
                        f'fixed N={count} is below ceil(L/1.94)={necessary}'
                        if not necessary_met else plan.get('outcome')
                    )
                    rows.append(row)
                    continue
                directory = output / 'execution' / name / f'seed_{seed}' / method
                try:
                    config_path = _execution_config(
                        directory, perturbed, boundary.deployment, count, seed, base,
                    )
                    metrics = experiment(
                        config_path, directory / 'run', seed=int(seed), plots=False,
                        protocol='fixed_n', fixed_n=int(count), method='projected_mass',
                        method_label=method, prescribed_boundary=boundary,
                        prescribed_plan=plan['plan_object'],
                        boundary_source='estimated_disk_union',
                        initial_guides=guides,
                    )
                    offline = reevaluate_result(directory / 'run')
                    _metrics_fields(row, metrics, offline)
                    row['trajectory_length_m'] = _trajectory_length(directory / 'run')
                    if not necessary_met and row['stage'] != 'success':
                        row['stage'] = 'necessary_count_violation'
                except Exception as error:
                    row['stage'] = 'program_error'
                    row['program_error'] = True
                    row['detail'] = f'{type(error).__name__}: {error}'
                    row['executed'] = False
                rows.append(row)
            for method in plans:
                plans[method].pop('plan_object', None)
    _write(output / 'probe_summary.json', {
        'protocol': spec['protocol'],
        'spec_sha256': spec['_sha256'],
        'base_spec_sha256': base['_sha256'],
        'seeds': seeds,
        'provenance': source,
        'perturbation': spec['perturbation'],
        'guide_counts': counts,
        'formal_validation': False,
        'rows': rows,
    })
    if any(row.get('executed') for row in rows):
        _probe_figures(output, rows)
    print(json.dumps({
        'seeds': seeds,
        'rows': len(rows),
        'success': sum(bool(row.get('success')) for row in rows),
        'input_generation_failed': sum(row.get('stage') == 'input_generation_failed' for row in rows),
        'boundary_invalid': sum(row.get('stage') == 'boundary_invalid' for row in rows),
        'plan_not_accepted': sum(row.get('stage') == 'plan_not_accepted' for row in rows),
    }, indent=2))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description='Scene realizability of narrow deployment curves')
    parser.add_argument('--spec', default='experiments/step1_scene_closure.yaml')
    parser.add_argument('--output', default='runs/step1_scene_closure')
    parser.add_argument('--seeds', type=int, nargs='*')
    args = parser.parse_args(argv)
    preview = yaml.safe_load(Path(args.spec).read_text(encoding='utf-8'))
    if preview.get('protocol') == 'scene_closure_seed_probe':
        run_seed_probe(args.spec, args.output, args.seeds or None)
        return
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
