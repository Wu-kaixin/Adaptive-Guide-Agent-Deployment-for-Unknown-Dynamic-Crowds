"""Expand a Step 1 factor specification into paired fixed-N tasks."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import yaml
from shapely.geometry import Polygon

from udc.analysis.summary import summarize_comparison
from udc.evaluation.acceptance import classify_outcome
from udc.experiments.config import METHODS, load_config
from udc.experiments.runner import _atomic_text, experiment

ROOT = Path.cwd()
RESERVED_VALIDATION = range(5000, 5030)
# Legacy list form of the resource factor: fractions of the spawn-based bound.
RESOURCE_SCALE = {'sufficient': None, 'moderate': 0.85, 'severe': 0.45}
ROW_METRICS = (
    'actual_max_gap', 'planned_max_gap', 'target_min_euclidean_distance',
    'mean_curve_distance', 'path_length', 'resource_utilization',
    'minimum_net_clearances', 'replan_count', 'wall_time', 'curve_length',
    'planned_demand_weighted_cost', 'actual_demand_weighted_cost',
    'planned_uniform_cost', 'actual_uniform_cost',
    'planned_uncovered_demand_share', 'actual_uncovered_demand_share',
    'hotspot_arc_fraction', 'hotspot_demand_share', 'hotspot_people',
    'planned_hotspot_concentration', 'actual_hotspot_concentration',
    'planned_hotspot_mean_spacing', 'actual_hotspot_mean_spacing',
    'planned_hotspot_max_gap', 'actual_hotspot_max_gap',
    'planned_concentration', 'equal_arc_fallback', 'adapt_concentration', 'demand_kind',
    'demand_clipped_count', 'stage_diagnosis', 'feasibility', 'plan_found',
    'assignment_found', 'task_sha256', 'input_snapshot_sha256', 'commit',
    'source_sha256',
)


def load_spec(path):
    path = Path(path)
    spec = yaml.safe_load(path.read_text(encoding='utf-8'))
    required = {'schema_version', 'protocol', 'methods', 'development_seeds', 'shared', 'factors'}
    if not isinstance(spec, dict) or not required <= set(spec):
        raise ValueError(f'Comparison spec requires {sorted(required)}')
    if spec['protocol'] != 'fixed_n':
        raise ValueError('Fair comparison uses the fixed_n protocol')
    methods = spec['methods']
    if not methods or len(set(methods)) != len(methods) or not set(methods) <= METHODS:
        raise ValueError(f'Comparison methods must be distinct members of {sorted(METHODS)}')
    seeds = spec['development_seeds']
    if any(type(seed) is not int or seed < 0 for seed in seeds) or len(seeds) != len(set(seeds)):
        raise ValueError('Seeds must be distinct nonnegative integers')
    role = spec.get('seed_role', 'development')
    if role == 'formal':
        if seeds != list(range(5000, 5030)):
            raise ValueError('Formal comparison uses seeds 5000 through 5029 and no others')
    elif any(seed in RESERVED_VALIDATION for seed in seeds):
        raise ValueError('Reserved validation seeds must not be listed as development seeds')
    elif role == 'development':
        if seeds != [0, 1, 2, 3, 4]:
            raise ValueError('Development comparison uses exactly five seeds: 0, 1, 2, 3, 4')
    elif role == 'confirmation':
        if seeds != list(range(5, 15)):
            raise ValueError('Confirmation comparison uses seeds 5 through 14 and no others')
    else:
        raise ValueError(f'Unsupported seed_role: {role}')
    resource = spec['factors']['resource']
    if isinstance(resource, dict):
        shapes = set(spec['factors']['shape'])
        for name, item in resource.items():
            if not isinstance(item, dict):
                raise ValueError(f'resource.{name} must be a mapping')
            if 'count_by_shape' in item:
                counts = item['count_by_shape']
                if set(item) != {'count_by_shape'} or not isinstance(counts, dict) or set(counts) != shapes:
                    raise ValueError(f'resource.{name}.count_by_shape must name every shape')
                if any(type(value) is not int or value < 3 for value in counts.values()):
                    raise ValueError(f'resource.{name} counts must be integers >= 3')
            elif set(item) != {'relative'} or type(item['relative']) is not int:
                raise ValueError(f'resource.{name} must be {{relative: <int>}} offsets from the spawn-based bound')
    elif not isinstance(resource, list) or not set(resource) <= set(RESOURCE_SCALE):
        raise ValueError('resource must be a list of sufficient/moderate/severe or a dict of relative offsets')
    spec['_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    spec['_path'] = str(path)
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
    return vertices, polygon, rx, ry


def spawn_bound(polygon, controller):
    """Necessary count from the spawn polygon's offset perimeter (a budget, not a guarantee)."""
    offset = float(controller['offset'])
    planned = float(controller['max_gap_limit']) - 2 * float(controller['arc_tolerance'])
    if planned <= 0:
        raise ValueError('max_gap_limit - 2*arc_tolerance must be positive')
    return max(3, math.ceil(polygon.buffer(offset).exterior.length / planned))


def _guide_count(polygon, controller, resource, resource_spec, shape=None):
    lower = spawn_bound(polygon, controller)
    if isinstance(resource_spec, dict):
        item = resource_spec[resource]
        if 'count_by_shape' in item:
            if shape not in item['count_by_shape']:
                raise ValueError(f'No guide count for shape {shape}')
            return int(item['count_by_shape'][shape])
        return max(3, lower + int(item['relative']))
    if resource == 'sufficient':
        return lower + 4
    return max(3, int(round(RESOURCE_SCALE[resource] * lower)))


def materialize(spec, environment, shape, size, demand, resource, method, seed):
    """Build one strict Step 1 config. Method is the only planner difference."""
    factors = spec['factors']
    scene = dict(factors['environment'][environment])
    scene['openings'] = []
    shared = spec['shared']
    count = int(factors['size'][size]['count'])
    vertices, polygon, rx, ry = _spawn(scene, shape, count, float(shared['spacing']))
    vertices = np.asarray(vertices, dtype=float).round(6)
    polygon = Polygon(vertices)
    if not polygon.is_valid or polygon.area <= 0:
        raise ValueError(f'Rounded spawn polygon is invalid for {shape}')
    demand_spec = factors['demand'][demand]
    guides = int(_guide_count(polygon, shared['controller'], resource, factors['resource'], shape))
    crowd = {
        'count': count,
        'spawn_vertices': vertices.tolist(),
        'spacing': float(shared['spacing']),
        'radius': dict(shared['radius']),
        'demand': {key: demand_spec[key] for key in ('mean', 'std', 'min', 'max')},
    }
    if 'multiplier' in demand_spec:
        # Spatial hotspot: a disk fixed in scene coordinates. ``offset_scale``
        # moves its centre along +x by that fraction of the spawn half-width so
        # the hotspot touches one side of the crowd instead of its centre.
        centre = polygon.centroid
        shift = float(demand_spec.get('offset_scale', 0.0)) * rx
        crowd['demand_region'] = {
            'center': [round(float(centre.x) + shift, 6), round(float(centre.y), 6)],
            'radius': round(float(demand_spec['radius_scale']) * min(rx, ry), 6),
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
    role = spec.get('seed_role', 'development')
    if role == 'formal':
        if any(seed not in RESERVED_VALIDATION for seed in seeds):
            raise ValueError('Formal comparison executes only seeds 5000-5029')
    elif any(seed in RESERVED_VALIDATION for seed in seeds):
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
                    'cell': name, **chosen,
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


def row_from_metrics(task, metrics):
    row = {
        **{key: task[key] for key in ('cell', 'seed', 'method', 'fixed_n', 'output')},
        **{key: task.get(key) for key in ('environment', 'shape', 'size', 'demand', 'resource')},
        'success': metrics['success'],
        'termination_status': metrics['termination_status'],
        'failure_stage': metrics['failure_stage'],
        'failure_class': classify_outcome(metrics['termination_status'], metrics['failure_stage']),
        'failure_reason': metrics['failure_reason'],
        'active_guides': metrics['active_guides'],
        'simulation_time': metrics['simulation_time'],
    }
    for key in ROW_METRICS:
        row[key] = metrics.get(key)
    return row


def _run_task(task, resume=False, execute=True):
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
                resume, task['method'], execute=execute,
            )
    return row_from_metrics(task, metrics)


def run_tasks(tasks, workers='auto', resume=False, execute=True):
    available = os.cpu_count() or 1
    requested = available if workers == 'auto' else int(workers)
    pool_size = max(1, min(requested, len(tasks)))
    rows = []
    with ProcessPoolExecutor(max_workers=pool_size) as pool:
        futures = [pool.submit(_run_task, task, resume, execute) for task in tasks]
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (row['cell'], row['seed'], row['method'], row.get('fixed_n') or 0))
    return rows


def pairing_report(rows):
    """Every (cell, seed) pair must share one frozen input snapshot across methods."""
    groups = {}
    for row in rows:
        groups.setdefault((row['cell'], row['seed']), set()).add(row.get('input_snapshot_sha256'))
    mismatched = [f'{cell}/seed_{seed}' for (cell, seed), hashes in groups.items() if len(hashes) != 1]
    return {'pairs': len(groups), 'consistent': not mismatched, 'mismatched': mismatched}


def write_run_manifest(output, spec, tasks, resume):
    frozen = {
        'schema_version': 2, 'spec': spec['_path'], 'spec_sha256': spec['_sha256'],
        'task_count': len(tasks), 'tasks': [
            {key: task[key] for key in ('cell', 'seed', 'method', 'fixed_n', 'output', 'config')}
            for task in tasks
        ],
    }
    manifest = Path(output) / 'run_manifest.json'
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding='utf-8'))
        if previous != frozen:
            raise ValueError('Output contains a different frozen run manifest; use a new directory')
        if not resume:
            raise FileExistsError('Output already holds this run; pass --resume to continue it')
    else:
        _atomic_text(manifest, json.dumps(frozen, indent=2) + '\n')


def hotspot_tasks(output, config='configs/step1/scenarios/high_demand_side.yaml', methods=None):
    """Reproduce the existing high-demand case at 20 and 24 guides."""
    path = str(Path(config))
    loaded = load_config(path)
    tasks = []
    for fixed_n in (20, 24):
        if fixed_n > loaded['guides']['count']:
            raise ValueError('high_demand_side does not provide enough guides')
        for method in sorted(METHODS if methods is None else methods):
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
    row = row_from_metrics(task, metrics)
    row['planning_attempts'] = [
        {key: value for key, value in attempt.items() if key != 'cost'}
        for attempt in metrics.get('planning_attempts', [])
    ]
    return row


def distmesh_analysis(rows):
    """Explain DistMesh-inspired gaps from its sqrt(rho) fixed point using saved evidence.

    DistMesh-inspired equalises the integral of sqrt(rho) and has no gap cap, so
    the widest planned gap grows with density contrast. The damping 0.35 and
    iteration limit are unchanged: retuning them would not change the fixed
    point that this prediction describes.
    """
    from udc.boundary import Boundary
    from udc.evaluation.coverage import distmesh_gap_prediction
    report = {}
    for row in rows:
        if row['method'] != 'distmesh':
            continue
        directory = Path(row['output'])
        trajectory = directory / 'trajectory.npz'
        if not trajectory.exists():
            continue
        data = np.load(trajectory, allow_pickle=False)
        if not len(data['boundary_deployment']):
            continue
        config = yaml.safe_load((directory / 'config.yaml').read_text(encoding='utf-8'))
        control = config['controller']
        boundary = Boundary(data['boundary_crowd'], data['boundary_deployment'],
                            data['boundary_arc'], float(data['boundary_length']))
        prediction = distmesh_gap_prediction(
            boundary, data['crowd_positions'], data['crowd_demand'], control['demand_bandwidth'],
            control['quadrature_samples'], int(row['fixed_n']),
            control['max_gap_limit'] - 2 * control['arc_tolerance'])
        planned = [a['max_gap'] for a in row.get('planning_attempts', [])]
        if not planned:
            attempts_path = directory / 'planning.json'
            if attempts_path.exists():
                planned = [a['max_gap'] for a in json.loads(attempts_path.read_text(encoding='utf-8'))]
        key = f"{row.get('cell', '')}/seed_{row['seed']}/n{row['fixed_n']}"
        report[key] = {
            **prediction, 'observed_planned_gaps': planned,
            'termination_status': row['termination_status'],
            'stage_diagnosis': row.get('stage_diagnosis'),
        }
    return report


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
            'stage_diagnosis separates the necessary count condition, plan search, '
            'reachable assignment and execution; termination statuses are unchanged.'
        ),
        'pairing': pairing_report(rows),
        'distmesh_gap_analysis': distmesh_analysis(rows),
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
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--plan-only', action='store_true',
                        help='Stop after planning. Resume without this flag to execute feasible plans.')
    parser.add_argument('--hotspot', action='store_true')
    args = parser.parse_args()
    if args.hotspot:
        report = run_hotspot(args.output, args.workers)
        print(json.dumps({key: value for key, value in report.items() if key != 'runs'}, indent=2))
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
            'fixed_n': sorted({(task['cell'], task['fixed_n']) for task in tasks}),
        }, indent=2))
        return
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    write_run_manifest(output, spec, tasks, args.resume)
    rows = run_tasks(tasks, args.workers, args.resume, execute=not args.plan_only)
    _atomic_text(output / 'comparison.json', json.dumps(rows, indent=2, allow_nan=False) + '\n')
    summary = summarize_comparison(rows)
    summary['pairing'] = pairing_report(rows)
    summary['spec_sha256'] = spec['_sha256']
    summary['distmesh_gap_analysis'] = distmesh_analysis(rows)
    _atomic_text(output / 'comparison_summary.json',
                 json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps({
        'tasks': len(rows),
        'successful': sum(row['success'] for row in rows),
        'pairing': summary['pairing'],
        'classes': {
            name: sum(row['failure_class'] == name for row in rows)
            for name in sorted({row['failure_class'] for row in rows})
        },
        'stages': {
            name: sum(row['stage_diagnosis'] == name for row in rows)
            for name in sorted({str(row['stage_diagnosis']) for row in rows})
        },
    }, indent=2))


if __name__ == '__main__':
    main()
