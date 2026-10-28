"""Preserve the pre-recorded fixed-action audit sources after verifying hashes."""
import json
from pathlib import Path
import shutil

from udc.experiments.static_multigroup import ROOT, digest, write_json


def main():
    study = ROOT / 'runs/static_service_audit_20261026/fixed_action_v1'
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    receipt = json.loads((study / 'receipt.json').read_text(encoding='utf-8'))
    if digest(study / 'manifest.json') != receipt['manifest_sha256'] or digest(study / 'summary.json') != receipt['summary_sha256']:
        raise ValueError('Fixed-action audit receipt changed')
    for relative, expected in manifest['sources'].items():
        source = ROOT / relative
        if digest(source) != expected:
            raise ValueError(f'Pre-audit source changed; cannot reconstruct snapshot: {relative}')
        target = study / 'source_snapshot' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(target)
        shutil.copyfile(source, target)
    for name, expected in receipt['results'].items():
        if digest(study / 'results' / name) != expected:
            raise ValueError('Fixed-action audit result changed')
    pressure = ROOT / 'runs/static_service_audit_20261026/pressure_v1'
    folder = pressure / 'results/K2_s1600_near_wall'
    import numpy as np
    with np.load(pressure / 'inputs/K2_s1600_near_wall/truth.npz', allow_pickle=False) as data:
        distance = np.linalg.norm(data['guides'][:, None] - data['positions'][None], axis=2)
        net = distance - (.2 + data['radii'][None] + .35)
        i, j = np.unravel_index(np.argmin(net), net.shape)
        diagnostic = {'guide_index': int(i), 'human_index': int(j), 'guide_position_m': data['guides'][i].tolist(),
                      'human_position_m': data['positions'][j].tolist(), 'center_distance_m': float(distance[i, j]),
                      'required_center_distance_m': float(.2 + data['radii'][j] + .35),
                      'net_clearance_m': float(net[i, j]), 'executed_intervals': 0,
                      'source_metrics_sha256': digest(folder / 'metrics.json'),
                      'interpretation': 'unsafe initial state before motion; not a controller-created collision'}
    destination = ROOT / 'reports/static_service_audit_20261026/pressure_v1_initial_state_diagnosis.json'
    write_json(destination, diagnostic)
    print(json.dumps({'preserved_source_files': len(manifest['sources']), 'pressure_initial_state': diagnostic}, indent=2))


if __name__ == '__main__':
    main()
