"""Expand the Step 1 factor specification into paired fixed-N tasks."""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import yaml
from shapely.geometry import Polygon

from udc.evaluation.acceptance import classify_outcome
from udc.experiments.config import METHODS, load_config
from udc.experiments.runner import experiment

ROOT = Path.cwd()
RESERVED_VALIDATION = range(5000, 5030)
RESOURCE_SCALE = {'sufficient': None, 'moderate': 0.85, 'severe': 0.45}


def load_spec(path):
    path = Path(path)
    spec = yaml.safe_load(path.read_text(encoding='utf-8'))
    required = {'schema_version', 'protocol', 'methods', 'development_seeds', 'shared', 'factors'}
    if not isinstance(spec, dict) or not required <= set(spec):
        raise ValueError(f'Comparison spec requires {sorted(required)}')
    if spec['protocol'] != 'fixed_n':
        raise ValueError('Fair comparison uses the fixed_n protocol')
    if set(spec['methods']) != METHODS:
        raise ValueError('Comparison must list all four deployment methods')
    seeds = spec['development_seeds']
    if seeds != [0, 1, 2, 3, 4]:
        raise ValueError('Development comparison uses exactly five seeds: 0, 1, 2, 3, 4')
    if any(seed in RESERVED_VALIDATION for seed in seeds):
        raise ValueError('Reserved validation seeds must not be listed as development seeds')
    return spec


def _ellipse(cx, cy, rx, ry, sides=16):
    theta = np.linspace(0.0, 2.0 * np.pi, sides, endpoint=False)
    return np.column_stack([cx + rx * np.cos(theta), cy + ry * np.sin(theta)])


def _nonconvex(cx, cy, rx, ry):
    x0, x1 = cx - rx, cx + rx
    y0, y1 = cy - ry, cy + ry
    mouth_y0 = y0 + 0.32 * (y1 - y0)
    mouth_y1 = y1 - 0.32 * (y1 - y0)
    notch = x1 - 0.42 * (x1 - x0)
    return np.array([
        [x0, y0], [x1, y0], [x1, mouth_y0], [notch, mouth_y0],
        [notch, mouth_y1], [x1, mouth_y1], [x1, y1], [x0, y1],
    ], dtype=float)


def _spawn(scene, shape, count, spacing):
    width, height = float(scene['width']), float(scene['height'])
    target = spacing * math.sqrt(count / math.pi) * 1.45
    stretch = {'circle': (1.0, 1.0), 'ellipse': (1.45, 0.72), 'nonconvex': (1.15, 1.15)}[shape]
    margin = 2.5
    rx = target * stretch[0]
    ry = target * stretch[1]
    scale = min(1.0, (width / 2 - margin) / rx, (height / 2 - margin) / ry)
    rx *= scale
    ry *= scale
    center = (width / 2, height / 2)
    if shape == 'nonconvex':
        vertices = _nonconvex(*center, rx, ry)
    else:
        vertices = _ellipse(*center, rx, ry)
    polygon = Polygon(vertices)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError(f'Invalid spawn polygon for {shape}')
    return vertices, polygon, min(rx, ry)


def _guide_count(polygon, controller, resource):
    offset = float(controller['offset'])
    planned = float(controller['max_gap_limit']) - 2 * float(controller['arc_tolerance'])
    if planned <= 0:
        raise ValueError('max_gap_limit - 2*arc_tolerance must be positive')
    lower = max(3, math.ceil(polygon.buffer(offset).exterior.length / planned))
    if resource == 'sufficient':
        return lower + 4
    count = max(3, int(round(RESOURCE_SCALE[resource] * lower)))
    return count


def materialize(spec, environment, shape, size, demand, resource, method, seed):
    """Build one strict Step 1 config. Method is the only planner difference."""
    factors = spec['factors']
    scene = dict(factors['environment'][environment])
    scene['openings'] = []
    shared = spec['shared']
    count = int(factors['size'][size]['count'])
    vertices, polygon, minor = _spawn(scene, shape, count, float(shared['spacing']))
    vertices = np.asarray(vertices, dtype=float).round(6)
    polygon = Polygon(vertices)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError(f'Rounded spawn polygon is invalid for {shape}')
    demand_spec = factors['demand'][demand]
    guides = int(_guide_count(polygon, shared['controller'], resource))
    crowd = {
        'count': count,
        'spawn_vertices': vertices.tolist(),
        'spacing': float(shared['spacing']),
        'radius': dict(shared['radius']),
        'demand': {key: demand_spec[key] for key in ('mean', 'std', 'min', 'max')},
    }
    if demand == 'hotspot':
        center = polygon.centroid
        crowd['demand_region'] = {
            'center': [round(float(center.x), 6), round(float(center.y), 6)],
            'radius': round(float(demand_spec['radius_scale']) * minor, 6),
            'multiplier': float(demand_spec['multiplier']),
        }
    controller = dict(shared['controller'])
    controller['method'] = method
    return {
        'step': 1,
        'scene': scene,
        'crowd': crowd,
        'guides': {**shared['guides'], 'count': guides},
        'controller': controller,
        'simulation': {**shared['simulation'], 'seed': int(seed)},
    }, guides


def iter_cells(spec):
    factors = spec['factors']
    for environment in factors['environment']:
        for shape in factors['shape']:
            for size in factors['size']:
                for demand in factors['demand']:
                    for resource in factors['resource']:
                        yield environment, shape, size, demand, resource


def build_tasks(spec, output, seeds=None, methods=None, selection=None):
    seeds = list(spec['development_seeds'] if seeds is None else seeds)
    if any(seed in RESERVED_VALIDATION for seed in seeds):
        raise ValueError('validation seeds 5000-5029 are reserved and are not executed here')
    if any(type(seed) is not int or seed < 0 for seed in seeds) or len(seeds) != len(set(seeds)):
        raise ValueError('Seeds must be distinct nonnegative integers')
    methods = list(spec['methods'] if methods is None else methods)
    unknown = set(methods) - METHODS
    if unknown:
        raise ValueError(f'Unknown methods: {sorted(unknown)}')
    selection = selection or {}
    tasks = []
    for environment, shape, size, demand, resource in iter_cells(spec):
        chosen = {
            'environment': environment, 'shape': shape, 'size': size,
            'demand': demand, 'resource': resource,
        }
        if any(selection.get(key) not in (None, value) for key, value in chosen.items()):
            continue
        reference, guides = materialize(
            spec, environment, shape, size, demand, resource, methods[0], seeds[0]
        )
        for seed in seeds:
            for method in methods:
                config, count = materialize(
                    spec, environment, shape, size, demand, resource, method, seed
                )
                if count != guides:
                    raise ValueError('Guide budget changed inside one comparison cell')
                name = '_'.join([environment, shape, size, demand, resource])
                tasks.append({
                    'cell': name,
                    'config': config,
                    'seed': seed,
                    'method': method,
                    'protocol': 'fixed_n',
                    'fixed_n': guides,
                    'output': str(Path(output) / name / method / f'seed_{seed}'),
                    'reference_without_method': {
                        key: reference[key] for key in reference if key != 'controller'
                    },
                })
    if not tasks:
        raise ValueError('Comparison selection matched no cells')
    return tasks


def _run_task(task):
    directory = Path(task['output'])
    directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory.parent) as temporary:
        path = Path(temporary) / 'case.yaml'
        path.write_text(yaml.safe_dump(task['config'], sort_keys=False), encoding='utf-8')
        load_config(path)
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1):
            metrics = experiment(
                path, directory, task['seed'], False, 'fixed_n', task['fixed_n'],
                False, task['method'],
            )
    return {
        'cell': task['cell'], 'seed': task['seed'], 'method': task['method'],
        'fixed_n': task['fixed_n'], 'output': task['output'],
        'success': metrics['success'],
        'termination_status': metrics['termination_status'],
        'failure_stage': metrics['failure_stage'],
        'failure_class': classify_outcome(metrics['termination_status'], metrics['failure_stage']),
        'failure_reason': metrics['failure_reason'],
        'active_guides': metrics['active_guides'],
        'simulation_time': metrics['simulation_time'],
        'actual_max_gap': metrics.get('actual_max_gap'),
        'mean_curve_distance': metrics.get('mean_curve_distance'),
        'path_length': metrics.get('path_length'),
        'resource_utilization': metrics.get('resource_utilization'),
        'minimum_net_clearances': metrics.get('minimum_net_clearances'),
        'task_sha256': metrics.get('task_sha256'),
    }


def run_tasks(tasks, workers='auto'):
    available = os.cpu_count() or 1
    requested = available if workers == 'auto' else int(workers)
    pool_size = max(1, min(requested, len(tasks)))
    rows = []
    with ProcessPoolExecutor(max_workers=pool_size) as pool:
        futures = [pool.submit(_run_task, task) for task in tasks]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (row['cell'], row['seed'], row['method'], row.get('fixed_n') or 0))
    return rows


def hotspot_tasks(output, config='configs/step1/scenarios/high_demand_side.yaml'):
    """Reproduce the existing high-demand case at 20 and 24 guides."""
    path = str(Path(config))
    loaded = load_config(path)
    tasks = []
    for fixed_n in (20, 24):
        if fixed_n > loaded['guides']['count']:
            raise ValueError('high_demand_side does not provide enough guides')
        for method in sorted(METHODS):
            tasks.append({
                'cell': f'high_demand_n{fixed_n}',
                'config_path': path,
                'seed': int(loaded['simulation']['seed']),
                'method': method,
                'fixed_n': fixed_n,
                'output': str(Path(output) / f'n{fixed_n}' / method),
            })
    return tasks


def _run_hotspot(task):
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        metrics = experiment(
            task['config_path'], task['output'], task['seed'], False,
            'fixed_n', task['fixed_n'], False, task['method'],
        )
    return {
        'fixed_n': task['fixed_n'], 'method': task['method'], 'seed': task['seed'],
        'success': metrics['success'],
        'termination_status': metrics['termination_status'],
        'failure_stage': metrics['failure_stage'],
        'failure_class': classify_outcome(metrics['termination_status'], metrics['failure_stage']),
        'failure_reason': metrics['failure_reason'],
        'active_guides': metrics['active_guides'],
        'simulation_time': metrics['simulation_time'],
        'actual_max_gap': metrics.get('actual_max_gap'),
        'mean_curve_distance': metrics.get('mean_curve_distance'),
        'path_length': metrics.get('path_length'),
        'resource_utilization': metrics.get('resource_utilization'),
        'minimum_net_clearances': metrics.get('minimum_net_clearances'),
        'replan_count': metrics.get('replan_count'),
        'task_sha256': metrics.get('task_sha256'),
        'output': task['output'],
    }


def run_hotspot(output, workers='auto'):
    tasks = hotspot_tasks(output)
    available = os.cpu_count() or 1
    requested = available if workers == 'auto' else int(workers)
    pool_size = max(1, min(requested, len(tasks)))
    rows = []
    with ProcessPoolExecutor(max_workers=pool_size) as pool:
        futures = [pool.submit(_run_hotspot, task) for task in tasks]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (row['fixed_n'], row['method']))
    report = {
        'scenario': 'configs/step1/scenarios/high_demand_side.yaml',
        'interpretation': (
            'The historical scenario draws demand independently for each person. '
            'It is not a spatial hotspot. fixed_n selects 20 or 24 of the 24 available guides. '
            'resource_shortfall, planning_failure and execution_failure are separate labels.'
        ),
        'runs': rows,
    }
    destination = Path(output) / 'diagnosis.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', default=str(ROOT / 'experiments' / 'step1_fair_comparison.yaml'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--seeds', type=int, nargs='+')
    parser.add_argument('--methods', nargs='+')
    parser.add_argument('--environment')
    parser.add_argument('--shape')
    parser.add_argument('--size')
    parser.add_argument('--demand')
    parser.add_argument('--resource')
    parser.add_argument('--workers', default='auto')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--hotspot', action='store_true')
    args = parser.parse_args()
    if args.hotspot:
        report = run_hotspot(args.output, args.workers)
        print(json.dumps(report, indent=2))
        return
    spec = load_spec(args.spec)
    selection = {
        'environment': args.environment, 'shape': args.shape, 'size': args.size,
        'demand': args.demand, 'resource': args.resource,
    }
    tasks = build_tasks(spec, args.output, args.seeds, args.methods, selection)
    if args.dry_run:
        print(json.dumps({
            'tasks': len(tasks),
            'cells': len({task['cell'] for task in tasks}),
            'methods': sorted({task['method'] for task in tasks}),
            'seeds': sorted({task['seed'] for task in tasks}),
        }, indent=2))
        return
    rows = run_tasks(tasks, args.workers)
    destination = Path(args.output) / 'comparison.json'
    destination.write_text(json.dumps(rows, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({
        'tasks': len(rows),
        'successful': sum(row['success'] for row in rows),
        'classes': {
            name: sum(row['failure_class'] == name for row in rows)
            for name in sorted({row['failure_class'] for row in rows})
        },
    }, indent=2))


if __name__ == '__main__':
    main()
