"""Geometry evidence and bounded development regression for target admission.

Raw snapshots remain only under runs/. This protocol never overwrites the v3
confirmation, redraws a rejected input, or substitutes another curve length.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
from time import perf_counter
import traceback

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon, mapping
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
import yaml
from threadpoolctl import threadpool_limits

from udc.deployment.admissible import admissible_phase
from udc.deployment.planners import _interpolate, periodic_gaps, coverage_statistics, quadrature_mass
from udc.experiments.nonconvex_rebuild import (load_boundary, load_plan, plan_target,
    rejected_result, array_hash, sha, write, _official_monitor)
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, reevaluate_result, _atomic_npz, _atomic_text
from udc.navigation import navigation_domain
from udc.navigation.paths import target_domain_check


DEFAULT = 'runs/step1_target_domain_consistency'
SPEC = 'configs/step1/target_domain_consistency.yaml'


class _OriginalPlanExhausted(ValueError):
    def __init__(self, details):
        super().__init__('PLAN_SEARCH_EXHAUSTED: original bounded planner found no target')
        self.details = details


def _parts(geometry, kind):
    if geometry.is_empty:
        return []
    if geometry.geom_type == kind:
        return [geometry]
    return [piece for child in getattr(geometry, 'geoms', ()) for piece in _parts(child, kind)]


def curve_domain_relation(boundary, domain, resolution=1e-5):
    """Exact polygonal intersections; penetration has a Lipschitz error bound.

    Segment endpoints and original arc coordinates are retained. Outside
    lengths do not rely on sampling. Only the maximum distance uses samples:
    distance to a fixed closed set is 1-Lipschitz, so the unsampled maximum is
    at most sample_max + maximum_sample_step / 2. GEOS double precision applies.
    """
    intervals, contacts = [], []
    for index, start in enumerate(boundary.deployment):
        end = boundary.deployment[(index + 1) % len(boundary.deployment)]
        segment = LineString([start, end])
        outside = segment.difference(domain.free)
        for piece in _parts(outside, 'LineString'):
            arcs = sorted(segment.project(Point(p)) for p in (piece.coords[0], piece.coords[-1]))
            begin, finish = float(boundary.arc[index] + arcs[0]), float(boundary.arc[index] + arcs[1])
            steps = max(1, int(np.ceil(piece.length / resolution)))
            positions = shapely.line_interpolate_point(piece, np.linspace(0., piece.length, steps + 1))
            depths = shapely.distance(positions, domain.free.boundary)
            low = float(np.max(depths))
            # All pieces lie outside free by exact intersection; endpoint depth
            # is zero except for a curve endpoint within an illegal interval.
            intervals.append({'start_arc_m': begin, 'end_arc_m': finish,
                'length_m': float(piece.length), 'original_edge': index,
                'coordinates': list(map(list, piece.coords)),
                'max_penetration_lower_m': low,
                'max_penetration_upper_m': low + float(piece.length / steps / 2),
                'maximum_sample_step_m': float(piece.length / steps)})
        contacts.extend({'original_edge': index, 'coordinates': list(p.coords[0])}
                        for p in _parts(outside, 'Point'))
    intervals.sort(key=lambda value: value['start_arc_m'])
    merged = []
    for interval in intervals:
        if merged and abs(merged[-1]['end_arc_m'] - interval['start_arc_m']) < 1e-10:
            merged[-1]['end_arc_m'] = interval['end_arc_m']
            merged[-1]['length_m'] += interval['length_m']
        else:
            merged.append({key: interval[key] for key in ('start_arc_m', 'end_arc_m', 'length_m')})
    line = LineString(np.vstack([boundary.deployment, boundary.deployment[0]]))
    outside_length = float(line.difference(domain.free).length)
    return {'curve_length_m': boundary.length, 'strict_illegal_length_m': outside_length,
            'strict_legal_length_m': float(line.intersection(domain.free).length),
            'intersection_length_residual_m': float(abs(sum(p['length_m'] for p in intervals) - outside_length)),
            'intervals': merged, 'per_edge_intersections': intervals, 'isolated_contacts': contacts,
            'wraps_seam': bool(merged and merged[0]['start_arc_m'] == 0.
                               and abs(merged[-1]['end_arc_m'] - boundary.length) < 1e-10),
            'max_penetration_lower_m': max((p['max_penetration_lower_m'] for p in intervals), default=0.),
            'max_penetration_upper_m': max((p['max_penetration_upper_m'] for p in intervals), default=0.),
            'distance_resolution_m': resolution,
            'scope': 'all original polygonal edges intersected; only depth maximum is bounded by discretization'}


def save_plan(directory, plan, details):
    directory.mkdir(parents=True, exist_ok=False)
    _atomic_npz(directory / 'targets.npz', targets=plan.positions,
                arc_positions=plan.arc_positions, cost=plan.cost)
    write(directory / 'plan.json', {'method': plan.method, 'iterations': plan.iterations,
          'exit_reason': plan.exit_reason, 'max_gap': plan.max_gap, 'density_total': plan.density_total,
          'targets_sha256': sha(directory / 'targets.npz')})
    write(directory / 'solver.json', details)


def diagnose(output):
    output = Path(output)
    source = output / 'reconstructed_v3'
    cell = source / 'inputs/elongated/seed_51'
    destination = output / 'diagnosis'
    destination.mkdir(exist_ok=False)
    b = load_boundary(cell)
    data = np.load(cell / 'input.npz', allow_pickle=False)
    domain = navigation_domain(data['room'], .55, Polygon(b.crowd), .55)
    plan_dir = source / 'targets/elongated/seed_51'
    plans = {name: load_plan(plan_dir / name) for name in
             ('equal_arc', 'projected_mass', 'pairwise_euclidean')}
    common = Point(plans['equal_arc'].positions[0])
    original = unary_union([Point(p).buffer(float(r), quad_segs=32) for p, r in zip(data['people'], data['radii'])])
    dilation = original.buffer(.30, quad_segs=32)
    closed = dilation.buffer(-.30, quad_segs=32)
    occupied = unary_union([original, closed])
    raw_offset = orient(occupied.buffer(1.3, quad_segs=32), sign=1)
    sampled = LineString(np.vstack([b.deployment, b.deployment[0]]))
    simplified = domain.physical_obstacle.simplify(.10, preserve_topology=True)
    geometries = {'01_observed_disks': original, '02_closing_dilation': dilation,
        '03_closing_erosion': closed, '04_closed_union_original': occupied,
        '05_controller_crowd_exterior': Polygon(b.crowd), '06_raw_offset_1p3': raw_offset,
        '07_sampled_deployment': sampled, '08_inset_room': domain.room_poly,
        '09_physical_mitre_0p55': domain.physical_obstacle, '10_simplify_0p10': simplified,
        '11_conservative_mitre_0p11': domain.conservative_obstacle,
        '12_navigation_free': domain.free}
    stages = []
    for name, geometry in geometries.items():
        payload = geometry.wkb
        (destination / (name + '.wkb')).write_bytes(payload)
        write(destination / (name + '.geojson'), mapping(geometry))
        distance = float(geometry.boundary.distance(common)) if geometry.geom_type in ('Polygon', 'MultiPolygon') else float(geometry.distance(common))
        stages.append({'name': name, 'wkb_sha256': hashlib.sha256(payload).hexdigest(),
            'type': geometry.geom_type, 'area_m2': geometry.area, 'length_m': geometry.length,
            'covers_common_target': bool(geometry.covers(common)),
            'boundary_distance_m': distance,
            'signed_outside_clearance_m': (-distance if geometry.covers(common) else distance)
                if geometry.geom_type in ('Polygon', 'MultiPolygon') else None})
    relation = curve_domain_relation(b, domain)
    write(destination / 'curve_domain.json', relation)
    method_records = {}
    for name, plan in plans.items():
        report = target_domain_check(domain, plan.positions, plan.arc_positions)
        report.update(target_array_sha256=array_hash(targets=plan.positions, arc_positions=plan.arc_positions),
            target_file_sha256=sha(plan_dir / name / 'targets.npz'),
            max_interpolation_error_m=float(np.max(np.abs(plan.positions - _interpolate(b, plan.arc_positions)))),
            max_distance_to_original_polyline_m=max(float(sampled.distance(Point(t))) for t in plan.positions),
            containing_illegal_intervals=[{'target': record['target'], 'interval_indices': [i for i, span in enumerate(relation['intervals'])
                if span['start_arc_m'] <= record['arc_m'] <= span['end_arc_m']]}
                for record in report['targets'] if not record['accepted']])
        with np.load(source / 'baseline/elongated/seed_51' / name / 'trajectory.npz', allow_pickle=False) as trajectory:
            report['navigation_read_exact_targets'] = bool(np.array_equal(trajectory['targets'], plan.positions))
            report['navigation_read_exact_crowd'] = bool(np.array_equal(trajectory['boundary_crowd'], b.crowd))
        method_records[name] = report
    vertices = np.asarray(simplified.exterior.coords[:-1])
    v = int(np.argmin(np.linalg.norm(vertices - np.asarray(common.coords[0]), axis=1)))
    triangle = vertices[[(v-1) % len(vertices), v, (v+1) % len(vertices)]]
    u, w = triangle[0] - triangle[1], triangle[2] - triangle[1]
    angle = float(np.arccos(np.clip(np.dot(u, w) / np.linalg.norm(u) / np.linalg.norm(w), -1, 1)))
    no_simplify = domain.physical_obstacle.buffer(.11, join_style='mitre')
    def signed_obstacle(geometry):
        return float(geometry.boundary.distance(common)) * (-1 if geometry.covers(common) else 1)
    evidence = {'schema_version': 1, 'data_origin': 'reconstructed_from_frozen_protocol_not_original_raw_data',
        'input_array_sha256': array_hash(**{key: data[key] for key in ('people', 'radii', 'demand', 'guides', 'room')}),
        'input_file_sha256': sha(cell / 'input.npz'), 'boundary_file_sha256': sha(cell / 'boundary.npz'),
        'boundary_array_sha256': array_hash(crowd=b.crowd, deployment=b.deployment, arc=b.arc, length=np.array(b.length)),
        'domain_sha256': domain.geometry_sha256,
        'common_target': list(common.coords[0]), 'stages': stages, 'methods': method_records,
        'parameters': {'room_m': data['room'].tolist(), 'person_radius_m': data['radii'].tolist(),
            'closing_radius_m': .30, 'disk_quad_segs': 32, 'offset_m': 1.3,
            'sample_spacing_upper_m': .10, 'sample_count': len(b.deployment),
            'wall_and_crowd_buffer_m': .55, 'join_style': 'mitre', 'mitre_limit': 5.0,
            'simplify_m': .10, 'planning_margin_m': .11, 'tolerance_m': 1e-7},
        'representation': {'coordinates': 'room metres throughout, no transforms',
            'target_serialization': 'float64 compressed NPZ; JSON uses round-trip Python floats',
            'sampling_rule': 'equal raw-exterior arc samples; then piecewise linear interpolation on sampled chord arc lengths',
            'raw_offset_length_m': raw_offset.exterior.length, 'sampled_curve_length_m': b.length,
            'sampled_to_raw_hausdorff_m': sampled.hausdorff_distance(raw_offset.exterior),
            'frozen_crowd_exterior_matches_rebuilt': bool(np.array_equal(np.asarray(orient(occupied, sign=1).exterior.coords[:-1]), b.crowd))},
        'mitre_diagnosis': {'simplified_corner_vertices': triangle.tolist(), 'interior_angle_degrees': float(np.degrees(angle)),
            'theoretical_unclipped_mitre_extension_m': float(.11 / np.sin(angle / 2)),
            'mitre_limit_extension_m': .55,
            'without_simplification_signed_clearance_m': signed_obstacle(no_simplify),
            'round_second_buffer_signed_clearance_m': signed_obstacle(simplified.buffer(.11)),
            'physical_round_buffer_signed_clearance_m': signed_obstacle(Polygon(b.crowd).buffer(.55)),
            'counterfactual_geometries_are_diagnostic_only': True},
        'historical_comparison': {'full_precision_original_input_curve_target_hashes_available': False,
            'available_evidence': 'historical rounded coordinate and signed distance match; original raw hashes unavailable',
            'original_confirmation_unchanged': True},
        'provenance': provenance({}, None, 'fixed_n', 11)}
    write(destination / 'geometry_chain.json', evidence)
    print(json.dumps({'common_target': evidence['common_target'], 'domain_sha256': domain.geometry_sha256,
        'curve': {k: relation[k] for k in ('strict_illegal_length_m', 'intervals', 'max_penetration_lower_m', 'max_penetration_upper_m')},
        'mitre_diagnosis': evidence['mitre_diagnosis']}, ensure_ascii=False), flush=True)


def independent_geometry(boundary, original, candidate, domain, base):
    """Acceptance evidence from saved coordinates, independent of search flags."""
    points = candidate.positions
    line = Polygon(boundary.deployment).exterior
    arcs = np.sort(np.array([line.project(Point(p)) for p in points]))
    gaps = np.diff(np.r_[arcs, arcs[0] + line.length])
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    np.fill_diagonal(distances, np.inf)
    curve_error = max(line.distance(Point(p)) for p in points)
    signed = np.array([domain.signed_free_distance(p) for p in points])
    in_domain = all(domain.physical_free.covers(Point(p)) and value >= -1e-7 for p, value in zip(points, signed))
    old_gaps = periodic_gaps(np.sort(original.arc_positions), boundary.length)
    gap_pattern_error = min(float(np.max(np.abs(gaps - np.roll(old_gaps, i)))) for i in range(len(gaps)))
    return {'accepted': bool(in_domain and curve_error <= 1e-7
              and gaps.max() <= base['gap_limit_m'] + 1e-9
              and gaps.min() >= base['center_separation_m'] - 1e-12
              and distances.min() >= base['center_separation_m'] and gap_pattern_error <= 1e-7),
            'all_pairs': len(points) * (len(points) - 1) // 2,
            'curve_error_m': float(curve_error), 'min_domain_signed_m': float(signed.min()),
            'min_pair_m': float(distances.min()), 'min_arc_gap_m': float(gaps.min()),
            'max_arc_gap_m': float(gaps.max()), 'seam_gap_m': float(gaps[-1]),
            'cyclic_gap_pattern_error_m': gap_pattern_error, 'original_length_m': boundary.length,
            'evaluated_length_m': float(line.length), 'static_domain_sha256': domain.geometry_sha256}


def run_case(task):
    root_string, row, stage, config = task
    root = Path(root_string)
    source = root / config.get('input_subdirectory', 'reconstructed_v3')
    cell = source / row['cell']
    destination = root / stage / row['scene'] / f"seed_{row['seed']}" / row['method']
    if destination.exists():
        raise FileExistsError(f'Refusing to overwrite {destination}')
    _official_monitor()
    started = perf_counter()
    metadata = {'task_id': row['task_id'], 'data_origin': 'protocol_reconstruction',
                'input_sha256': row['input_sha256'], 'recomputed_target': False,
                'candidate_target_changed': False, 'reason': 'input_rejected_no_target',
                'candidate_source_sha256': provenance({}, None, 'fixed_n', row['count'])['source_sha256']}
    try:
        with threadpool_limits(limits=1):
            record = json.loads((cell / 'input_record.json').read_text(encoding='utf-8'))
            if sha(cell / 'input.npz') != record['input_file_sha256'] or sha(cell / 'input.yaml') != record['config_sha256']:
                raise RuntimeError('Frozen input/config changed')
            if record['status'] != 'ACCEPTED':
                result = rejected_result(destination, row, cell, record['status'], record['error'])
            else:
                boundary = load_boundary(cell)
                protocol = json.loads((source / 'protocol.json').read_text(encoding='utf-8'))
                spec, base = protocol['spec'], protocol['base_spec']
                original_dir = source / 'targets' / row['scene'] / f"seed_{row['seed']}" / row['method']
                if original_dir.exists():
                    original = load_plan(original_dir)
                    metadata['reason'] = 'reused_saved_reconstructed_original_targets'
                else:
                    before = perf_counter()
                    original, detail = plan_target(boundary, row['method'], row['count'], spec, base)
                    detail['planning_wall_s'] = perf_counter() - before
                    metadata.update(recomputed_target=True, reason='original_v3_targets_not_migrated_plan_with_unchanged_budgets')
                    if original is None:
                        raise _OriginalPlanExhausted(detail)
                    save_plan(original_dir, original, detail)
                metadata['original_targets_array_sha256'] = array_hash(targets=original.positions, arc_positions=original.arc_positions)
                data = np.load(cell / 'input.npz', allow_pickle=False)
                domain = navigation_domain(data['room'], base['wall_distance_m'], Polygon(boundary.crowd), base['wall_distance_m'])
                candidate, details = admissible_phase(boundary, original, domain,
                    np.ones(len(boundary.deployment)), phase_bins=config['phase_bins'],
                    gap_limit=base['gap_limit_m'], separation=base['center_separation_m'],
                    quadrature_samples=base['quadrature_samples'])
                if candidate is None:
                    result = rejected_result(destination, row, cell, 'PLAN_SEARCH_EXHAUSTED',
                        'PLAN_SEARCH_EXHAUSTED: fixed common-phase grid exhausted; infeasibility not proven')
                    write(destination / 'phase_search.json', details)
                else:
                    independent = independent_geometry(boundary, original, candidate, domain, base)
                    if not independent['accepted']:
                        raise RuntimeError('Phase candidate failed independent saved-coordinate geometry acceptance')
                    metadata.update(candidate_target_changed=details['changed'],
                        algorithm=candidate.method,
                        candidate_targets_array_sha256=array_hash(targets=candidate.positions, arc_positions=candidate.arc_positions))
                    candidate_dir = root / 'candidate_targets' / stage / row['scene'] / f"seed_{row['seed']}" / row['method']
                    save_plan(candidate_dir, candidate, details)
                    result = experiment(cell / 'input.yaml', destination, plots=False, protocol='fixed_n',
                        fixed_n=row['count'], prescribed_boundary=boundary, prescribed_plan=candidate,
                        boundary_source='estimated_disk_union', method_label=row['method'])
                    offline = reevaluate_result(destination)
                    with np.load(destination / 'trajectory.npz', allow_pickle=False) as actual:
                        metadata['exact_input_and_targets'] = bool(
                            np.array_equal(actual['positions'][0], data['guides'])
                            and np.array_equal(actual['crowd_positions'], data['people'])
                            and np.array_equal(actual['crowd_radii'], data['radii'])
                            and np.array_equal(actual['crowd_demand'], data['demand'])
                            and np.array_equal(actual['room'], data['room'])
                            and np.array_equal(actual['targets'], candidate.positions))
                    metadata['offline_consistent'] = bool(offline['criteria_consistent'])
                    write(destination / 'independent_geometry.json', independent)
                    write(destination / 'phase_search.json', details)
            offline = reevaluate_result(destination)
            metadata['offline_consistent'] = bool(offline['criteria_consistent'])
    except _OriginalPlanExhausted as error:
        result = rejected_result(destination, row, cell, 'PLAN_SEARCH_EXHAUSTED', str(error))
        write(destination / 'original_solver.json', error.details)
        metadata['offline_consistent'] = bool(reevaluate_result(destination)['criteria_consistent'])
    except Exception:
        destination.mkdir(parents=True, exist_ok=True)
        _atomic_text(destination / 'runner_error.txt', traceback.format_exc())
        if not (destination / 'trajectory.npz').exists():
            # Same failure-artifact schema, retaining the error in accounting.
            temporary = destination / 'error_artifacts'
            result = rejected_result(temporary, row, cell, 'EXPERIMENT_ERROR', traceback.format_exc())
            for file in temporary.iterdir():
                file.replace(destination / file.name)
            temporary.rmdir()
        result = {**row, 'termination_status': 'EXPERIMENT_ERROR', 'success': False}
        write(destination / 'metrics.json', result)
        write(destination / 'state.json', {'status': 'error', 'success': False})
    metadata['wall_s'] = perf_counter() - started
    write(destination / 'target_provenance.json', metadata)
    return {**row, **metadata, **{key: result.get(key) for key in
        ('termination_status', 'success', 'simulation_time', 'replan_count', 'minimum_net_clearances')}}


def run(output, stage, workers):
    root = Path(output)
    config = yaml.safe_load(Path(SPEC).read_text(encoding='utf-8'))
    manifest = json.loads((root / 'reconstructed_v3/manifest.json').read_text(encoding='utf-8'))
    rows = manifest['tasks']
    if stage == 'seed51':
        rows = [row for row in rows if row['scene'] == 'elongated' and row['seed'] == 51]
        if workers != 1:
            raise ValueError('seed51 must run serially')
    elif stage == 'small':
        rows = [row for row in rows if [row['scene'], row['seed']] in config['small_cases']]
    elif stage not in ('regression', 'regression_final'):
        raise ValueError('unknown development stage')
    if (root / (stage + '_freeze.json')).exists():
        raise FileExistsError('A stage is immutable; choose a new output, never overwrite it')
    os.environ.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                      NUMEXPR_NUM_THREADS='1', UDC_PRIORITY_STALL_COUNTERFACTUAL='0')
    write(root / (stage + '_freeze.json'), {'stage': stage, 'config': config,
        'config_sha256': sha(SPEC), 'manifest_sha256': sha(root / 'reconstructed_v3/manifest.json'),
        'workers': workers, 'numerical_threads_per_worker': 1, 'provenance': provenance({}, None, 'fixed_n', None),
        'task_ids': [row['task_id'] for row in rows]})
    started = perf_counter()
    tasks = [(str(root), row, stage, config) for row in rows]
    results = []
    if workers == 1:
        for task in tasks:
            result = run_case(task)
            results.append(result)
            print(json.dumps({'task': result['task_id'], 'status': result['termination_status']}), flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_case, task) for task in tasks]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(json.dumps({'task': result['task_id'], 'status': result['termination_status']}), flush=True)
    summary = {'stage': stage, 'tasks_expected': len(rows), 'tasks_completed': len(results),
        'wall_s': perf_counter() - started,
        'outcomes': dict(Counter(row['termination_status'] for row in results)),
        'offline_inconsistent': sum(row.get('offline_consistent') is not True for row in results),
        'input_or_target_mismatches': sum(row.get('exact_input_and_targets') is False for row in results),
        'postprocessed_targets': sum(row['candidate_target_changed'] for row in results),
        'rows': sorted(results, key=lambda row: row['task_id'])}
    write(root / (stage + '_summary.json'), summary)
    print(json.dumps({key: value for key, value in summary.items() if key != 'rows'}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=DEFAULT)
    parser.add_argument('--stage', choices=['diagnose', 'original', 'seed51', 'small', 'regression', 'regression_final'], required=True)
    parser.add_argument('--workers', type=int, default=1)
    args = parser.parse_args()
    if args.stage == 'diagnose':
        diagnose(args.output)
    elif args.stage == 'original':
        original_rejection(args.output)
    else:
        if not 1 <= args.workers <= 6:
            raise ValueError('This development protocol caps episode workers at six')
        run(args.output, args.stage, args.workers)


def original_rejection(output):
    """Reclassify the three fixed original seed51 targets, with no repair."""
    root = Path(output)
    source = root / 'reconstructed_v3'
    cell = source / 'inputs/elongated/seed_51'
    boundary = load_boundary(cell)
    results = []
    with threadpool_limits(limits=1):
        for method in ('equal_arc', 'projected_mass', 'pairwise_euclidean'):
            plan = load_plan(source / 'targets/elongated/seed_51' / method)
            destination = root / 'original_rejection' / method
            result = experiment(cell / 'input.yaml', destination, plots=False, protocol='fixed_n',
                fixed_n=11, prescribed_boundary=boundary, prescribed_plan=plan,
                boundary_source='estimated_disk_union', method_label=method)
            offline = reevaluate_result(destination)
            results.append({'method': method, 'status': result['termination_status'],
                'success': result['success'], 'steps': result['steps'],
                'offline_consistent': offline['criteria_consistent']})
    write(root / 'original_rejection_summary.json', {'rows': results,
        'new_successes': sum(row['success'] for row in results),
        'outcomes': dict(Counter(row['status'] for row in results))})
    print(json.dumps(results), flush=True)


if __name__ == '__main__':
    main()
