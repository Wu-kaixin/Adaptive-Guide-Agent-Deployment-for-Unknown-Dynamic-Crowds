"""Reconstruct a saved same-allocation target jump without changing actions."""
import json
from pathlib import Path

import numpy as np

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.geometry import construct_groups
from udc.multigroup.allocation import allocate, build_catalog, selected_targets
from udc.experiments.static_multigroup import ROOT, digest, write_json


def main():
    study = ROOT / 'runs/static_service_audit_20261026/stability_v1'
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    critical = ['src/udc/multigroup/allocation.py', 'src/udc/multigroup/geometry.py',
                'src/udc/deployment/planners.py', 'src/udc/multigroup/observation.py']
    if any(digest(ROOT / name) != manifest['sources'][name] for name in critical):
        raise ValueError('Planning source no longer matches saved study')
    rows = [json.loads(p.read_text(encoding='utf-8')) for p in (study / 'results').glob('*/metrics.json')]
    eligible = [r for r in rows if r.get('counts_l1') == 0 and r['perturbation_m'] == .001]
    selected = max(eligible, key=lambda r: r.get('matched_target_rms_m') or 0)
    baseline_id = selected['task_id'].rsplit('_p', 1)[0] + '_p0'
    baseline = json.loads((study / 'results' / baseline_id / 'metrics.json').read_text(encoding='utf-8'))
    model, spec = manifest['base_spec']['model'], manifest['spec']
    diagnostics = []
    for row in (baseline, selected):
        with np.load(study / row['observation'], allow_pickle=False) as data:
            obs = BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']), spec['declared_error_bound_m'], row['measurement_model'])
        groups, domain = construct_groups(obs, manifest['base_spec']['room_m'], model)
        catalog = build_catalog(groups, obs, domain, row['budget'], row['layout'], model)
        allocation = allocate(catalog, groups, row['budget'], row['allocator'])
        targets, _ = selected_targets(allocation, model)
        with np.load(study / 'results' / row['task_id'] / 'trajectory.npz', allow_pickle=False) as data:
            if not np.array_equal(targets, data['targets']):
                raise ValueError('Reconstructed targets differ from saved actions')
        diagnostics.append({'task_id': row['task_id'], 'counts': allocation.counts,
                            'selected_phases': [c.phase for c in allocation.candidates],
                            'targets': targets, 'planning_objective': allocation.objective,
                            'source_metrics_sha256': digest(study / 'results' / row['task_id'] / 'metrics.json')})
    result = {'selection': 'largest matched RMS among all completed 1mm same-count perturbations; descriptive extreme',
              'additional_perturbation_bound_m': .001, 'matched_target_rms_m': selected['matched_target_rms_m'],
              'target_hausdorff_m': selected['target_hausdorff_m'],
              'continuous_worst_delta': selected['continuous_worst_delta'],
              'partition_stability_certificate': selected['stability_certificate'], 'pair': diagnostics,
              'interpretation': 'phase/optimization selection can switch despite certified unchanged grouping; not dynamic execution'}
    destination = ROOT / 'reports/static_service_audit_20261026/target_jump_diagnosis.json'
    write_json(destination, result)
    print(json.dumps({k: v for k, v in result.items() if k != 'pair'}, indent=2))


if __name__ == '__main__':
    main()
