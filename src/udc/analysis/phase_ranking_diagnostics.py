"""Reconstruct finite phase rankings in the saved millimetre perturbation pair."""
import json

import numpy as np
from shapely.geometry import Point

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.geometry import construct_groups
from udc.multigroup.allocation import _lloyd_sites, service_statistics
from udc.multigroup.service import continuous_arc_service
from udc.deployment.planners import demand_profile, quadrature_mass, _interpolate
from udc.experiments.static_multigroup import ROOT, write_json


def main():
    study = ROOT / 'runs/static_service_audit_20261026/stability_v1'
    destination = ROOT / 'reports/static_service_audit_20261026'
    pair = json.loads((destination / 'target_jump_diagnosis.json').read_text(encoding='utf-8'))['pair']
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    config = manifest['base_spec']['model']
    result = []
    for item in pair:
        unit = next(u for u in manifest['units'] if u['unit_id'] == item['task_id'])
        with np.load(study / unit['observation'], allow_pickle=False) as obs:
            measurement = BoundedMeasurement(Observation(obs['positions'], obs['radii'], obs['demand']), .12, unit['measurement_model'])
        groups, domain = construct_groups(measurement, manifest['base_spec']['room_m'], config)
        for group, count, expected_phase in zip(groups, item['counts'], item['selected_phases']):
            index = np.asarray(group.rows)
            density = demand_profile(group.boundary, measurement.observation.positions[index],
                                     measurement.observation.demand[index], config['bandwidth_m'])
            quadrature, mass, length = quadrature_mass(group.boundary, density, config['planning_samples'])
            phases = []
            for phase in range(config['phase_bins']):
                sites = _lloyd_sites(group.boundary, quadrature, mass, count, phase / config['phase_bins'], config)
                targets = _interpolate(group.boundary, sites)
                separation = np.linalg.norm(targets[:, None] - targets[None], axis=2)
                np.fill_diagonal(separation, np.inf)
                valid = bool(all(domain.free.covers(Point(p)) for p in targets) and separation.min() >= config['guide_distance_m'])
                uncovered, cost, gap = service_statistics(quadrature, mass, sites, length, config['max_gap_m'] / 2)
                phases.append({'phase': phase, 'admissible': valid, 'uncovered_256': uncovered,
                               'normalized_cost': cost / length ** 2,
                               'continuous_arc_uncovered': continuous_arc_service(group.boundary, density, sites, config['max_gap_m'] / 2)['uncovered']})
            ranked = sorted((p for p in phases if p['admissible']), key=lambda p: (p['uncovered_256'], p['normalized_cost'], p['phase']))
            if ranked[0]['phase'] != expected_phase:
                raise ValueError('Reconstructed phase winner differs')
            result.append({'task_id': item['task_id'], 'group_id': group.group_id, 'selected_phase': expected_phase,
                           'second_minus_winner_U_margin': ranked[1]['uncovered_256'] - ranked[0]['uncovered_256'],
                           'ranked_phases': ranked})
    write_json(destination / 'phase_ranking_diagnosis.json', {'rows': result,
               'interpretation': 'fixed-count reconstruction of the original 256-node finite-phase decision; descriptive diagnosis'})
    print(json.dumps([{k: v for k, v in r.items() if k != 'ranked_phases'} for r in result], indent=2))


if __name__ == '__main__':
    main()
