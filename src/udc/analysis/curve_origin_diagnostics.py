"""Audit geometric parameter origins in the saved 1 mm target-jump pair."""
import json

import numpy as np
from shapely.geometry import LineString, Point

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.geometry import construct_groups
from udc.experiments.static_multigroup import ROOT, digest, write_json


def main():
    study = ROOT / 'runs/static_service_audit_20261026/stability_v1'
    report = ROOT / 'reports/static_service_audit_20261026'
    selected = json.loads((report / 'target_jump_diagnosis.json').read_text(encoding='utf-8'))
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    grouped = []
    for pair in selected['pair']:
        task = next(u for u in manifest['units'] if u['unit_id'] == pair['task_id'])
        with np.load(study / task['observation'], allow_pickle=False) as obs:
            measurement = BoundedMeasurement(Observation(obs['positions'], obs['radii'], obs['demand']), .12, task['measurement_model'])
        groups, _ = construct_groups(measurement, manifest['base_spec']['room_m'], manifest['base_spec']['model'])
        grouped.append(groups)
    rows = []
    for old, new in zip(*grouped):
        old_line = LineString(np.vstack([old.boundary.deployment, old.boundary.deployment[0]]))
        new_line = LineString(np.vstack([new.boundary.deployment, new.boundary.deployment[0]]))
        shift = old_line.project(Point(new.boundary.deployment[0]))
        rows.append({'group_id': old.group_id, 'deployment_polyline_hausdorff_m': old_line.hausdorff_distance(new_line),
                     'origin_euclidean_shift_m': float(np.linalg.norm(old.boundary.deployment[0] - new.boundary.deployment[0])),
                     'new_origin_projected_to_old_arc_m': shift,
                     'shortest_origin_arc_shift_m': min(shift, old_line.length - shift),
                     'old_length_m': old_line.length, 'new_length_m': new_line.length})
    result = {'pair': [r['task_id'] for r in selected['pair']], 'groups': rows,
              'target_jump_diagnosis_sha256': digest(report / 'target_jump_diagnosis.json'),
              'interpretation': 'phase indices depend on curve origins; geometric and parameterization changes must be distinguished'}
    write_json(report / 'curve_origin_diagnosis.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
