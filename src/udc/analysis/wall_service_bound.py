"""Evaluator-only necessary uncovered mass from known wall constraints.

No admissible guide centre can serve a point outside the axis-aligned box
obtained by expanding the room-centre rectangle by the service radius. This
uses a subset of the truly unreachable region (round corners omitted).
"""
import numpy as np

from udc.multigroup.service import _model, _union


def wall_uncovered_bound(boundary, density, room, wall_distance, service_radius):
    points, rho, lengths, total = _model(boundary, density)
    room = np.asarray(room, float)
    if (room.shape != (2,) or not np.isfinite(room).all() or not np.isfinite([wall_distance, service_radius]).all()
            or wall_distance < 0 or service_radius < 0 or np.any(room <= 2 * wall_distance)):
        raise ValueError('Finite nonempty guide-centre rectangle and nonnegative radius required')
    low, high = wall_distance - service_radius, room - wall_distance + service_radius
    mass, length_outside = 0., 0.
    for i, (point, length) in enumerate(zip(points, lengths)):
        direction = points[(i + 1) % len(points)] - point
        intervals = []
        for axis in (0, 1):
            # a + b*t < 0; boundary points have zero arc measure.
            for a, b in ((point[axis] - low, direction[axis]), (high[axis] - point[axis], -direction[axis])):
                if b == 0:
                    if a < 0:
                        intervals.append((0., 1.))
                elif b > 0:
                    intervals.append((0., min(1., -a / b)))
                else:
                    intervals.append((max(0., -a / b), 1.))
        for a, b in _union(intervals):
            mass += length * (rho[i] * (b - a) + (rho[(i + 1) % len(rho)] - rho[i]) * (b * b - a * a) / 2)
            length_outside += length * (b - a)
    return {'uncovered_lower_bound': float(mass / total), 'unreachable_arc_length_m': float(length_outside),
        'scope': 'original_polyline_piecewise_linear_density_evaluator_only',
        'geometric_reason': 'reference outside service-expanded guide-centre axis box; ignores additional people/guide restrictions',
        'runtime_uncertainty_certificate': False, 'formal_interval_proof': False}


def audit(original, output):
    import json
    from pathlib import Path
    from udc.deployment.planners import demand_profile
    from udc.multigroup.observation import StaticTruth
    from udc.multigroup.evaluation import reference_groups
    from udc.experiments.static_multigroup import digest, write_json
    from threadpoolctl import threadpool_limits
    original, output = Path(original), Path(output)
    read = lambda path: json.loads(path.read_text(encoding='utf-8'))
    manifest, results = read(original / 'manifest.json'), read(original / 'results.json')
    if digest(original / 'manifest.json') != read(original / 'seal.json')['manifest_sha256']:
        raise ValueError('Original controlled manifest changed')
    config, room = manifest['model'], manifest['spec']['room_m']
    rows, violations = [], []
    with threadpool_limits(limits=1):
        for unit in manifest['units']:
            path = original / unit['truth']
            if digest(path) != unit['truth_sha256']:
                raise ValueError('Original controlled truth changed')
            with np.load(path, allow_pickle=False) as data:
                truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
            groups = reference_groups(truth, room, config)
            bounds = [wall_uncovered_bound(group.boundary,
                demand_profile(group.boundary, truth.positions[list(group.rows)], truth.demand[list(group.rows)], config['bandwidth_m']),
                room, config['wall_distance_m'], config['max_gap_m'] / 2) for group in groups]
            actual = [r for r in results if r['unit_id'] == unit['unit_id']]
            margins = [g['uncovered'] - b['uncovered_lower_bound'] for r in actual
                       for g, b in zip(r['original_service']['groups'], bounds)]
            if min(margins) < 0:
                violations.append({'unit_id': unit['unit_id'], 'minimum_actual_minus_bound': min(margins)})
            rows.append({'unit_id': unit['unit_id'], 'wall_condition_m': unit['requested_left_wall_clearance_m'],
                'groups': bounds, 'max_group_unavoidable_uncovered': max(b['uncovered_lower_bound'] for b in bounds),
                'minimum_actual_minus_bound': min(margins)})
    summary = {'scene_conditions': len(rows), 'positive_wall_obstruction_scenes': sum(r['max_group_unavoidable_uncovered'] > 0 for r in rows),
        'by_wall_condition': {str(w): {'scenes': sum(r['wall_condition_m'] == w for r in rows),
            'positive_bound_scenes': sum(r['wall_condition_m'] == w and r['max_group_unavoidable_uncovered'] > 0 for r in rows),
            'minimum_max_group_bound': min(r['max_group_unavoidable_uncovered'] for r in rows if r['wall_condition_m'] == w),
            'maximum_max_group_bound': max(r['max_group_unavoidable_uncovered'] for r in rows if r['wall_condition_m'] == w)}
            for w in (None, .8, .02)}, 'violations': violations, 'all_observed_service_above_bound': not violations,
        'new_motion_executions': 0, 'new_independent_samples': 0, 'runtime_uncertainty_certificate': False,
        'scope': 'necessary geometric obstruction for original true reference, any number of wall-admissible guide centres; float64 integration'}
    write_json(output / 'rows.json', rows)
    write_json(output / 'summary.json', summary)
    if violations:
        raise ValueError('Wall necessary bound contradicted saved service; inspect full precision, no tolerance bypass')
    return summary
