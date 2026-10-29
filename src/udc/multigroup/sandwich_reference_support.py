"""Monotone inner/outer morphology for an original-reference support region.

This model enclosure does not assign demand mass to planar area. It only
tightens the older sufficient support. Ordinary float64/GEOS arithmetic is
audited against saved references; this is not an interval-arithmetic proof.
"""
import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from .geometry import outer_disk


def fill_holes(geometry):
    """Fill bounded holes without dropping disconnected polygon components."""
    if geometry.is_empty:
        return geometry
    if geometry.geom_type == 'Polygon':
        return Polygon(geometry.exterior)
    if geometry.geom_type == 'MultiPolygon':
        return unary_union([Polygon(p.exterior) for p in geometry.geoms])
    raise ValueError('Morphology enclosure requires polygonal geometry')


def group_enclosure(measurement, rows, config):
    obs, epsilon = measurement.observation, measurement.position_error_bound_m
    cosine, pad = np.cos(np.pi / 64), 1e-9
    lower_disks, upper_disks = [], []
    for row in rows:
        radius = obs.radii[row] + config['construction_margin_m']
        lower_radius = radius - epsilon - pad
        if lower_radius > 0:
            lower_disks.append(Point(obs.positions[row]).buffer(lower_radius, quad_segs=16))
        upper_radius = radius / cosine + 1e-12 + epsilon + pad
        upper_disks.append(outer_disk(obs.positions[row], upper_radius))
    lower, upper = unary_union(lower_disks), unary_union(upper_disks)
    closing = config['closing_radius_m']
    if closing > 0:
        # buffer_q(A,+r) encloses A (+) B(r*cos(pi/64)), and is enclosed
        # by A (+) B(r). Negative buffers have the opposite inclusions.
        lower_closed = lower.buffer(closing * cosine, quad_segs=16).buffer(-closing / cosine, quad_segs=16)
        upper_closed = upper.buffer(closing / cosine, quad_segs=16).buffer(-closing * cosine, quad_segs=16)
        lower = unary_union([lower, lower_closed])
        upper = unary_union([upper, upper_closed])
    lower, upper = fill_holes(lower), fill_holes(upper)
    offset = config['offset_m']
    lower_offset = lower.buffer(offset * cosine, quad_segs=16)
    upper_offset = upper.buffer(offset / cosine, quad_segs=16)
    # Original resampling joins adjacent boundary points. Every point of
    # that chord is within arc_spacing/2 of at least one sampled endpoint.
    # Expand both sides of the boundary enclosure by that distance.
    chord = config['sample_spacing_m'] / 2 + pad
    outer = upper_offset.buffer(chord / cosine, quad_segs=16)
    inner = lower_offset.buffer(-chord / cosine, quad_segs=16)
    if not inner.difference(outer).is_empty:
        raise ValueError('Monotone inner support is not inside outer support')
    region = outer.difference(inner)
    if region.is_empty or not region.is_valid:
        raise ValueError('Invalid monotone support enclosure')
    return region, {'lower_components': len(getattr(lower, 'geoms', (lower,))),
        'upper_components': len(getattr(upper, 'geoms', (upper,))),
        'outer_area_m2': outer.area, 'excluded_inner_area_m2': inner.area,
        'chord_enclosure_m': chord, 'numeric_construction_pad_m': pad,
        'method': 'monotone_sandwich_v1', 'demand_mass_bound': None}
