"""Read-only saved-trajectory replay using the original frozen evaluator."""
import argparse
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--unit', required=True)
    parser.add_argument('--cpu', required=True, type=int)
    args = parser.parse_args()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    import psutil
    psutil.Process().cpu_affinity([args.cpu])
    study = Path(args.study).resolve()
    sys.path.insert(0, str(study / 'source_snapshot/src'))
    import numpy as np
    from udc.experiments import static_multigroup as frozen
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    tasks = [task for task in manifest['tasks'] if task['unit_id'] == args.unit]
    if not tasks:
        raise ValueError('Replay unit outside frozen manifest')
    mismatches, checked, verified_receipts = [], 0, 0
    for task in tasks:
        folder = study / 'results' / task['task_id']
        if not (folder / 'receipt.json').is_file():
            continue
        receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
        if any(frozen.digest(folder / name) != sha for name, sha in receipt.items()):
            raise ValueError('Saved task changed: ' + task['task_id'])
        verified_receipts += 1
        metrics = json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))
        if 'motion' not in metrics:
            continue
        truth, _, measurement = frozen._load_inputs(study, task)
        groups, _ = frozen.construct_groups(measurement, manifest['spec']['room_m'], manifest['spec']['model'])
        state = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
        with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
            execution = frozen.Execution(data['positions'], data['velocities'], data['assignment'],
                                         state['execution_status'], (), state['evidence'])
            fresh = frozen.evaluate(execution, data['targets'], data['target_groups'], groups, measurement,
                                    truth, manifest['spec']['room_m'], manifest['spec']['model'])
        checked += 1
        if any(fresh[key] != metrics[key] for key in fresh):
            mismatches.append(task['task_id'])
    print(json.dumps({'unit_id': args.unit, 'checked': checked, 'verified_receipts': verified_receipts,
                      'mismatches': mismatches, 'scientific_source': str(Path(frozen.__file__).resolve())}), flush=True)
