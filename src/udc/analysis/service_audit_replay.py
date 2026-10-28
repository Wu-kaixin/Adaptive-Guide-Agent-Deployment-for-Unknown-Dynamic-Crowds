"""Offline re-evaluation of saved supplementary service and pressure evidence."""
import argparse
import json
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from udc.multigroup.observation import StaticTruth, BoundedMeasurement
from udc.interfaces import Observation
from udc.multigroup.geometry import construct_groups
from udc.multigroup.execution import Execution
from udc.multigroup.evaluation import reference_groups, evaluate
from udc.experiments.static_mechanism import continuous_summary
from udc.experiments.static_multigroup import write_json
from .service_audit_report import read, verify_study


def replay(study):
    study = Path(study).resolve()
    verified = verify_study(study, trajectories=True)
    manifest = read(study / 'manifest.json')
    spec, base = manifest['spec'], manifest['base_spec']
    truth_cache, reference_cache = {}, {}
    checked, mismatches = 0, []
    with threadpool_limits(limits=1):
        for folder in sorted((study / 'results').iterdir()):
            metrics, planning = read(folder / 'metrics.json'), read(folder / 'planning.json')
            if 'continuous_common_service' not in metrics and 'motion' not in metrics:
                continue
            truth_path = metrics['truth']
            if truth_path not in truth_cache:
                with np.load(study / truth_path, allow_pickle=False) as data:
                    truth_cache[truth_path] = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            truth = truth_cache[truth_path]
            with np.load(folder / 'trajectory.npz', allow_pickle=False) as trajectory:
                if 'continuous_common_service' in metrics:
                    if truth_path not in reference_cache:
                        reference_cache[truth_path] = reference_groups(truth, base['room_m'], base['model'])
                    fresh = continuous_summary(reference_cache[truth_path], truth, trajectory['targets'], base['model'])
                    same = fresh == metrics['continuous_common_service']
                else:
                    with np.load(study / metrics['observation'], allow_pickle=False) as obs:
                        measurement = BoundedMeasurement(Observation(obs['positions'], obs['radii'], obs['demand']),
                                                         spec['error_bound_m'], spec['measurement_model'])
                    groups, _ = construct_groups(measurement, base['room_m'], base['model'])
                    state = read(folder / 'state.json')
                    execution = Execution(trajectory['positions'], trajectory['velocities'], trajectory['assignment'],
                                          state['execution_status'], (), state['evidence'])
                    fresh = evaluate(execution, trajectory['targets'], trajectory['target_groups'], groups,
                                     measurement, truth, base['room_m'], base['model'])
                    same = all(fresh[key] == metrics[key] for key in fresh)
            checked += 1
            if not same:
                mismatches.append(metrics['task_id'])
    result = verified | {'recomputed_rows': checked, 'mismatches': mismatches, 'all_match': not mismatches,
                         'mode': 'offline saved-action re-evaluation; no scientific retry'}
    write_json(study / 'offline_replay.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.study), indent=2))
