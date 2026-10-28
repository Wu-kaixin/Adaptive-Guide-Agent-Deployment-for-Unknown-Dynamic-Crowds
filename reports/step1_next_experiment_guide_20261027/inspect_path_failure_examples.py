"""Read-only reconstruction of two saved failures; never executes a deployment.

Run with the project's .venv Python. Scientific inputs and sources are read from
the original frozen study. Only the adjacent diagnostic JSON is written.
"""
import os
for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '1'

from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / 'runs/static_multigroup_20261026/confirmation_v2_frozen'
sys.path.insert(0, str(STUDY / 'source_snapshot/src'))

from datetime import datetime
import hashlib
import importlib.metadata
import json
import platform
import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union
from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.geometry import construct_groups, outer_disk
import udc.multigroup.geometry as geometry_module


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def geometry_hash(geom):
    return hashlib.sha256(geom.wkb).hexdigest()


def reconstruct(task_id):
    folder = STUDY / 'results' / task_id
    receipt = json.loads((folder / 'receipt.json').read_text())
    actual_hashes = {name: sha(folder / name) for name in receipt}
    if actual_hashes != receipt:
        raise ValueError('Original artifact hash mismatch: ' + task_id)
    saved = json.loads((folder / 'config.json').read_text())
    task, config = saved['task'], saved['model']
    state = json.loads((folder / 'state.json').read_text())
    with np.load(STUDY / task['observation'], allow_pickle=False) as data:
        measurement = BoundedMeasurement(
            Observation(data['positions'], data['radii'], data['demand']),
            task['error_bound_m'], 'frozen_bounded_measurement')
    groups, domain = construct_groups(measurement, [40., 30.], config)
    with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
        current = data['positions'][-1].copy()
        assignment = data['assignment'].copy()
        targets = data['targets'].copy()
    guide = state['evidence']['guide']
    target = targets[assignment[guide]]
    stationary = {j: outer_disk(point, config['guide_distance_m'], quad_segs=4)
                  for j, point in enumerate(current) if j != guide}
    free = domain.free.difference(unary_union(list(stationary.values())))
    endpoint_details = []
    for label, point in [('start', current[guide]), ('target', target)]:
        query = Point(point)
        blockers = []
        for j, disk in stationary.items():
            if disk.covers(query):
                blockers.append({
                    'guide': j, 'position_m': current[j].tolist(),
                    'assignment': int(assignment[j]),
                    'distance_m': float(np.linalg.norm(point - current[j])),
                    'analytic_clearance_m': float(np.linalg.norm(point - current[j]) - config['guide_distance_m']),
                    'polygon_contains_endpoint': bool(disk.contains(query)),
                    'polygon_boundary_distance_m': float(disk.boundary.distance(query)),
                    'polygon_endpoint_relate': disk.relate(query),
                    'polygon_wkb_sha256': geometry_hash(disk),
                    'at_own_target_bitwise': bool(assignment[j] >= 0 and np.array_equal(current[j], targets[assignment[j]])),
                })
        endpoint_details.append({
            'endpoint': label, 'position_m': point.tolist(),
            'static_free_covers': bool(domain.free.covers(query)),
            'serial_free_covers': bool(free.covers(query)),
            'serial_free_endpoint_relate': free.relate(query),
            'stationary_blockers': blockers,
        })
    components = [free] if free.geom_type == 'Polygon' else list(free.geoms)
    pairs = np.linalg.norm(targets[:, None] - targets[None, :], axis=2)
    pairs[np.diag_indices(len(targets))] = np.inf
    return {
        'task_id': task_id, 'failure': state['evidence'],
        'source_folder': str(folder), 'receipt_sha256': sha(folder / 'receipt.json'),
        'artifact_sha256': actual_hashes, 'all_receipt_hashes_match': True,
        'frozen_inputs': {name: {'path': str(STUDY / task[name]), 'sha256': sha(STUDY / task[name])}
                          for name in ('observation', 'truth')},
        'truth_use': 'Only hashed for provenance; not loaded or used in geometric reconstruction.',
        'completed_serial_paths': len(state['paths']), 'robot_count': len(current),
        'target_count': len(targets), 'observed_group_count': len(groups),
        'minimum_target_pair_distance_m': float(pairs.min()),
        'guide_distance_threshold_m': config['guide_distance_m'],
        'stationary_disk_quad_segs': 4,
        'stationary_polygon_circumradius_m': float(config['guide_distance_m'] / np.cos(np.pi / 16) + 1e-12),
        'endpoint_details': endpoint_details,
        'serial_free_geometry_type': free.geom_type, 'serial_free_valid': bool(free.is_valid),
        'serial_free_component_count': len(components),
        'component_pair_distances_m': [[float(first.distance(second)) for second in components]
                                       for first in components],
        'start_components': [i for i, geom in enumerate(components) if geom.covers(Point(current[guide]))],
        'target_components': [i for i, geom in enumerate(components) if geom.covers(Point(target))],
        'static_free_wkb_sha256': geometry_hash(domain.free),
        'serial_free_wkb_sha256': geometry_hash(free),
    }


if __name__ == '__main__':
    manifest = json.loads((STUDY / 'manifest.json').read_text())
    scientific_sources = {}
    for relative in ('src/udc/multigroup/geometry.py', 'src/udc/multigroup/execution.py',
                     'src/udc/multigroup/observation.py', 'src/udc/navigation/paths.py'):
        actual = sha(STUDY / 'source_snapshot' / relative)
        if actual != manifest['sources'][relative]:
            raise ValueError('Frozen scientific source changed: ' + relative)
        scientific_sources[relative] = actual
    rows = [reconstruct(task) for task in (
        'K2_circle_s2000_e0.04_sufficient_projected_mass_demand',
        'K2_concave_s2003_e0.00_sufficient_projected_mass_demand')]
    record = {
        'captured_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'scope': 'Two selected existing failures, read-only diagnosis; no new experiment or population attribution.',
        'selection': 'First encountered saved example of each failure reason during bounded artifact search, not random sampling.',
        'manifest_sha256': sha(STUDY / 'manifest.json'),
        'python': platform.python_version(), 'python_executable': sys.executable,
        'dependency_versions': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'shapely')},
        'loaded_geometry_module': str(Path(geometry_module.__file__).resolve()),
        'frozen_source_sha256': scientific_sources,
        'diagnostic_script_sha256': sha(__file__), 'reconstructed_examples': rows,
        'interpretations': [
            'First example: a final target is physically separated by more than 0.75 m from an already arrived robot, but lies strictly inside its conservative circumscribed 16-sided obstacle. This is an observed target-admission/execution-representation mismatch for this pair, not an assumed floating-point error.',
            'Second example: accepted start and goal belong to different components of the current conservative serial free space. Adding graph nodes alone cannot connect them without changing that free space. This does not prove global physical infeasibility or failure under a different execution order.',
            'No claim that these two mechanisms explain all PATH_NOT_FOUND rows. No assignment, order, geometry, threshold, trajectory or old artifact was changed.',
        ],
    }
    destination = Path(__file__).with_name('path_failure_examples.json')
    destination.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(destination), 'examples': len(rows),
                      'all_receipt_hashes_match': all(row['all_receipt_hashes_match'] for row in rows)}, indent=2))
