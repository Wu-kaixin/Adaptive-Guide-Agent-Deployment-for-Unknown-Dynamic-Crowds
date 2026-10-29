"""Reproduce a saved support witness through GEOS's segment arithmetic."""
from fractions import Fraction as F
import math
from pathlib import Path

import numpy as np
from shapely.geometry import LinearRing

from .convex_transfer_audit import read, checked
from udc.experiments.static_multigroup import digest, write_json
from udc.multigroup.interpolation_enclosure import support_envelope


def diagnose(study, output):
    study, output = Path(study), Path(output)
    manifest = read(study / 'manifest.json')
    checked(study / 'manifest.json', read(study / 'seal.json')['manifest_sha256'])
    rows = read(study / 'results.json')
    witnesses = [(r, t, v) for r in rows for t in r['records'] for v in t['violations']]
    if len(witnesses) != 1 or witnesses[0][1]['test'] != 'sampled_curve_in_offset_hull':
        raise ValueError('This diagnosis requires the single preserved interpolation witness')
    row, test, witness = witnesses[0]
    geometry = study / 'inputs' / row['task_id'] / test['stage_geometry']
    checked(geometry, test['stage_geometry_sha256'])
    with np.load(geometry, allow_pickle=False) as data:
        vertices, reference = data['offset'].copy(), data['reference'].copy()
    index = witness['target_vertex_index']
    requested = index * LinearRing(vertices).length / len(reference)
    total, found = 0., None
    for segment, (a, b) in enumerate(zip(vertices, np.roll(vertices, -1, axis=0))):
        dx, dy = float(b[0])-float(a[0]), float(b[1])-float(a[1])
        length = math.sqrt(dx*dx+dy*dy)
        if total+length > requested:
            fraction = (requested-total)/length
            expected = np.array([(float(b[j])-float(a[j]))*fraction+float(a[j]) for j in range(2)])
            if not np.array_equal(expected, reference[index]):
                raise ValueError('GEOS segment selection/interpolation not reproduced bitwise')
            f = lambda v: F.from_float(float(v))
            direction = witness['direction']
            support = lambda p: sum(n*f(v) for n,v in zip(direction,p))
            coordinate_trace = []
            for j in range(2):
                ah,bh,t = f(a[j]),f(b[j]),f(fraction)
                dh = f(float(b[j])-float(a[j]))
                ph = f((float(b[j])-float(a[j]))*fraction)
                qh = f(expected[j])
                coordinate_trace.append({'exact_endpoint_difference': str(bh-ah),
                    'rounded_difference': str(dh), 'subtraction_rounding_error': str(dh-(bh-ah)),
                    'multiplication_rounding_error': str(ph-dh*t),
                    'addition_rounding_error': str(qh-(ph+ah)),
                    'total_error_relative_to_exact_convex_combination': str(qh-(ah+(bh-ah)*t))})
            envelope = support_envelope(vertices, (tuple(direction),))
            found = {'segment_index': segment, 'reference_index': index, 'reference_count': len(reference),
                'endpoint_a': a.tolist(), 'endpoint_b': b.tolist(), 'saved_point': reference[index].tolist(),
                'fraction': fraction, 'fraction_hex': fraction.hex(), 'fraction_exact': str(f(fraction)),
                'requested_distance': requested, 'prior_accumulated_distance': total, 'segment_length': length,
                'bitwise_interpolation_reproduction': True, 'direction': direction,
                'endpoint_a_support': str(support(a)), 'endpoint_b_support': str(support(b)),
                'point_support': str(support(expected)),
                'excess_over_global_offset_support': witness['excess_fraction'],
                'coordinate_arithmetic_trace': coordinate_trace,
                'revised_coordinate_error_fractions': [str(v) for v in envelope.coordinate_errors],
                'revised_upper_support_fraction': str(envelope.upper_supports[0]),
                'saved_witness_inside_revised_envelope': support(expected) <= envelope.upper_supports[0]}
            break
        total += length
    if found is None:
        raise ValueError('Saved witness has no matching interpolated segment')
    found.update(source_geometry=str(geometry), source_geometry_sha256=digest(geometry),
        source_manifest_sha256=digest(study/'manifest.json'), new_motion_executions=0,
        source_references=['https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/linearref/LinearLocation.cpp',
            'https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/linearref/LengthLocationMap.cpp',
            'https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/algorithm/Length.cpp'],
        scope='exact binary-coordinate witness and arithmetic reproduction; revised conditional interpolation bound only')
    if output.exists():
        raise FileExistsError(output)
    write_json(output, found)
    return found
