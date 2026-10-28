"""Offline validation of the selected fixed-action execution counterexample."""
import argparse
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import StaticTruth, BoundedMeasurement
from udc.multigroup.geometry import construct_groups
from udc.multigroup.execution import Execution
from udc.multigroup.evaluation import evaluate, reference_groups
from udc.experiments.static_mechanism import continuous_summary
from udc.experiments.static_multigroup import digest, write_json
from .service_audit_report import read, verify_study


def replay(study):
    study = Path(study).resolve()
    verified = verify_study(study, trajectories=True)
    manifest = read(study / 'manifest.json')
    base = manifest['base_spec']
    mismatches, starts = [], []
    with threadpool_limits(limits=1):
        for task in manifest['units']:
            if digest(study / task['actions']) != task['actions_sha256']:
                raise ValueError('Witness fixed actions changed')
            with np.load(study / task['truth'], allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
                initial = data['guides'].copy()
            with np.load(study / task['observation'], allow_pickle=False) as data:
                obs = BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                                         task['error_bound_m'], 'saved_witness')
            folder = study / 'results' / task['task_id']
            saved, state = read(folder / 'metrics.json'), read(folder / 'state.json')
            groups, _ = construct_groups(obs, base['room_m'], base['model'])
            with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                execution = Execution(data['positions'], data['velocities'], data['assignment'], state['execution_status'], (), state['evidence'])
                fresh = evaluate(execution, data['targets'], data['target_groups'], groups, obs, truth, base['room_m'], base['model'])
                actual = continuous_summary(reference_groups(truth, base['room_m'], base['model']), truth,
                                            execution.positions[-1][execution.assignment >= 0], base['model'])
                same = all(fresh[k] == saved[k] for k in fresh) and actual == saved['continuous_actual_service']
                same = same and np.array_equal(data['positions'][0], initial)
                starts.append(data['positions'][0].copy())
            if not same:
                mismatches.append(task['task_id'])
    result = verified | {'recomputed_rows': len(manifest['units']), 'mismatches': mismatches,
                         'identical_initial_robot_states': all(np.array_equal(s, starts[0]) for s in starts),
                         'all_match': not mismatches, 'mode': 'offline interval and service validation, no scientific retry'}
    write_json(study / 'offline_replay.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.study), indent=2))
