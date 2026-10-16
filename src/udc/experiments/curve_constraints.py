"""Forty-eight prescribed-curve plans plus a small frozen demand supplement.

The curves come from the existing prescribed catalog. This runner does not
estimate a boundary and does not rewrite the earlier 300-task diagnostic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter, process_time

import numpy as np
import yaml
from shapely.geometry import Point, Polygon

from udc.boundary.prescribed import build_prescribed_curve
from udc.deployment.constrained import constant_quadrature, optimize_pairwise, optimize_phase, planning_outcome
from udc.deployment.geometry import count_rule, pair_geometry, tier_count
from udc.deployment.planners import coverage_statistics, plan_coverage
from udc.deployment.risk_aware import plan_projected_mass
from udc.experiments.curve_diagnostics import _jsonable, _write
from udc.experiments.provenance import provenance


PATHS = ('equal_arc', 'projected_mass', 'projected_mass_phase', 'pairwise_euclidean')


def load_spec(path):
    path = Path(path)
    spec = yaml.safe_load(path.read_text(encoding='utf-8'))
    if spec.get('protocol') != 'curve_constraint_diagnostic':
        raise ValueError('Unsupported constraint-diagnostic spec')
    if list(spec['paths']) != list(PATHS):
        raise ValueError(f'Paths must be {list(PATHS)}')
    if len(spec['units']) != 12:
        raise ValueError('The constant-density study is 12 units')
    if int(spec['solver_maxiter']) != 40 or float(spec['per_start_timeout_s']) != 20:
        raise ValueError('Solver budget is frozen at 40 iterations and 20 s per start')
    spec['_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    spec['_path'] = str(path)
    return spec


def _legacy(boundary, method, count, phases, iterations, gap, separation, quadrature, mass):
    attempts = []
    chosen = None
    for phase in phases:
        started = perf_counter()
        cpu = process_time()
        if method == 'projected_mass':
            plan = plan_projected_mass(
                boundary, None, None, count, None, gap, separation, float(phase),
                len(quadrature), iterations, density_override=np.ones(len(boundary.deployment)),
            )
        else:
            plan = plan_coverage(
                boundary, None, None, count, None, method, float(phase),
                len(quadrature), iterations, density_override=np.ones(len(boundary.deployment)),
            )
        elapsed = perf_counter() - started
        geometry = pair_geometry(boundary, plan.arc_positions, gap, separation)
        cost = coverage_statistics(quadrature, mass, plan.arc_positions, boundary.length, np.inf)
        record = _geometry_record(geometry, separation)
        record.update({
            'start': f'phase_{float(phase):.2f}',
            'solver_status': plan.exit_reason,
            'solver_success': bool(geometry['accepted_by_controller_rule']),
            'solver_iterations': int(plan.iterations),
            'solver_seconds': elapsed,
            'cpu_seconds': process_time() - cpu,
            'cost_before': cost['weighted_cost'],
            'cost_after': cost['weighted_cost'],
            'accepted': bool(geometry['arc_box_ok'] and geometry['euclidean_ok']),
            'controller_rule_accepted': bool(geometry['accepted_by_controller_rule']),
            'arc_positions': np.asarray(plan.arc_positions, dtype=float).tolist(),
        })
        attempts.append(record)
        if chosen is None and record['controller_rule_accepted']:
            chosen = (plan, record)
            break
    accepted = chosen is not None and chosen[1]['accepted']
    return {
        'method': method,
        'objective': 'demand-weighted mean squared arc distance; constant curve density',
        'description': 'existing planner and its discrete phase list; first controller-rule hit stops the list',
        'global_optimum': False,
        'accepted': bool(accepted),
        'outcome': planning_outcome(accepted, None),
        'best_cost': None if not accepted else chosen[1]['cost_after'],
        'selected_start': None if chosen is None else chosen[1]['start'],
        'attempts': attempts,
        'arc_positions': None if not accepted else chosen[1]['arc_positions'],
        'positions': None if not accepted else np.asarray(chosen[0].positions, dtype=float).tolist(),
        'unevaluated': {
            'people_clearance': False, 'wall_clearance': False, 'path': False,
            'execution': False, 'safety_filter': False,
        },
    }


def _geometry_record(geometry, separation):
    residuals = []
    for item in geometry['pairwise']:
        distance = item['euclidean_m']
        residuals.append({
            'i': item['i'], 'j': item['j'], 'adjacent': item['adjacent'],
            'euclidean_m': distance,
            'violation_m': None if distance is None else float(max(0.0, separation - distance)),
        })
    return {
        'min_arc_gap_m': geometry['min_arc_gap_m'],
        'max_arc_gap_m': geometry['max_arc_gap_m'],
        'seam_arc_gap_m': geometry['seam_arc_gap_m'],
        'sum_arc_gap_error_m': geometry['sum_arc_gap_error_m'],
        'min_euclidean_m': geometry['min_euclidean_m'],
        'min_adjacent_euclidean_m': geometry['min_adjacent_euclidean_m'],
        'min_nonadjacent_euclidean_m': geometry['min_nonadjacent_euclidean_m'],
        'worst_pair': geometry['worst_pair'],
        'arc_box_ok': geometry['arc_box_ok'],
        'euclidean_ok': geometry['euclidean_ok'],
        'pair_count': len(residuals),
        'all_pairs_checked': True,
        'max_euclidean_violation_m': float(max((item['violation_m'] or 0.0) for item in residuals)),
        'pair_residuals_m': residuals,
        'gap_residuals_m': [item['arc_gap_m'] for item in geometry['pairwise'] if item['adjacent']],
    }


def _base_plans(boundary, count, gap, separation, quadrature, mass, iterations):
    equal = plan_coverage(
        boundary, None, None, count, None, 'equal_arc', 0.0, len(quadrature), iterations,
        density_override=np.ones(len(boundary.deployment)),
    )
    projected = plan_projected_mass(
        boundary, None, None, count, None, gap, separation, 0.0, len(quadrature), iterations,
        density_override=np.ones(len(boundary.deployment)),
    )
    return equal.arc_positions, projected.arc_positions


def _drop_plan(bundle):
    plan = bundle.pop('plan', None)
    if plan is not None:
        bundle['arc_positions'] = np.asarray(plan.arc_positions, dtype=float).tolist()
        bundle['positions'] = np.asarray(plan.positions, dtype=float).tolist()
    else:
        bundle['arc_positions'] = None
        bundle['positions'] = None
    return bundle


def run_task(task):
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        started = perf_counter()
        cpu = process_time()
        built = build_prescribed_curve(task['family'], task['intensity'], task['seed'])
        boundary = built['boundary']
        gap = float(task['gap_limit_m'])
        separation = float(task['center_separation_m'])
        rule = count_rule(boundary.length, gap, separation)
        count, certificate = tier_count(rule, task['tier'])
        samples = int(task['quadrature_samples'])
        if task['density'] == 'constant':
            quadrature, mass, _ = constant_quadrature(boundary, samples)
            density_name = 'constant curve density'
        else:
            from udc.deployment.planners import demand_profile, quadrature_mass
            rho = demand_profile(
                boundary, np.asarray(task['people'], dtype=float),
                np.asarray(task['demand'], dtype=float), float(task['bandwidth']), 'mass_cvt',
            )
            quadrature, mass, _ = quadrature_mass(boundary, rho, samples)
            density_name = 'mass_cvt gaussian of the frozen interior samples'
        if certificate is not None:
            bundle = {
                'method': task['path'], 'accepted': False,
                'outcome': planning_outcome(False, certificate),
                'count_certificate': certificate, 'attempts': [], 'global_optimum': False,
            }
        elif task['path'] in {'equal_arc', 'projected_mass'}:
            bundle = _legacy(
                boundary, task['path'], count, task['legacy_phases'],
                int(task['legacy_max_iterations']), gap, separation, quadrature, mass,
            )
        else:
            equal_sites, projected_sites = _base_plans(
                boundary, count, gap, separation, quadrature, mass, int(task['legacy_max_iterations']),
            )
            phases = [float(fraction) * boundary.length for fraction in task['phase_fractions']]
            if task['path'] == 'projected_mass_phase':
                bundle = optimize_phase(
                    boundary, projected_sites, quadrature, mass, phases, gap, separation,
                    int(task['solver_maxiter']), float(task['per_start_timeout_s']),
                )
            else:
                starts = [('projected_mass_phase0', projected_sites), ('equal_arc_phase0', equal_sites)]
                for fraction in task['phase_fractions']:
                    if float(fraction) == 0.0:
                        continue
                    starts.append((
                        f'projected_mass_rotate_{float(fraction):.2f}L',
                        (np.asarray(projected_sites) + float(fraction) * boundary.length) % boundary.length,
                    ))
                bundle = optimize_pairwise(
                    boundary, starts, quadrature, mass, gap, separation,
                    int(task['solver_maxiter']), float(task['per_start_timeout_s']),
                )
            bundle = _drop_plan(bundle)
        bundle.update({
            'family': task['family'], 'intensity': task['intensity'], 'tier': task['tier'],
            'seed': int(task['seed']), 'count': int(count), 'density': density_name,
            'length_m': float(boundary.length), 'curve_valid': bool(built['report']['valid']),
            'count_certificate': certificate,
            'wall_seconds': perf_counter() - started,
            'cpu_seconds': process_time() - cpu,
            'planning_gap_limit_m': gap,
            'acceptance_gap_limit_m': float(task['acceptance_gap_limit_m']),
            'center_separation_m': separation,
            'boundary_source': 'prescribed_diagnostic',
            'global_optimum': False,
        })
        if bundle.get('outcome') is None:
            bundle['outcome'] = planning_outcome(bool(bundle.get('accepted')), certificate)
        return bundle


def _tasks_for(spec, units, density, people=None, demand=None, bandwidth=None):
    tasks = []
    for unit in units:
        for path in (spec['paths'] if density == 'constant' else spec['demand_supplement']['paths']):
            tasks.append({
                **unit, 'path': path, 'density': density,
                'gap_limit_m': spec['gap_limit_m'],
                'acceptance_gap_limit_m': spec['acceptance_gap_limit_m'],
                'center_separation_m': spec['center_separation_m'],
                'quadrature_samples': spec['quadrature_samples'],
                'legacy_phases': spec['legacy_phases'],
                'legacy_max_iterations': spec['legacy_max_iterations'],
                'phase_fractions': spec['phase_fractions'],
                'solver_maxiter': spec['solver_maxiter'],
                'per_start_timeout_s': spec['per_start_timeout_s'],
                'people': people, 'demand': demand, 'bandwidth': bandwidth,
            })
    return tasks


def _interior_demand(boundary, inset, limit, background, hotspot):
    polygon = Polygon(boundary.deployment).buffer(-float(inset))
    if polygon.is_empty:
        return None
    region = max(polygon.geoms, key=lambda part: part.area) if polygon.geom_type == 'MultiPolygon' else polygon
    minx, miny, maxx, maxy = region.bounds
    chosen = []
    x = minx
    while x <= maxx + 1e-9 and len(chosen) < int(limit):
        y = miny
        while y <= maxy + 1e-9 and len(chosen) < int(limit):
            if region.covers(Point(x, y)):
                chosen.append([float(x), float(y)])
            y += 0.8
        x += 0.8
    if len(chosen) < 3:
        return None
    points = np.asarray(chosen, dtype=float)
    centre = np.asarray(boundary.deployment).mean(axis=0)
    hotspot_index = int(np.argmin(np.linalg.norm(points - centre, axis=1)))
    weights = np.full(len(points), float(background))
    weights[hotspot_index] = float(hotspot)
    return points, weights, hotspot_index


def _summarize(rows):
    counts = {}
    by_path = {}
    for row in rows:
        counts[row['outcome']] = counts.get(row['outcome'], 0) + 1
        by_path.setdefault(row['method'], {})
        by_path[row['method']][row['outcome']] = by_path[row['method']].get(row['outcome'], 0) + 1
    return {'tasks': len(rows), 'outcomes': counts, 'by_method': by_path}


def _compact(row):
    """Summary row. Full pair residuals stay in the task file."""
    kept = {key: row.get(key) for key in (
        'family', 'intensity', 'tier', 'seed', 'method', 'count', 'density', 'length_m',
        'outcome', 'accepted', 'best_cost', 'selected_start', 'count_certificate',
        'wall_seconds', 'cpu_seconds', 'global_optimum', 'curve_valid', 'boundary_source',
        'planning_gap_limit_m', 'acceptance_gap_limit_m', 'center_separation_m',
    )}
    focus = None
    for attempt in row.get('attempts') or []:
        if attempt.get('accepted') and attempt.get('start') == row.get('selected_start'):
            focus = attempt
            break
    if focus is None:
        accepted = [item for item in (row.get('attempts') or []) if item.get('accepted')]
        focus = accepted[0] if accepted else ((row.get('attempts') or [None])[-1])
    if focus is not None:
        kept.update({
            'min_euclidean_m': focus.get('min_euclidean_m'),
            'min_nonadjacent_euclidean_m': focus.get('min_nonadjacent_euclidean_m'),
            'max_arc_gap_m': focus.get('max_arc_gap_m'),
            'min_arc_gap_m': focus.get('min_arc_gap_m'),
            'worst_pair': focus.get('worst_pair'),
            'max_euclidean_violation_m': focus.get('max_euclidean_violation_m'),
            'solver_status': focus.get('solver_status'),
            'solver_success': focus.get('solver_success'),
            'solver_iterations': focus.get('solver_iterations'),
            'cost_before': focus.get('cost_before'),
            'cost_after': focus.get('cost_after'),
            'all_pairs_checked': focus.get('all_pairs_checked'),
        })
    return kept


def _run_all(tasks, workers, output, folder):
    destination = Path(output) / folder
    destination.mkdir(parents=True, exist_ok=True)
    if workers < 1:
        raise ValueError('workers must be positive')
    if workers == 1 or len(tasks) == 1:
        rows = [run_task(task) for task in tasks]
    else:
        rows = []
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_task, task) for task in tasks]
            for future in as_completed(futures):
                rows.append(future.result())
    rows.sort(key=lambda row: (row['family'], row['intensity'], row['tier'], row['seed'], row['method']))
    for row in rows:
        name = f"{row['family']}__{row['intensity']}__{row['tier']}__seed_{row['seed']}__{row['method']}.json"
        _write(destination / name, row)
    summary = _summarize(rows)
    summary['rows'] = [_compact(row) for row in rows]
    _write(destination / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description='Pairwise curve-constraint diagnostic')
    parser.add_argument('--spec', default='experiments/step1_curve_constraints.yaml')
    parser.add_argument('--output', default='runs/step1_curve_constraints')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args(argv)
    spec = load_spec(args.spec)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    source = provenance({'spec': spec['_sha256']}, None, 'curve_constraint_diagnostic', None)
    _write(output / 'selection.json', {
        'spec_sha256': spec['_sha256'],
        'units': spec['units'],
        'paths': spec['paths'],
        'solver_maxiter': spec['solver_maxiter'],
        'per_start_timeout_s': spec['per_start_timeout_s'],
        'phase_fractions': spec['phase_fractions'],
        'legacy_phases': spec['legacy_phases'],
        'notes': spec['notes'],
        'provenance': source,
        'formal_validation': False,
    })
    constant = _run_all(_tasks_for(spec, spec['units'], 'constant'), args.workers, output, 'constant_density')
    supplement = spec['demand_supplement']
    demand_tasks = []
    placements = []
    for case in supplement['cases']:
        built = build_prescribed_curve(case['family'], case['intensity'], case['seed'])
        placed = _interior_demand(
            built['boundary'], supplement['inset_m'], supplement['maximum_people'],
            supplement['background_demand'], supplement['hotspot_demand'],
        )
        if placed is None:
            placements.append({**case, 'skipped': 'interior_samples_unavailable'})
            continue
        points, weights, hotspot_index = placed
        _write(output / 'demand_supplement' / f"{case['family']}__{case['intensity']}__people.json", {
            'people': points.tolist(), 'demand': weights.tolist(),
            'hotspot_index': hotspot_index, 'bandwidth': supplement['bandwidth'],
            'boundary_source': 'prescribed_diagnostic',
        })
        demand_tasks.extend(_tasks_for(
            spec, [case], 'gaussian', points.tolist(), weights.tolist(), supplement['bandwidth'],
        ))
        placements.append({**case, 'people': len(points), 'hotspot_index': hotspot_index})
    demand = {'tasks': 0, 'outcomes': {}, 'placements': placements}
    if demand_tasks:
        demand.update(_run_all(demand_tasks, args.workers, output, 'demand_supplement'))
        demand['placements'] = placements
    print(json.dumps({
        'constant_density': constant['outcomes'],
        'demand_supplement': demand.get('outcomes'),
        'placements': placements,
    }, indent=2))


if __name__ == '__main__':
    main()
