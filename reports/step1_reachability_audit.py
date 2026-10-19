"""Read-only inventory and cleanup plan for the 2026-10-19 local audit."""
import hashlib
import json
import platform
import sys
import importlib.metadata
import subprocess
from time import perf_counter
from collections import Counter
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / 'pyproject.toml').is_file() and (parent / 'src/udc').is_dir())
OUT = ROOT / 'runs/step1_reachability_diagnostics'
OUT.mkdir(parents=True, exist_ok=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def storage():
    mapping = {
        '2026-10-16-formal-step1': ROOT / 'runs/formal/step1_projection_v1',
        '2026-10-16-general-curve': ROOT / 'runs/step1_general_curve',
        '2026-10-16-kaii-reproduction': ROOT / 'runs/reproduction/Kaii',
        '2026-10-16-curve-constraints': ROOT / 'runs',
    }
    rows = []
    for folder, destination in mapping.items():
        base = ROOT / 'backups' / folder
        for path in sorted(base.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(base)
            target = destination / relative
            digest = sha(path)
            same = target.is_file() and sha(target) == digest
            if target.exists() and not same:
                # Keep unique historic content beside the associated experiment.
                target = destination / 'provenance' / '2026-10-16-original' / relative
                same = target.is_file() and sha(target) == digest
                if target.exists() and not same:
                    raise ValueError(f'Conflicting provenance target: {target}')
            rows.append({
                'source': path.relative_to(ROOT).as_posix(),
                'destination': target.relative_to(ROOT).as_posix(),
                'bytes': path.stat().st_size, 'sha256': digest,
                'action': 'delete_verified_duplicate' if same else 'move_unique',
            })
    save('storage_mapping.json', rows)
    summary = {'files': len(rows), 'bytes': sum(row['bytes'] for row in rows),
               'actions': dict(Counter(row['action'] for row in rows)),
               'mapping_sha256': sha(OUT / 'storage_mapping.json'),
               'unique_files': [row for row in rows if row['action'] == 'move_unique']}
    save('storage_plan.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def reevaluate_nine():
    import numpy as np
    from threadpoolctl import threadpool_limits
    from shapely.geometry import Point, Polygon
    from udc.boundary import Boundary
    from udc.safety import Safety
    from udc.navigation import navigation_domain
    from udc.evaluation.acceptance import evaluate
    from udc.experiments.config import load_config
    from udc.experiments.provenance import provenance
    import udc

    rows = []
    with threadpool_limits(limits=1):
        for scene in ('elongated', 'tight_u', 'wide_u'):
            for method in ('equal_arc', 'projected_mass', 'pairwise_euclidean'):
                base = ROOT / 'runs/step1_wide_u_replan_repair' / scene / method / 'run'
                metrics = json.loads((base / 'metrics.json').read_text(encoding='utf-8'))
                config = load_config(base / 'config.yaml')
                checksums = {name: sha(base / name) for name in
                             ('trajectory.npz', 'metrics.json', 'planning.json', 'config.yaml', 'state.json')}
                with np.load(base / 'trajectory.npz', allow_pickle=False) as data:
                    boundary = Boundary(data['boundary_crowd'], data['boundary_deployment'],
                                        data['boundary_arc'], float(data['boundary_length']))
                    safety = Safety(**metrics['safety_limits'])
                    mask = data['crowd_hotspot_mask'] if 'crowd_hotspot_mask' in data.files else None
                    started = perf_counter()
                    criteria, diagnostics = evaluate(
                        data['positions'], data['safe_velocity'], data['active_mask'],
                        data['targets'], data['assignment'], boundary, safety,
                        dict(config['controller'], _people=data['crowd_positions'],
                             _radii=data['crowd_radii'], _room=data['room'], _demand=data['crowd_demand'],
                             _hotspot_mask=None if mask is None or not len(mask) else mask))
                    evaluation_s = perf_counter() - started
                    domain = navigation_domain(data['room'], safety.wall_distance,
                                               Polygon(boundary.crowd), safety.wall_distance)
                    first_exit = None
                    for frame, positions in enumerate(data['positions']):
                        for guide, point in enumerate(positions):
                            if data['assignment'][guide] < 0:
                                continue
                            # Record sub-tolerance rounding separately from genuine domain exits.
                            if domain.free.covers(Point(point)):
                                continue
                            signed = domain.signed_free_distance(point)
                            if signed < -1e-7 and first_exit is None:
                                first_exit = {'frame': frame, 'guide': guide,
                                              'position': point.tolist(), 'signed_distance_m': signed}
                    input_arrays = ('crowd_positions', 'crowd_radii', 'crowd_demand', 'targets',
                                    'boundary_crowd', 'boundary_deployment', 'boundary_arc', 'room')
                    hashes = {key: hashlib.sha256(np.ascontiguousarray(data[key]).tobytes()).hexdigest()
                              for key in input_arrays}
                    hashes['initial_guides'] = hashlib.sha256(data['positions'][0].tobytes()).hexdigest()
                    actual_length = float(np.linalg.norm(np.diff(data['positions'], axis=0), axis=2).sum())
                    row = dict(scene=scene, method=method, source=base.relative_to(ROOT).as_posix(),
                               stored_status=metrics['termination_status'], stored_seed=metrics['seed'],
                               independent_success=all(criteria.values()),
                               criteria_consistent=criteria == metrics['criteria'],
                               criteria=criteria, diagnostics=diagnostics,
                               evaluation_wall_s=evaluation_s, file_sha256=checksums,
                               input_array_sha256=hashes, geometry_sha256=domain.geometry_sha256,
                               guide_components=[domain.component_index(p) for p in data['positions'][0]],
                               target_components=[domain.component_index(p) for p in data['targets']],
                               first_exit_beyond_1e_minus_7_m_tolerance=first_exit,
                               simulation_time_s=metrics['simulation_time'], steps=metrics['steps'],
                               actual_trajectory_length_m=actual_length,
                               initial_assigned_path_length_m=metrics['path_length'],
                               stored_wall_s=metrics['wall_time'], replan_count=metrics['replan_count'],
                               replan_duration_s=None,
                               replan_duration_unavailable_reason='Historic metrics do not time individual replans',
                               deadlock_events=metrics['deadlock_events'])
                    for name, digest in checksums.items():
                        assert sha(base / name) == digest, 'Historic artifact changed'
                    rows.append(row)
                    print(f'{scene}/{method}: {all(criteria.values())}, match={row["criteria_consistent"]}, eval={evaluation_s:.3f}s')
    save('stable_nine_reevaluation.json', rows)
    packages = ('numpy', 'scipy', 'shapely', 'PyYAML', 'matplotlib', 'jupedsim', 'pedpy', 'pytest', 'pip', 'setuptools')
    dependencies = {}
    for name in packages:
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    info = dict(python=sys.version, executable=sys.executable, udc_import=udc.__file__,
                platform=platform.platform(), dependencies=dependencies,
                source_provenance=provenance({}, None, 'offline_audit', None),
                initial_head='b5eb3418a2363e0ec0864fcde4455b3fd8716d8b',
                remote_head='b5eb3418a2363e0ec0864fcde4455b3fd8716d8b',
                latest_requested_commit='e2a0430', latest_requested_commit_available=False,
                editable_direct_url=json.loads(importlib.metadata.distribution('udc-guide-deployment').read_text('direct_url.json')),
                execution_runs_this_round=0, fixed_input_33_available=False,
                host_report_counts_unverified=dict(total=45, initialization_failure=12, valid=33, success=22, path_unreachable=11))
    save('environment_and_sync.json', info)
    save('stable_nine_summary.json', dict(total=len(rows), success=sum(r['independent_success'] for r in rows),
         criteria_mismatches=sum(not r['criteria_consistent'] for r in rows),
         new_simulations=0, new_seeds=0, historical_files_changed=0,
         replans_retimed=False, interpretation='Independent saved-trajectory reevaluation; not a new execution regression'))


def inventory():
    metrics = []
    errors = []
    for path in sorted((ROOT / 'runs').rglob('metrics.json')):
        try:
            value = json.loads(path.read_text(encoding='utf-8-sig'))
            metrics.append({'path': path.relative_to(ROOT).as_posix(),
                            'seed': value.get('seed'), 'status': value.get('termination_status'),
                            'scene': value.get('scene'), 'method': value.get('method'),
                            'commit': value.get('commit')})
        except (OSError, ValueError) as error:
            errors.append({'path': path.relative_to(ROOT).as_posix(), 'error': str(error)})
    result = {'metric_files': len(metrics), 'read_errors': errors,
              'seed_20_24_tasks': [row for row in metrics if row['seed'] in range(20, 25)],
              'commit_prefix_e2a0430_tasks': [row for row in metrics if str(row['commit']).startswith('e2a0430')]}
    save('local_metrics_inventory.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'reevaluate-nine':
        reevaluate_nine()
    elif len(sys.argv) > 1 and sys.argv[1] == 'inventory':
        inventory()
    elif len(sys.argv) > 1 and sys.argv[1] == 'plan-storage' and (ROOT / 'backups').is_dir():
        storage()
    else:
        raise SystemExit('Choose reevaluate-nine or inventory; plan-storage requires existing project backups.')
