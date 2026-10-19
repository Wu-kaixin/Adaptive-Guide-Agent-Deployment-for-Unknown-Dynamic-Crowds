"""Limited, labelled legacy regression where the complete 73 raw tasks are absent.

Three v2 seed30 cells and v1 wide-U seed22 are rebuilt with their frozen
generators. Their hashes are compared to the published records. Nine older
scene-closure snapshots actually present on this host are replayed verbatim.
This supplement never claims to reproduce the complete previous 73 tasks.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from shapely.geometry import Point, Polygon
from threadpoolctl import threadpool_limits
import yaml

from udc.boundary import Boundary, estimate_disk_union
from udc.deployment import Plan
from udc.experiments.nonconvex_rebuild import (load_protocol, draw_cell, array_hash, sha, write)
from udc.experiments.provenance import provenance
from udc.experiments.runner import experiment, reevaluate_result, _atomic_npz, _atomic_text
from udc.experiments.scene_closure import _execution_config, _people_report
from udc.experiments.target_domain import run_case


def _rebuild(root, name, selected):
    spec, base = load_protocol(f'configs/step1/{name}.yaml')
    package = root / name
    source = package / 'reconstructed'
    source.mkdir(parents=True, exist_ok=False)
    write(source / 'protocol.json', {'spec': spec, 'base_spec': base, 'data_origin': 'protocol_reconstruction'})
    historical = (json.loads(Path('reports/step1_nonconvex_confirmation_v2/paired_units_30.json').read_text())
        if name == 'nonconvex_confirmation_v2' else
        json.loads(Path('reports/step1_nonconvex_record_rebuild_v1/summary.json').read_text())['tasks_45'])
    checksums = json.loads(Path(f'reports/step1_{name}/checksums.json').read_text())['files']
    comparisons, rows = [], []
    for scene_name, seed, methods in selected:
        scene = next(item for item in base['scenes'] if item['name'] == scene_name)
        data, stream = draw_cell(spec, base, scene, seed)
        cell = source / 'inputs' / scene_name / f'seed_{seed}'
        cell.mkdir(parents=True)
        _atomic_npz(cell / 'input.npz', **data)
        cfg = _execution_config(cell, data['people'], np.array([[0,0],[30,0],[30,30],[0,30]]),
                                spec['guide_counts'][scene_name], seed, base)
        config = yaml.safe_load(cfg.read_text())
        config['guides']['initial_positions'] = data['guides'].tolist()
        _atomic_text(cfg, yaml.safe_dump(config, sort_keys=False))
        quality = _people_report(data['people'], data['radii'], base['room_m'], base['minimum_center_distance_m'])
        if not (quality['meets_stated_separation'] and quality['inside_room']):
            raise RuntimeError('Chosen representative has invalid input; do not redraw it')
        b = estimate_disk_union(data['people'], 1.3, .1, data['room'], .55, .75,
                                 data['radii'], closing_radius=.3, quad_segs=16)
        _atomic_npz(cell / 'boundary.npz', crowd=b.crowd, deployment=b.deployment,
                    arc=b.arc, length=np.array(b.length))
        digest = array_hash(**{key: data[key] for key in ('people','radii','demand','guides','room')})
        previous = next(row for row in historical if row['scene'] == scene_name and row['seed'] == seed)
        prefix = f'runs/step1_{name}/inputs/{scene_name}/seed_{seed}/'
        expected_file = checksums.get(prefix + 'input.npz')
        expected_boundary = checksums.get(prefix + 'boundary.npz')
        comparison = {'scene': scene_name, 'seed': seed, 'origin': 'protocol_reconstruction',
            'input_array_sha256': digest, 'historical_input_array_sha256': previous['input_sha256'],
            'input_array_hash_matches': digest == previous['input_sha256'],
            'input_file_hash_matches': None if expected_file is None else sha(cell/'input.npz') == expected_file,
            'boundary_file_hash_matches': None if expected_boundary is None else sha(cell/'boundary.npz') == expected_boundary}
        comparisons.append(comparison)
        write(cell / 'input_record.json', {'status': 'ACCEPTED', 'error': '', 'input_sha256': digest,
            'input_file_sha256': sha(cell/'input.npz'), 'config_sha256': sha(cfg),
            'boundary_sha256': sha(cell/'boundary.npz'), 'random_streams': stream})
        for method in methods:
            rows.append({'task_id': f'{scene_name}__seed_{seed}__{method}', 'scene': scene_name,
                'seed': seed, 'method': method, 'count': spec['guide_counts'][scene_name],
                'cell': cell.relative_to(source).as_posix(), 'input_sha256': digest, 'input_status': 'ACCEPTED'})
    write(source / 'manifest.json', {'tasks': rows, 'task_count': len(rows), 'reconstructed_subset_only': True})
    write(package / 'historical_hash_comparison.json', comparisons)
    return package, rows, historical


def replay_saved(arguments):
    source, destination = map(Path, arguments)
    saved = np.load(source/'trajectory.npz', allow_pickle=False)
    previous = json.loads((source/'metrics.json').read_text())
    _, base = load_protocol('configs/step1/nonconvex_execution_confirmation_v3.yaml')
    input_dir = destination.parent / (destination.name + '_config')
    input_dir.mkdir(parents=True, exist_ok=False)
    cfg = _execution_config(input_dir, saved['crowd_positions'], np.array([[0,0],[30,0],[30,30],[0,30]]),
        len(saved['targets']), previous['seed'], base)
    config = yaml.safe_load(cfg.read_text())
    config['guides']['initial_positions'] = saved['positions'][0].tolist()
    _atomic_text(cfg, yaml.safe_dump(config, sort_keys=False))
    b = Boundary(saved['boundary_crowd'], saved['boundary_deployment'], saved['boundary_arc'], float(saved['boundary_length']))
    line = Polygon(b.deployment).exterior
    plan = Plan(saved['targets'], np.array([line.project(Point(t)) for t in saved['targets']]),
        np.zeros(1), previous['planned_max_gap'], previous['method'], 0, 'REUSE_SAVED_TARGETS', previous['total_demand'])
    with threadpool_limits(limits=1):
        result = experiment(cfg, destination, plots=False, protocol='fixed_n', fixed_n=len(plan.positions),
            prescribed_boundary=b, prescribed_plan=plan, boundary_source='estimated_disk_union', method_label=plan.method)
        offline = reevaluate_result(destination)
    actual = np.load(destination/'trajectory.npz', allow_pickle=False)
    paired = all(np.array_equal(actual[key], saved[key]) for key in
        ('targets', 'crowd_positions', 'crowd_radii', 'crowd_demand', 'room')) and np.array_equal(actual['positions'][0], saved['positions'][0])
    record = {'source': str(source), 'source_trajectory_sha256': sha(source/'trajectory.npz'),
        'origin': 'existing_host_raw_scene_closure_snapshot', 'recomputed_target': False,
        'postprocessing': False, 'termination_status': result['termination_status'],
        'offline_consistent': offline['criteria_consistent'], 'exact_input_and_targets': bool(paired),
        'simulation_time': result['simulation_time'], 'replan_count': result['replan_count'],
        'minimum_net_clearances': result['minimum_net_clearances']}
    write(destination/'saved_input_provenance.json', record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='runs/step1_target_domain_consistency/legacy_supplement')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.workers <= 6:
        raise ValueError('one to six workers')
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=False)
    methods = ['equal_arc', 'projected_mass', 'pairwise_euclidean']
    selections = [('nonconvex_confirmation_v2', [(scene, 30, methods) for scene in ('elongated','tight_u','wide_u')]),
                  ('nonconvex_record_rebuild_v1', [('wide_u', 22, ['equal_arc'])])]
    cfg = yaml.safe_load(Path('configs/step1/target_domain_consistency.yaml').read_text())
    cfg['input_subdirectory'] = 'reconstructed'
    tasks = []
    for name, selected in selections:
        package, rows, _ = _rebuild(root, name, selected)
        tasks.extend((str(package), row, 'regression', cfg) for row in rows)
    saved_root = Path('runs/reproduction/kai-14900kf/stable_b5eb341/scene_closure/execution')
    saved_tasks = [(saved_root/scene/'disk_union_primary'/method/'run', root/'saved_nine'/scene/method)
                    for scene in ('elongated','tight_u','wide_u') for method in methods]
    write(root/'freeze.json', {'provenance': provenance({}, None, 'fixed_n', None),
        'workers': args.workers, 'numerical_threads_per_worker': 1, 'reconstructed_tasks': [t[1] for t in tasks],
        'saved_tasks': [str(t[0]) for t in saved_tasks], 'complete_previous_73_reproduced': False})
    started = perf_counter()
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_case, task) for task in tasks] + [pool.submit(replay_saved, task) for task in saved_tasks]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({'case': result.get('task_id', result.get('source')), 'status': result['termination_status']}), flush=True)
    for name, _ in selections:
        package = root/name
        comparison = json.loads((package/'historical_hash_comparison.json').read_text())
        for item in comparison:
            item['target_file_comparisons'] = {}
            checksums = json.loads(Path(f'reports/step1_{name}/checksums.json').read_text())['files']
            for file in (package/'reconstructed/targets'/item['scene']/f"seed_{item['seed']}").glob('*/targets.npz'):
                expected = checksums.get(f"runs/step1_{name}/targets/{item['scene']}/seed_{item['seed']}/{file.parent.name}/targets.npz")
                item['target_file_comparisons'][file.parent.name] = {'sha256': sha(file),
                    'historical_sha256': expected, 'matches': None if expected is None else expected == sha(file)}
        write(package/'historical_hash_comparison.json', comparison)
    summary = {'scope': '19 limited legacy cases; NOT previous 73/73',
        'wall_s': perf_counter()-started, 'tasks': len(results),
        'successes': sum(r['termination_status']=='SUCCESS' for r in results),
        'offline_inconsistent': sum(not r['offline_consistent'] for r in results),
        'rows': results}
    write(root/'summary.json', summary)
    print(json.dumps({key: value for key, value in summary.items() if key!='rows'}), flush=True)


if __name__=='__main__':
    main()
