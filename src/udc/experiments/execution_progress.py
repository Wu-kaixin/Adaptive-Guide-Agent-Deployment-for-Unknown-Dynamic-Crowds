"""Replay saved Step-1 plans after the free-space gate repair.

The nine scene-closure executions and wide-U seed 22 are not redrawn.
Targets, people and guide initial positions come from the saved artifacts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Point, Polygon
from threadpoolctl import threadpool_limits

from udc.boundary import Boundary
from udc.deployment import Plan
from udc.experiments.runner import experiment, reevaluate_result


def replay(config, source, destination):
    data = np.load(source / 'trajectory.npz')
    metrics = json.loads((source / 'metrics.json').read_text(encoding='utf-8'))
    boundary = Boundary(
        data['boundary_crowd'], data['boundary_deployment'], data['boundary_arc'],
        float(data['boundary_length']),
    )
    line = Polygon(boundary.deployment).exterior
    arcs = np.array([line.project(Point(point)) for point in data['targets']])
    plan = Plan(
        positions=np.asarray(data['targets'], dtype=float),
        arc_positions=arcs, cost=np.zeros(1),
        max_gap=float(metrics['planned_max_gap']), method=str(metrics['method']),
        iterations=0, exit_reason='reused_saved_targets',
        density_total=float(metrics.get('total_demand') or 0.0),
    )
    with threadpool_limits(limits=1):
        result = experiment(
            config, destination, plots=False, protocol='fixed_n',
            fixed_n=int(len(data['targets'])), method_label=str(metrics['method']),
            prescribed_boundary=boundary, prescribed_plan=plan,
            boundary_source='estimated_disk_union',
        )
    offline = reevaluate_result(destination)
    replayed = np.load(destination / 'trajectory.npz')
    same_shape = replayed['positions'].shape == data['positions'].shape
    return {
        'previous_status': metrics['termination_status'],
        'status': result['termination_status'],
        'success': bool(result['success']),
        'offline_consistent': bool(offline['criteria_consistent']),
        'time_s': result['simulation_time'],
        'previous_time_s': metrics['simulation_time'],
        'replans': result['replan_count'],
        'previous_replans': metrics['replan_count'],
        'position_delta_m': (None if not same_shape else float(np.max(np.abs(replayed['positions'] - data['positions'])))),
        'target_delta_m': float(np.max(np.abs(replayed['targets'] - data['targets']))),
        'initial_delta_m': float(np.max(np.abs(replayed['positions'][0] - data['positions'][0]))),
        'minimum_net_clearances': result.get('minimum_net_clearances'),
        'criteria': result.get('criteria'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nine-root', type=Path, default=Path('runs/step1_wide_u_replan_repair'))
    parser.add_argument('--seed22', type=Path,
                        default=Path('runs/step1_nonconvex_record_rebuild_v1/repair_regression/wide_u/seed_22/equal_arc'))
    parser.add_argument('--seed22-config', type=Path,
                        default=Path('runs/step1_nonconvex_record_rebuild_v1/inputs/wide_u/seed_22/input.yaml'))
    parser.add_argument('--output', type=Path, default=Path('runs/step1_execution_progress_repair/saved_replay'))
    args = parser.parse_args()
    rows = []
    for scene in ('elongated', 'tight_u', 'wide_u'):
        for method in ('equal_arc', 'projected_mass', 'pairwise_euclidean'):
            source = args.nine_root / scene / method / 'run'
            row = replay(source / 'config.yaml', source, args.output / 'nine' / scene / method)
            row['label'] = f'{scene}/{method}'
            rows.append(row)
            print(json.dumps(row), flush=True)
    row = replay(args.seed22_config, args.seed22, args.output / 'wide_u_seed22_equal_arc')
    row['label'] = 'wide_u/seed22/equal_arc'
    rows.append(row)
    print(json.dumps(row), flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'summary.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
