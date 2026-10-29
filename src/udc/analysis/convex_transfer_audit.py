"""Exact finite-direction tests of ideal support lemmas on saved GEOS stages.

These are proof-premise diagnostics, never runtime certificates. A failed test
refutes the proposed stage inclusion. Passing finitely many inputs/directions
cannot establish the universal inclusion, and must not unquarantine a guard.
"""
from fractions import Fraction as F
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.geometry import construct_groups, outer_disk
from udc.multigroup.observation import BoundedMeasurement
from udc.experiments.static_multigroup import digest, write_json


DIRECTIONS = ((1, 0), (2, 1), (1, 1), (1, 2), (0, 1), (-1, 2), (-1, 1), (-2, 1),
              (-1, 0), (-2, -1), (-1, -1), (-1, -2), (0, -1), (1, -2), (1, -1), (2, -1))


def read(path):
    import json
    return json.loads(Path(path).read_text(encoding='utf-8'))


def checked(path, checksum):
    if digest(path) != checksum:
        raise ValueError('Frozen support-transfer artifact changed: ' + str(path))


def vertices(geometry):
    """Include every exterior/interior vertex, including multiple components."""
    if geometry.is_empty:
        return np.empty((0, 2))
    if hasattr(geometry, 'geoms'):
        return np.concatenate([vertices(part) for part in geometry.geoms])
    if geometry.geom_type != 'Polygon':
        raise ValueError('Unexpected geometry in support-stage audit')
    return np.concatenate([np.asarray(ring.coords[:-1], float)
                           for ring in [geometry.exterior, *geometry.interiors]])


def exact_supports(points, directions=DIRECTIONS):
    """Return exact support of finite binary coordinates, with witness indices.

    A common power-of-two denominator makes integer dot products inexpensive;
    no floating argmax or numerical tolerance participates in a decision.
    """
    points = np.asarray(points, float)
    if points.ndim != 2 or points.shape[1] != 2 or not len(points) or not np.isfinite(points).all():
        raise ValueError('Finite nonempty two-dimensional support input required')
    ratios = [[float(v).as_integer_ratio() for v in row] for row in points]
    denominator = max(d for row in ratios for _, d in row)
    integer = [(row[0][0] * (denominator // row[0][1]),
                row[1][0] * (denominator // row[1][1])) for row in ratios]
    result = []
    for a, b in directions:
        scores = [a * x + b * y for x, y in integer]
        index = max(range(len(scores)), key=scores.__getitem__)
        result.append((F(scores[index], denominator), index))
    return result


def support_violations(source, target, radius=0., directions=DIRECTIONS):
    """Test target support <= source support + r*norm(direction), exactly.

    If the rational excess is positive, square it against the rational radius
    squared times the integer direction norm squared. No approximate sqrt.
    """
    radius = F.from_float(float(radius))
    if radius < 0:
        raise ValueError('Nonnegative dilation radius required')
    before, after = exact_supports(source, directions), exact_supports(target, directions)
    failures = []
    for direction, (h0, i0), (h1, i1) in zip(directions, before, after):
        excess = h1 - h0
        limit_squared = radius * radius * sum(v * v for v in direction)
        if excess > 0 and excess * excess > limit_squared:
            failures.append({'direction': list(direction), 'source_vertex_index': i0, 'target_vertex_index': i1,
                'source_support_fraction': str(h0), 'target_support_fraction': str(h1),
                'excess_fraction': str(excess), 'excess_float': float(excess),
                'excess_squared_fraction': str(excess * excess), 'allowed_squared_fraction': str(limit_squared),
                'radius_m': float(radius), 'exact_violation': True})
    return failures


def scan_input(study, task):
    """Stop this input at its first refuted stage and preserve stage geometries."""
    study = Path(study)
    manifest = read(study / 'manifest.json')
    original = Path(manifest['original'])
    old = read(original / 'manifest.json')
    config, room = old['base_spec']['model'], old['base_spec']['room_m']
    checked(original / task['input'], task['input_sha256'])
    with np.load(original / task['input'], allow_pickle=False) as data:
        observation = Observation(data['positions'].copy(), data['radii'].copy(), data['demand'].copy())
    measurement = BoundedMeasurement(observation, task['error_bound_m'], 'saved_support_transfer_development')
    folder = study / 'inputs' / task['task_id']
    folder.mkdir(parents=True, exist_ok=False)
    records, group_count, refuted = [], 0, False
    with threadpool_limits(limits=1):
        groups, _ = construct_groups(measurement, room, config)
        for group in groups:
            disks = unary_union([outer_disk(observation.positions[i], observation.radii[i]
                + measurement.position_error_bound_m + config['construction_margin_m']) for i in group.rows])
            positive = disks.buffer(config['closing_radius_m'], quad_segs=16)
            closed = positive.buffer(-config['closing_radius_m'], quad_segs=16)
            occupied = orient(Polygon(unary_union([disks, closed]).exterior), sign=1)
            deployment = orient(occupied.buffer(config['offset_m'], quad_segs=16), sign=1)
            if occupied.wkb != group.occupied.wkb:
                raise ValueError('Stage reconstruction differs from the declared pipeline')
            stages = {'disks': vertices(disks), 'closing_positive': vertices(positive),
                'closed': vertices(closed), 'occupied': vertices(occupied),
                'offset': vertices(deployment), 'reference': group.boundary.deployment}
            filename = f'group_{group.group_id:03d}.npz'
            np.savez_compressed(folder / filename, **stages)
            tests = [('closed_in_input_hull', 'disks', 'closed', 0.),
                ('filled_occupied_in_input_hull', 'disks', 'occupied', 0.),
                ('offset_in_occupied_dilation', 'occupied', 'offset', config['offset_m']),
                ('sampled_curve_in_offset_hull', 'offset', 'reference', 0.)]
            for name, source, target, radius in tests:
                envelope_record = None
                directions = DIRECTIONS
                hull_certificate = None
                if manifest['design'].get('direction_mode') == 'all_source_hull_facets':
                    from .exact_convex_hull import facet_directions
                    directions,indices = facet_directions(stages[source],include_axes=(source=='offset'))
                    hull_certificate = {'source_hull_vertex_indices':list(indices),'axes_added':source=='offset'}
                if name == 'sampled_curve_in_offset_hull' and manifest['design'].get('interpolation_enclosure') == 'rational_binary64_v1':
                    from udc.multigroup.interpolation_enclosure import support_envelope
                    envelope = support_envelope(stages[source], directions)
                    violations = []
                    for direction, upper, (score, vertex) in zip(directions, envelope.upper_supports, exact_supports(stages[target],directions)):
                        if score > upper:
                            violations.append({'direction': list(direction), 'target_vertex_index': vertex,
                                'target_support_fraction': str(score), 'upper_support_fraction': str(upper),
                                'excess_fraction': str(score-upper), 'exact_violation': True})
                    name = 'sampled_curve_in_binary64_interpolation_support'
                    envelope_record = {'coordinate_error_fractions': [str(v) for v in envelope.coordinate_errors],
                        'upper_support_fractions': [str(v) for v in envelope.upper_supports], 'contract': envelope.contract}
                else:
                    violations = support_violations(stages[source], stages[target], radius,directions)
                records.append({'group': group.group_id, 'test': name, 'source': source, 'target': target,
                    'radius_m': radius, 'directions_checked': len(directions), 'directions':[list(n) for n in directions],
                    'hull_certificate':hull_certificate,'violations': violations,
                    'stage_geometry': filename, 'stage_geometry_sha256': digest(folder / filename),
                    'interpolation_enclosure': envelope_record})
                if violations:
                    refuted = True
                    break
            group_count += 1
            if refuted:
                break
    result = {'task_id': task['task_id'], 'source': task, 'groups_scheduled': len(groups),
        'groups_checked': group_count, 'records': records,
        'proposed_enclosure_refuted': refuted, 'stopped_on_first_refutation': refuted,
        'controller_model_bound_claimed': False, 'runtime_certificate_restored': False}
    write_json(folder / 'result.json', result)
    write_json(folder / 'receipt.json', {p.name: digest(p) for p in folder.iterdir()
        if p.name != 'receipt.json' and p.is_file()})
    return result
