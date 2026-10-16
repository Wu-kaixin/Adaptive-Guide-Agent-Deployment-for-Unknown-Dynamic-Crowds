"""Prescribed closed curves for diagnostic planning.

These curves are inputs. They are not estimated from pedestrian positions.
A diagnostic runner must label them as such. ``estimate_boundary`` never
reads this module, and ordinary Step 1 configs have no field that points here.
"""
from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, Polygon

from .estimate import Boundary


def polyline_length(points):
    points = np.asarray(points, dtype=float)
    return float(np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1).sum())


def scale_to_length(points, target_length):
    """Uniform scale about the vertex centroid. Polyline length scales exactly."""
    points = np.asarray(points, dtype=float)
    length = polyline_length(points)
    if not np.isfinite(length) or length <= 0 or not np.isfinite(target_length) or target_length <= 0:
        raise ValueError('CURVE_INVALID: length scale needs a positive finite length')
    center = points.mean(axis=0)
    scale = float(target_length / length)
    return center + (points - center) * scale, scale, length


def resample_arclength(points, spacing):
    """Uniform arc-length samples on a closed ring. The first point is not repeated."""
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3:
        raise ValueError('CURVE_INVALID: need a polyline of at least three points')
    segment = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    if np.any(segment <= 0) or not np.isfinite(segment).all():
        raise ValueError('CURVE_INVALID: resampling hit a degenerate segment')
    length = float(segment.sum())
    count = max(32, int(round(length / spacing)))
    cumulative = np.r_[0.0, np.cumsum(segment)]
    targets = np.arange(count) * length / count
    index = np.searchsorted(cumulative, targets, side='right') - 1
    index = np.clip(index, 0, len(points) - 1)
    local = (targets - cumulative[index]) / segment[index]
    nxt = (index + 1) % len(points)
    return points[index] + local[:, None] * (points[nxt] - points[index])


def boundary_from_ring(points):
    points = np.asarray(points, dtype=float)
    segment = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    arc = np.r_[0.0, np.cumsum(segment[:-1])]
    ring = np.asarray(points, dtype=float)
    return Boundary(ring.copy(), ring.copy(), arc, float(segment.sum()))


def _append_line(store, start, end, spacing):
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    distance = float(np.linalg.norm(end - start))
    if distance <= 1e-12:
        return
    count = max(1, int(math.ceil(distance / spacing)))
    for i in range(count):
        store.append(start + (end - start) * (i / count))


def _append_arc(store, center, radius, angle0, angle1, spacing):
    center = np.asarray(center, dtype=float)
    sweep = float(angle1 - angle0)
    arc_length = abs(sweep) * float(radius)
    if arc_length <= 1e-12:
        return
    count = max(2, int(math.ceil(arc_length / spacing)))
    for i in range(count):
        angle = angle0 + sweep * (i / count)
        store.append(center + radius * np.array([math.cos(angle), math.sin(angle)]))


def stadium_ring(radius, target_length, spacing):
    """Capsule of analytic perimeter ``target_length`` and cap radius ``radius``.

    The straight length is ``target_length - 2*pi*radius``. Sampling then a
    uniform length scale removes the chord deficit of the arcs. The scale is
    recorded by the caller; this function returns the scaled ring.
    """
    if radius <= 0 or 2 * math.pi * radius >= target_length:
        raise ValueError('CURVE_INVALID: stadium radius does not leave a positive straight segment')
    # ``one_straight`` is the length of each of the two straight sides.
    one_straight = (target_length - 2 * math.pi * radius) / 2
    half = one_straight / 2
    raw = []
    _append_arc(raw, (half, 0.0), radius, -math.pi / 2, math.pi / 2, spacing)
    _append_line(raw, (half, radius), (-half, radius), spacing)
    _append_arc(raw, (-half, 0.0), radius, math.pi / 2, 3 * math.pi / 2, spacing)
    _append_line(raw, (-half, -radius), (half, -radius), spacing)
    scaled, scale, raw_length = scale_to_length(np.asarray(raw), target_length)
    return scaled, {
        'family': 'high_curvature_convex',
        'construction': 'stadium',
        'requested_cap_radius_m': float(radius),
        'analytic_straight_m': float(one_straight),
        'raw_length_m': raw_length,
        'length_scale': scale,
    }


def radial_dent_ring(depth, sigma, target_length, spacing, base_radius=5.0):
    """Circle with one smooth inward dent centered on the positive x-axis.

    ``depth`` is the fractional radius removed at the dent centre. ``sigma`` is
    the Gaussian angular width in radians. The ring is then scaled to
    ``target_length``. No vertices are inserted or deleted to force simplicity.
    """
    if not 0 < depth < 0.95 or sigma <= 0 or base_radius <= 0:
        raise ValueError('CURVE_INVALID: dent depth, width and base radius must be positive')
    dense = max(720, int(math.ceil(2 * math.pi * base_radius / spacing)))
    theta = np.linspace(0.0, 2 * math.pi, dense, endpoint=False)
    angle = (theta + math.pi) % (2 * math.pi) - math.pi
    radius = base_radius * (1.0 - depth * np.exp(-0.5 * (angle / sigma) ** 2))
    if np.any(radius <= 0):
        raise ValueError('CURVE_INVALID: dent radius became nonpositive')
    points = np.column_stack([radius * np.cos(theta), radius * np.sin(theta)])
    scaled, scale, raw_length = scale_to_length(points, target_length)
    return scaled, {
        'family': 'shallow_concave',
        'construction': 'radial_gaussian_dent',
        'depth_fraction': float(depth),
        'sigma_rad': float(sigma),
        'base_radius_m': float(base_radius),
        'raw_length_m': raw_length,
        'length_scale': scale,
    }


def _fillet_ring(vertices, radius, arc_samples):
    """Fillet every corner that has room for ``radius``. Sharp corners stay.

    Rejected corners are listed. The polygon is not replaced by a buffer, so a
    self-intersection created by the fillet is left for the validity check.
    """
    vertices = [np.asarray(v, dtype=float) for v in vertices]
    count = len(vertices)
    samples = []
    rejected = []
    for index in range(count):
        previous = vertices[(index - 1) % count]
        current = vertices[index]
        nxt = vertices[(index + 1) % count]
        back = previous - current
        forward = nxt - current
        length_back = float(np.linalg.norm(back))
        length_forward = float(np.linalg.norm(forward))
        if length_back <= 1e-12 or length_forward <= 1e-12:
            rejected.append(int(index))
            samples.append(current)
            continue
        direction_back = back / length_back
        direction_forward = forward / length_forward
        cosine = float(np.clip(np.dot(direction_back, direction_forward), -1.0, 1.0))
        interior = math.acos(cosine)
        if interior < 1e-3 or interior > math.pi - 1e-3:
            samples.append(current)
            continue
        trim = radius / math.tan(interior / 2)
        if trim >= length_back - 1e-8 or trim >= length_forward - 1e-8 or trim <= 0:
            rejected.append(int(index))
            samples.append(current)
            continue
        start = current + direction_back * trim
        end = current + direction_forward * trim
        bisector = direction_back + direction_forward
        bisector_length = float(np.linalg.norm(bisector))
        if bisector_length <= 1e-12:
            rejected.append(int(index))
            samples.append(current)
            continue
        bisector /= bisector_length
        center = current + bisector * (radius / math.sin(interior / 2))
        angle_start = math.atan2(start[1] - center[1], start[0] - center[0])
        angle_end = math.atan2(end[1] - center[1], end[0] - center[0])
        sweep = (angle_end - angle_start + math.pi) % (2 * math.pi) - math.pi
        # The shorter arc follows the corner. A sweep near ±pi is ambiguous.
        if abs(abs(sweep) - math.pi) < 1e-6:
            rejected.append(int(index))
            samples.append(current)
            continue
        for step in range(arc_samples):
            angle = angle_start + sweep * (step / arc_samples)
            samples.append(center + radius * np.array([math.cos(angle), math.sin(angle)]))
    return np.asarray(samples, dtype=float), rejected


def notch_ring(outer_width, outer_height, slot_width, slot_depth, fillet, target_length, spacing):
    """Rectangle with one rectangular slot, corners filleted where they fit.

    The slot opens through the top edge. After construction the ring is scaled
    uniformly onto ``target_length``. Slot width therefore changes by that scale;
    the catalog fitter below chooses the pre-scale width so the scaled width
    meets a stated target, and records the residual.
    """
    if min(outer_width, outer_height, slot_width, slot_depth, fillet, spacing) <= 0:
        raise ValueError('CURVE_INVALID: notch parameters must be positive')
    if slot_width >= outer_width or slot_depth >= outer_height:
        raise ValueError('CURVE_INVALID: slot does not fit inside the outer rectangle')
    left, right = -outer_width / 2, outer_width / 2
    bottom, top = -outer_height / 2, outer_height / 2
    slot_left, slot_right = -slot_width / 2, slot_width / 2
    slot_floor = top - slot_depth
    vertices = [
        (left, bottom), (right, bottom), (right, top),
        (slot_right, top), (slot_right, slot_floor), (slot_left, slot_floor),
        (slot_left, top), (left, top),
    ]
    filleted, rejected = _fillet_ring(vertices, fillet, arc_samples=8)
    scaled, scale, raw_length = scale_to_length(filleted, target_length)
    return scaled, {
        'family': 'deep_notch',
        'construction': 'filleted_rectangular_slot',
        'outer_width_m': float(outer_width),
        'outer_height_m': float(outer_height),
        'requested_slot_width_m': float(slot_width),
        'requested_slot_depth_m': float(slot_depth),
        'fillet_radius_m': float(fillet),
        'fillet_rejected_corners': rejected,
        'raw_length_m': raw_length,
        'length_scale': scale,
    }


def measured_slot_width(points):
    """Width of a top-opening slot from horizontal sections of the polygon.

    A section through the slot meets the boundary four times. The middle gap
    is the slot. The median of those gaps is returned. None means the section
    test did not see a slot; the ring is not altered.
    """
    polygon = Polygon(np.asarray(points, dtype=float))
    if not polygon.is_valid or polygon.is_empty:
        return None
    y_min, y_max = polygon.bounds[1], polygon.bounds[3]
    gaps = []
    for fraction in np.linspace(0.58, 0.92, 18):
        height = y_min + fraction * (y_max - y_min)
        section = polygon.intersection(LineString([(-1.0e4, height), (1.0e4, height)]))
        xs = sorted(_section_xs(section))
        if len(xs) < 4:
            continue
        gaps.append(xs[2] - xs[1])
    if not gaps:
        return None
    return float(np.median(gaps))


def _section_xs(geometry):
    if geometry.is_empty:
        return []
    kind = geometry.geom_type
    if kind == 'Point':
        return [float(geometry.x)]
    if kind == 'LineString':
        return [float(geometry.coords[0][0]), float(geometry.coords[-1][0])]
    if kind in {'MultiPoint', 'MultiLineString', 'GeometryCollection'}:
        values = []
        for part in geometry.geoms:
            values.extend(_section_xs(part))
        return values
    return []


def fit_notch(target_slot_width, depth_fraction, fillet, target_length, spacing,
              outer_width=8.0, outer_height=7.2):
    """Choose the pre-scale slot width so the scaled slot matches ``target_slot_width``.

    Two deterministic corrections are enough because uniform scaling is linear
    in the reported width. The outer box stays fixed. A residual is recorded.
    """
    slot_depth = depth_fraction * outer_height
    slot_width = target_slot_width
    meta = None
    points = None
    for _ in range(4):
        points, meta = notch_ring(
            outer_width, outer_height, slot_width, slot_depth, fillet, target_length, spacing,
        )
        measured = measured_slot_width(points)
        if measured is None or measured <= 0:
            meta = {**meta, 'measured_slot_width_m': None, 'slot_fit': 'not_identified'}
            return points, meta
        slot_width *= target_slot_width / measured
        if slot_width >= outer_width or slot_width <= 0:
            meta = {**meta, 'measured_slot_width_m': measured, 'slot_fit': 'left_outer_box'}
            return points, meta
    points, meta = notch_ring(
        outer_width, outer_height, slot_width, slot_depth, fillet, target_length, spacing,
    )
    measured = measured_slot_width(points)
    meta = {
        **meta,
        'target_slot_width_m': float(target_slot_width),
        'depth_fraction': float(depth_fraction),
        'pre_scale_slot_width_m': float(slot_width),
        'measured_slot_width_m': None if measured is None else float(measured),
        'slot_width_residual_m': None if measured is None else float(measured - target_slot_width),
        'slot_fit': 'scaled',
    }
    return points, meta


def outward_normals(points):
    tangent = np.roll(points, -1, axis=0) - np.roll(points, 1, axis=0)
    norm = np.linalg.norm(tangent, axis=1)
    tangent = tangent / np.maximum(norm, 1e-15)[:, None]
    # Closed CCW ring: the outward normal is the clockwise rotation of the tangent.
    return np.column_stack([tangent[:, 1], -tangent[:, 0]])


def fourier_profile(length, stations, seed, modes=(2, 3, 4)):
    """Unit-peak cosine series. The seed draws one amplitude and one phase per mode.

    Draw order is fixed: for mode 2, then 3, then 4, ``uniform(-1, 1)`` then
    ``uniform(0, 2π)``. The series is divided by its maximum absolute value so
    the peak is exactly 1. It does not enter the deployment random stream; the
    planners called later are deterministic.
    """
    rng = np.random.default_rng(int(seed))
    coefficients = []
    total = np.zeros(len(stations), dtype=float)
    for mode in modes:
        amplitude = float(rng.uniform(-1.0, 1.0))
        phase = float(rng.uniform(0.0, 2.0 * math.pi))
        coefficients.append({'mode': int(mode), 'amplitude': amplitude, 'phase': phase})
        total += amplitude * np.cos(2 * math.pi * mode * stations / length + phase)
    peak = float(np.max(np.abs(total)))
    if peak <= 1e-15:
        raise ValueError('CURVE_INVALID: Fourier profile vanished')
    return total / peak, coefficients


def apply_normal_perturbation(points, seed, amplitude):
    """Displace along the outward normal by a fixed-amplitude Fourier profile.

    ``amplitude`` is metres on this ring, before the caller scales the length
    back to the common perimeter. The displacement is not projected back onto
    a simple polygon.
    """
    points = np.asarray(points, dtype=float)
    length = polyline_length(points)
    segment = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    stations = np.r_[0.0, np.cumsum(segment[:-1])]
    profile, coefficients = fourier_profile(length, stations, seed)
    displaced = points + outward_normals(points) * (float(amplitude) * profile)[:, None]
    return displaced, {
        'seed': int(seed),
        'seed_role': 'fixed-amplitude outward Fourier perturbation of modes 2, 3 and 4',
        'amplitude_m_before_final_scale': float(amplitude),
        'coefficients': coefficients,
        'profile_peak': 1.0,
    }


def hausdorff(left, right):
    """Directed-maximum Euclidean distance from ``left`` samples onto ``right`` segments."""
    right = np.asarray(right, dtype=float)
    start = right
    end = np.roll(right, -1, axis=0)
    edge = end - start
    length2 = np.sum(edge * edge, axis=1)
    worst = 0.0
    for point in np.asarray(left, dtype=float):
        relative = point - start
        step = np.sum(relative * edge, axis=1) / np.maximum(length2, 1e-15)
        step = np.clip(step, 0.0, 1.0)
        closest = start + step[:, None] * edge
        worst = max(worst, float(np.linalg.norm(closest - point, axis=1).min()))
    return worst


def curve_report(points, reference=None):
    """Closure, simplicity, orientation and discretization measurements.

    Nothing in this report rewrites ``points``.
    """
    points = np.asarray(points, dtype=float)
    segment = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    closed = np.vstack([points, points[0]])
    ring = LineString(closed)
    polygon = Polygon(points)
    valid_polygon = bool(polygon.is_valid and polygon.geom_type == 'Polygon')
    area = float(polygon.area) if valid_polygon else None
    hull_area = None
    if valid_polygon:
        hull_area = float(polygon.convex_hull.area)
    orientation = None
    if valid_polygon and area is not None:
        orientation = 'ccw' if polygon.exterior.is_ccw else 'cw'
    reasons = []
    if not np.isfinite(points).all():
        reasons.append('nonfinite')
    if len(points) < 32:
        reasons.append('too_few_samples')
    if np.any(segment <= 1e-8):
        reasons.append('duplicate_or_degenerate_segment')
    if not bool(ring.is_simple):
        reasons.append('self_intersection')
    if not valid_polygon:
        reasons.append('polygon_invalid_or_multipart')
    elif polygon.interiors:
        reasons.append('holes')
    elif area is None or area <= 1e-8:
        reasons.append('degenerate_area')
    elif orientation != 'ccw':
        reasons.append('not_counterclockwise')
    discretization = None
    if reference is not None and len(points) >= 3 and len(reference) >= 3:
        forward = hausdorff(points, reference)
        backward = hausdorff(reference, points)
        discretization = {'hausdorff_m': max(forward, backward),
                          'samples_to_reference_m': forward,
                          'reference_to_samples_m': backward}
        if not np.isfinite(discretization['hausdorff_m']):
            reasons.append('discretization_nonfinite')
    kappa = discrete_curvature(points) if np.isfinite(points).all() and len(points) >= 3 else None
    return {
        'valid': not reasons,
        'invalid_reasons': reasons,
        'sample_count': int(len(points)),
        'length_m': float(segment.sum()) if np.isfinite(segment).all() else None,
        'min_segment_m': float(segment.min()) if len(segment) else None,
        'max_segment_m': float(segment.max()) if len(segment) else None,
        'duplicate_segments': int(np.sum(segment <= 1e-8)),
        'closed_by_ring': True,
        'simple': bool(ring.is_simple),
        'polygon_valid': valid_polygon,
        'components': 1 if valid_polygon else 0,
        'holes': int(len(polygon.interiors)) if valid_polygon else None,
        'orientation': orientation,
        'area_m2': area,
        'convex_hull_area_m2': hull_area,
        'convexity_defect': (None if area is None or hull_area is None or hull_area <= 0
                             else float((hull_area - area) / hull_area)),
        'max_curvature_per_m': None if kappa is None else float(np.max(kappa)),
        'min_curvature_per_m': None if kappa is None else float(np.min(kappa)),
        'concave_sample_fraction': (None if kappa is None else float(np.mean(kappa < -1e-3))),
        'discretization': discretization,
    }


def discrete_curvature(points):
    previous = np.roll(points, 1, axis=0)
    nxt = np.roll(points, -1, axis=0)
    incoming = points - previous
    outgoing = nxt - points
    incoming_length = np.linalg.norm(incoming, axis=1)
    outgoing_length = np.linalg.norm(outgoing, axis=1)
    cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    dot = np.sum(incoming * outgoing, axis=1)
    turn = np.arctan2(cross, dot)
    step = 0.5 * (incoming_length + outgoing_length)
    return turn / np.maximum(step, 1e-12)


def min_nonadjacent_distance(points, min_index_gap):
    """Minimum Euclidean distance between samples that are not periodic neighbours.

    Neighbourhood is index distance on the ring, which follows the curve order.
    """
    points = np.asarray(points, dtype=float)
    count = len(points)
    best = math.inf
    pair = None
    gap = int(min_index_gap)
    for i in range(count):
        for delta in range(gap, count - gap + 1):
            j = (i + delta) % count
            if j <= i:
                continue
            distance = float(np.linalg.norm(points[i] - points[j]))
            if distance < best:
                best = distance
                pair = (int(i), int(j))
    return (None if pair is None else best), pair


def roll_origin(points, fraction, spacing=0.01):
    """Move the parameter origin by ``fraction`` of the perimeter.

    The shape is unchanged. ``fraction`` is realized as a whole-sample shift on
    an arc-length grid of ``spacing``, so the stored fraction is
    ``shift / sample_count``.
    """
    if not 0 <= float(fraction) < 1:
        raise ValueError('CURVE_INVALID: origin fraction must lie in [0, 1)')
    sampled = resample_arclength(points, spacing)
    shift = int(round(float(fraction) * len(sampled))) % len(sampled)
    rolled = sampled if shift == 0 else np.vstack([sampled[shift:], sampled[:shift]])
    return rolled, {
        'origin_fraction_requested': float(fraction),
        'origin_shift_samples': int(shift),
        'origin_fraction_realized': float(shift / len(sampled)),
        'origin_grid_spacing_m': float(spacing),
    }


# Frozen after measuring validity, convexity, slot width and cap width.
# A 0.04 m outward Fourier wave made both capsules non-convex, so the seed
# does not deform the shape. It only moves the parameter origin.
FROZEN_GEOMETRY = {
    'target_length_m': 32.0,
    'planner_spacing_m': 0.05,
    'fine_spacing_m': 0.01,
    'gap_limit_m': 1.94,
    'center_separation_m': 0.75,
    'separation_meaning': 'center distance 2*guide_radius + clearance = 2*0.20 + 0.35',
    'seeds': [15, 16, 17, 18, 19],
    'seed_origin_step': 0.05,
    'curves': {
        ('high_curvature_convex', 'mild'): {'cap_radius_m': 1.50},
        ('high_curvature_convex', 'strong'): {'cap_radius_m': 0.55},
        ('shallow_concave', 'mild'): {'depth_fraction': 0.22, 'sigma_rad': 0.40},
        ('shallow_concave', 'strong'): {'depth_fraction': 0.34, 'sigma_rad': 0.40},
        ('deep_notch', 'wide'): {'slot_width_m': 1.20, 'depth_fraction': 0.72, 'fillet_m': 0.18},
        ('deep_notch', 'narrow'): {'slot_width_m': 0.45, 'depth_fraction': 0.78, 'fillet_m': 0.10},
    },
}


def nominal_ring(family, intensity, target_length=32.0):
    """Unperturbed ring scaled to the common perimeter. No validity repair."""
    key = (family, intensity)
    if key not in FROZEN_GEOMETRY['curves']:
        raise KeyError(f'Unknown prescribed curve: {family}/{intensity}')
    spec = FROZEN_GEOMETRY['curves'][key]
    if family == 'high_curvature_convex':
        return stadium_ring(spec['cap_radius_m'], target_length, FROZEN_GEOMETRY['fine_spacing_m'])
    if family == 'shallow_concave':
        return radial_dent_ring(
            spec['depth_fraction'], spec['sigma_rad'], target_length,
            FROZEN_GEOMETRY['fine_spacing_m'],
        )
    if family == 'deep_notch':
        return fit_notch(
            spec['slot_width_m'], spec['depth_fraction'], spec['fillet_m'],
            target_length, FROZEN_GEOMETRY['fine_spacing_m'],
        )
    raise KeyError(family)


def build_prescribed_curve(family, intensity, seed, target_length=32.0, spacing=0.05):
    """Return the planner ring, its boundary, a validity report and construction notes.

    ``seed`` selects the parameter origin. It is not a crowd seed and it does
    not redraw the shape.
    """
    seeds = FROZEN_GEOMETRY['seeds']
    if int(seed) not in seeds:
        raise ValueError(f'Prescribed-curve seed {seed} is outside the frozen list {seeds}')
    nominal, construction = nominal_ring(family, intensity, target_length)
    nominal_report = curve_report(nominal)
    fraction = (int(seed) - seeds[0]) * FROZEN_GEOMETRY['seed_origin_step']
    rolled, origin = roll_origin(nominal, fraction, FROZEN_GEOMETRY['fine_spacing_m'])
    sampled = resample_arclength(rolled, spacing)
    reference = resample_arclength(rolled, FROZEN_GEOMETRY['fine_spacing_m'])
    report = curve_report(sampled, reference)
    if family == 'deep_notch':
        report['measured_slot_width_m'] = measured_slot_width(nominal)
    boundary = boundary_from_ring(sampled)
    return {
        'family': family,
        'intensity': intensity,
        'seed': int(seed),
        'points': sampled,
        'boundary': boundary,
        'report': report,
        'nominal_report': nominal_report,
        'construction': construction,
        'origin': origin,
        'crowd_field': 'same_ring_as_deployment_not_an_estimated_envelope',
        'boundary_source': 'prescribed_diagnostic',
    }


def prepare_curve(points, spacing, seed, amplitude, target_length):
    """Perturb a fine ring, restore the common perimeter, and resample.

    Invalid rings are kept. The perturbation is applied to arc-length samples
    at most 1 cm apart so a long straight wall is not a single chord.
    """
    nominal = np.asarray(points, dtype=float)
    fine_spacing = min(float(spacing), 0.01)
    try:
        fine = resample_arclength(nominal, fine_spacing)
        resample_error = None
    except ValueError as exc:
        fine = nominal
        resample_error = str(exc)
    perturbed, perturbation = apply_normal_perturbation(fine, seed, amplitude)
    restored, scale, perturbed_length = scale_to_length(perturbed, target_length)
    perturbation = {
        **perturbation,
        'length_before_final_scale_m': perturbed_length,
        'final_length_scale': scale,
    }
    if resample_error is None:
        try:
            sampled = resample_arclength(restored, spacing)
        except ValueError as exc:
            sampled = restored
            resample_error = str(exc)
    else:
        sampled = restored
    reference_samples = None
    if resample_error is None:
        reference_samples = resample_arclength(restored, fine_spacing)
    report = curve_report(sampled, reference_samples)
    if resample_error is not None:
        report['valid'] = False
        report['invalid_reasons'] = [*report['invalid_reasons'], 'resample_failed']
        report['resample_error'] = resample_error
    boundary = None
    if np.isfinite(sampled).all() and len(sampled) >= 3 and polyline_length(sampled) > 0:
        try:
            boundary = boundary_from_ring(sampled)
        except ValueError:
            boundary = None
    return sampled, boundary, report, perturbation
