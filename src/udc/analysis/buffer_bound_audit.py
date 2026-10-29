"""Source-aware audit of a generic round-buffer inner-radius premise.

This does not change any geometry or acceptance tolerance. A generic polygon
counterexample does not automatically refute the repository's restricted input
family; scan that family separately and preserve the distinction.
"""
from fractions import Fraction as F
import json
import math
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon, Point
from threadpoolctl import threadpool_limits

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement, StaticTruth
from udc.multigroup.geometry import construct_groups
from udc.multigroup.evaluation import reference_groups
from udc.experiments.static_multigroup import digest, write_json
from udc.experiments.frozen_resume import atomic_json


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _f(value):
    return F.from_float(float(value))


def exact_ring_covers(coords, point):
    """Exact ray test for the supplied binary-coordinate polygon ring."""
    ring = [tuple(_f(v) for v in p) for p in coords]
    if ring[0] == ring[-1]:
        ring.pop()
    x, y = map(_f, point)
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        cross = (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
        if cross == 0 and min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1):
            return True
        if (y0 > y) != (y1 > y):
            intersection = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if intersection > x:
                inside = not inside
    return inside


def convex_turns(polygon):
    points = np.asarray(polygon.exterior.coords[:-1], float)
    signed = np.sum(points[:, 0] * np.roll(points[:, 1], -1) - points[:, 1] * np.roll(points[:, 0], -1))
    if signed < 0:
        points = points[::-1]
    first, second = points - np.roll(points, 1, axis=0), np.roll(points, -1, axis=0) - points
    angles = np.arctan2(first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0], np.sum(first * second, axis=1))
    return points, first, angles


def corner_witness(source, buffered, radius, quad_segs=16):
    points, incoming, angles = convex_turns(source)
    quantum = math.pi / (2 * quad_segs)
    claimed = radius * math.cos(quantum / 2)
    for index in np.flatnonzero(angles > 1.01 * quantum):
        angle = float(angles[index])
        segments = int(angle / quantum + .5)
        if segments < 1:
            continue
        increment = angle / segments
        if increment <= 1.01 * quantum:
            continue
        direction = incoming[index]
        start = math.atan2(-direction[0], direction[1])
        chord_radius = radius * math.cos(increment / 2)
        probe_radius = (chord_radius + claimed) / 2
        for segment in range(segments):
            theta = start + (segment + .5) * increment
            probe = points[index] + probe_radius * np.array([math.cos(theta), math.sin(theta)])
            squared = sum((_f(a) - _f(b)) ** 2 for a, b in zip(probe, points[index]))
            inside_claimed = squared < _f(claimed) ** 2
            outside_actual = not exact_ring_covers(buffered.exterior.coords, probe)
            if inside_claimed and outside_actual and not buffered.interiors:
                return {'source_vertex': points[index].tolist(), 'source_vertex_index': int(index),
                    'turn_angle_rad': angle, 'nominal_quantum_rad': quantum, 'fillet_segments': segments,
                    'actual_increment_rad': increment, 'claimed_inner_radius_m': claimed,
                    'local_chord_radius_m': chord_radius, 'point': probe.tolist(),
                    'exact_inside_claimed_disk': True, 'exact_outside_buffer_polygon': True,
                    'squared_point_vertex_distance_fraction': str(squared),
                    'claimed_squared_radius_fraction': str(_f(claimed) ** 2),
                    'shapely_covers_point': bool(buffered.covers(Point(probe))),
                    'distance_outside_actual_buffer_m': float(buffered.distance(Point(probe)))}
    return None


def generic_example():
    angle = 1.49 * math.pi / 32
    polygon = Polygon([(10., 10.), (20., 10.), (20. + 10 * math.cos(angle), 10. + 10 * math.sin(angle)), (10., 20.)])
    buffered = polygon.buffer(1., quad_segs=16)
    return polygon, buffered, corner_witness(polygon, buffered, 1.)


def scan_groups(groups, label, config, output, records):
    for group in groups:
        source = group.occupied
        points, _, angles = convex_turns(source)
        ratio = float(np.max(angles) / (math.pi / 32))
        record = {'input': label, 'group': group.group_id, 'vertices': len(points),
            'maximum_positive_turn_over_nominal_quantum': ratio, 'actual_source_family_witness': None}
        if np.any(angles > 1.01 * math.pi / 32):
            buffered = source.buffer(config['offset_m'], quad_segs=16)
            witness = corner_witness(source, buffered, config['offset_m'])
            record['actual_source_family_witness'] = witness
            if witness:
                name = f'witness_{len(records):06d}.npz'
                np.savez_compressed(output / name, source=np.asarray(source.exterior.coords),
                    buffered=np.asarray(buffered.exterior.coords), point=np.asarray(witness['point']))
                record['witness_geometry'] = name
                record['witness_geometry_sha256'] = digest(output / name)
        records.append(record)


def audit(original, output):
    original, output = Path(original), Path(output)
    old = read(original / 'manifest.json')
    if digest(original / 'manifest.json') != read(original / 'seal.json')['manifest_sha256']:
        raise ValueError('Original buffer-audit manifest changed')
    config, room = old['base_spec']['model'], old['base_spec']['room_m']
    polygon, buffered, witness = generic_example()
    np.savez_compressed(output / 'generic_witness.npz', source=np.asarray(polygon.exterior.coords),
        buffered=np.asarray(buffered.exterior.coords), point=np.asarray(witness['point']) if witness else np.empty(2))
    write_json(output / 'generic_witness.json', {'witness': witness, 'shapely': shapely.__version__, 'geos': shapely.geos_version_string})
    records, truths, frame_count = [], set(), 0
    with threadpool_limits(limits=1):
        for number, sequence in enumerate(old['sequences']):
            if sequence['truth_sha256'] not in truths:
                path = original / sequence['truth']
                if digest(path) != sequence['truth_sha256']:
                    raise ValueError('Old truth changed')
                with np.load(path, allow_pickle=False) as data:
                    truth = StaticTruth(data['positions'], data['radii'], data['demand'], data['group_ids'])
                scan_groups(reference_groups(truth, room, config), sequence['sequence_id'] + ':true', config, output, records)
                truths.add(sequence['truth_sha256'])
            for frame in sequence['frames']:
                path = original / frame['observation']
                if digest(path) != frame['observation_sha256']:
                    raise ValueError('Old observation changed')
                with np.load(path, allow_pickle=False) as data:
                    measurement = BoundedMeasurement(Observation(data['positions'], data['radii'], data['demand']),
                        old['spec']['declared_error_bound_m'], 'old_bounded')
                groups, _ = construct_groups(measurement, room, config)
                scan_groups(groups, sequence['sequence_id'] + ':' + str(frame['perturbation_m']), config, output, records)
                frame_count += 1
            atomic_json(output / 'status.json', {'phase': 'running', 'completed_sequences': number + 1,
                'scheduled_sequences': len(old['sequences']), 'groups_scanned': len(records)})
    summary = {'generic_polygon_inner_bound_refuted': witness is not None,
        'true_physical_inputs': len(truths), 'old_observation_frames': frame_count, 'groups_scanned': len(records),
        'maximum_source_turn_ratio': max(r['maximum_positive_turn_over_nominal_quantum'] for r in records),
        'groups_above_1_01_nominal_turn': sum(r['maximum_positive_turn_over_nominal_quantum'] > 1.01 for r in records),
        'actual_source_family_witnesses': sum(r['actual_source_family_witness'] is not None for r in records),
        'new_motion_executions': 0, 'new_independent_samples': 0,
        'scope': 'generic counterexample and finite original occupied-polygon family scan; no global bound repaired or certificate validated',
        'restricted_family_proof_from_no_witness': False, 'controller_thresholds_changed': False}
    write_json(output / 'records.json', records)
    write_json(output / 'summary.json', summary)
    atomic_json(output / 'status.json', {'phase': 'completed', **summary})
    return summary
