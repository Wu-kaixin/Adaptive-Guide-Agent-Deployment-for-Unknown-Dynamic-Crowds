"""Handwritten public-session development fixture, separately truth-audited."""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import yaml
from threadpoolctl import threadpool_limits

from .static_multigroup import ROOT, scientific_sources, environment_record, write_json, digest
from udc.interfaces import Observation
from udc.multigroup.observation import StaticTruth, BoundedMeasurement
from udc.multigroup.foundation_contract import ObservationFrame
from udc.multigroup.session import StaticDeploymentSession
from udc.analysis.session_audit import audit_journal


def run(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = scientific_sources()
    config = yaml.safe_load((ROOT / 'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))['model']
    for name in sources:
        path = output / 'source_snapshot' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    manifest = {'evidence': 'handwritten_fixture_development_only', 'new_independent_samples': 0,
        'sources': sources, 'environment': environment_record(), 'model': config,
        'fixture': {'person_ids': [42, 7], 'positions': [[10., 10.], [22., 10.]],
                    'radii': [.2, .2], 'demand': [1., 2.], 'guides': [[2., 2.], [24., 2.]],
                    'error_bound_m': .04, 'frames': ['initial', 'row_permuted_update', 'deliberately_stale']}}
    write_json(output / 'manifest.json', manifest)
    write_json(output / 'seal.json', {'manifest_sha256': digest(output / 'manifest.json')})
    fixture = manifest['fixture']
    truth = StaticTruth(fixture['positions'], fixture['radii'], fixture['demand'], [0, 1])
    controller = StaticDeploymentSession(fixture['guides'], [40, 30], config, output / 'journal',
        provenance={'manifest_sha256': digest(output / 'manifest.json'), 'snapshot': '../source_snapshot'})
    with threadpool_limits(limits=1):
        first = BoundedMeasurement(Observation(truth.positions, truth.radii, truth.demand), .04, 'handwritten_bounded')
        controller.step(ObservationFrame('initial', 0., tuple(fixture['person_ids']), first))
        changed = truth.positions + np.array([[.001, 0.], [0., -.001]])
        second = BoundedMeasurement(Observation(changed[::-1], truth.radii[::-1], truth.demand[::-1]), .04, 'handwritten_bounded')
        controller.step(ObservationFrame('row_permuted_update', controller.next_observation_time_s,
                                        tuple(fixture['person_ids'][::-1]), second))
        controller.step(ObservationFrame('deliberately_stale', 0., tuple(fixture['person_ids']), first))
        result = audit_journal(output / 'journal', truth, fixture['person_ids'])
    write_json(output / 'physical_audit.json', result)
    if not result['all_match'] or result['statuses'] != {'DELIVERED_OBSERVATION_PLAN': 2, 'OBSERVATION_RELEASE_VIOLATION': 1}:
        raise RuntimeError('Session fixture failed its declared accounting and physical audit')
    return {k: v for k, v in result.items() if k != 'rows'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False), flush=True)
