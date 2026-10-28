"""Classify a fixed checkpoint's saved path failures; never execute a robot."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def diagnose(study, cohort, unit):
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    sys.path.insert(0, str(study / 'source_snapshot/src'))
    import numpy as np
    from shapely.geometry import Point
    from shapely.ops import unary_union
    from udc.experiments import static_multigroup as frozen
    from udc.multigroup.geometry import outer_disk
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    tasks = {task['task_id']: task for task in manifest['tasks']}
    ids = cohort['units'][unit]
    _, _, observation = frozen._load_inputs(study, tasks[ids[0]])
    _, domain = frozen.construct_groups(observation, manifest['spec']['room_m'], manifest['spec']['model'])
    config = manifest['spec']['model']
    records = []
    for task_id in ids:
        folder = study / 'results' / task_id
        if digest(folder / 'receipt.json') != cohort['receipt_sha256'][task_id]:
            raise ValueError('Checkpoint task changed')
        state = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
        with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
            positions = data['positions'][-1].copy()
            targets, assignment = data['targets'].copy(), data['assignment'].copy()
        guide = state['evidence']['guide']
        target = targets[assignment[guide]]
        stationary = {j: outer_disk(point, config['guide_distance_m'], quad_segs=4)
                      for j, point in enumerate(positions) if j != guide}
        free = domain.free.difference(unary_union(list(stationary.values())))
        endpoints = []
        for name, point in [('start', positions[guide]), ('target', target)]:
            query = Point(point)
            distances = np.linalg.norm(positions - point, axis=1)
            distances[guide] = np.inf
            blockers = [{'guide': j, 'center_distance_m': float(distances[j]),
                         'strict_polygon_interior': disk.contains(query),
                         'polygon_boundary_distance_m': disk.boundary.distance(query)}
                        for j, disk in stationary.items() if disk.covers(query)]
            endpoints.append({'endpoint': name, 'coordinate_m': point.tolist(),
                              'minimum_other_guide_center_distance_m': float(distances.min()),
                              'static_free_covers': domain.free.covers(query),
                              'serial_free_covers': free.covers(query), 'polygon_blockers': blockers})
        raw_reason = state['evidence']['reason']
        category, component = 'unresolved', {}
        if not all(row['serial_free_covers'] for row in endpoints):
            if not all(row['static_free_covers'] for row in endpoints):
                category = 'static_domain_endpoint_rejection'
            elif any(row['minimum_other_guide_center_distance_m'] < config['guide_distance_m'] for row in endpoints):
                category = 'stationary_robot_analytic_clearance_not_met'
            elif any(blocker['strict_polygon_interior'] for row in endpoints for blocker in row['polygon_blockers']):
                category = 'analytic_clearance_passed_circumscribed_polygon_rejected'
            else:
                category = 'boolean_serial_domain_rejection_unresolved'
        elif raw_reason == 'finite_visibility_graph' and free.geom_type in ('Polygon', 'MultiPolygon'):
            parts = [free] if free.geom_type == 'Polygon' else list(free.geoms)
            starts = [i for i, part in enumerate(parts) if part.covers(Point(positions[guide]))]
            ends = [i for i, part in enumerate(parts) if part.covers(Point(target))]
            connected = set(starts)
            changed = True
            while changed:
                changed = False
                for i in list(connected):
                    for j, part in enumerate(parts):
                        if j not in connected and parts[i].intersects(part):
                            connected.add(j)
                            changed = True
            reaches = bool(set(ends) & connected)
            gap = min((parts[i].distance(parts[j]) for i in connected for j in ends), default=None)
            category = ('same_or_touching_component_finite_graph_failure' if reaches else
                        'strictly_separated_serial_components' if gap is not None and gap > 0 else
                        'component_relation_unresolved')
            component = {'start_parts': starts, 'target_parts': ends, 'parts_count': len(parts),
                         'target_connected_through_touching_parts': reaches, 'positive_gap_m': gap}
        row = {'task_id': task_id, 'guide': guide, 'original_reason': raw_reason, 'category': category,
               'guide_distance_threshold_m': config['guide_distance_m'], 'endpoints': endpoints,
               'components': component, 'serial_free_valid': free.is_valid,
               'serial_free_wkb_sha256': hashlib.sha256(free.wkb).hexdigest(),
               'static_free_wkb_sha256': hashlib.sha256(domain.free.wkb).hexdigest(),
               'original_receipt_sha256': cohort['receipt_sha256'][task_id]}
        records.append(row)
    return records


def select_cases(study, manifest, checkpoint=None):
    preserved = (json.loads((checkpoint / 'preserved_receipts.json').read_text(encoding='utf-8'))
                 if checkpoint is not None else None)
    units, hashes = defaultdict(list), {}
    for task in manifest['tasks']:
        if preserved is not None and task['task_id'] not in preserved:
            continue
        folder = study / 'results' / task['task_id']
        if not (folder / 'receipt.json').is_file():
            continue
        if json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))['status'] == 'PATH_NOT_FOUND':
            units[task['unit_id']].append(task['task_id'])
            hashes[task['task_id']] = preserved[task['task_id']] if preserved is not None else digest(folder / 'receipt.json')
    return dict(units), hashes


def run(study, checkpoint, output, workers):
    output.mkdir(parents=True, exist_ok=False)
    (output / 'units').mkdir()
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    units, hashes = select_cases(study, manifest, checkpoint)
    cohort = {'manifest_sha256': digest(study / 'manifest.json'), 'units': dict(units),
              'receipt_sha256': hashes, 'selection': ('All PATH_NOT_FOUND rows in the fixed preserved checkpoint.' if checkpoint else
                'All recorded PATH_NOT_FOUND rows at final report time; no favorable-case selection.')}
    cohort_path = output / 'cohort.json'
    cohort_path.write_text(json.dumps(cohort, indent=2) + '\n', encoding='utf-8')
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        environment[name] = '1'

    def one(unit):
        path = output / 'units' / (unit + '.json')
        with path.open('wb') as stdout, path.with_suffix('.stderr.log').open('wb') as stderr:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/inspect_frozen_failures.py'),
                                     '--study', str(study), '--cohort', str(cohort_path), '--unit', unit],
                                    cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            raise RuntimeError('Failure diagnosis error: ' + str(path))
        return json.loads(path.read_text(encoding='utf-8'))

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(one, unit) for unit in units]):
            rows.extend(future.result())
    rows.sort(key=lambda row: row['task_id'])
    summary = {'cases': len(rows), 'scheduled_cases': len(hashes), 'categories': dict(Counter(row['category'] for row in rows)),
               'original_reasons': dict(Counter(row['original_reason'] for row in rows)),
               'new_robot_executions': 0, 'new_independent_scenes': 0,
               'scope': 'Controller-geometry diagnoses of a fixed checkpoint, not global physical infeasibility proofs.'}
    (output / 'records.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--checkpoint')
    parser.add_argument('--output')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--cohort')
    parser.add_argument('--unit')
    args = parser.parse_args()
    if args.unit:
        value = diagnose(Path(args.study).resolve(), json.loads(Path(args.cohort).read_text(encoding='utf-8')), args.unit)
    else:
        value = run(Path(args.study).resolve(), Path(args.checkpoint).resolve() if args.checkpoint else None,
                    Path(args.output).resolve(), args.workers)
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
