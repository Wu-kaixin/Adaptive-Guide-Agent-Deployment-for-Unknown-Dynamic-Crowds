"""Named comparison adapters over the two existing boundary implementations."""
from time import perf_counter
import hashlib

from shapely.geometry import Point, Polygon

from .estimate import estimate_boundary, estimate_disk_union, occupied_hull, disk_occupancy


ESTIMATORS = {'convex_hull': 'estimated_convex_offset',
              'disk_union_closed': 'estimated_disk_union'}


def construct_boundary(estimator, positions, radii, room, *, offset=1.3, spacing=.10,
                       wall_margin=.55, crowd_distance=.75, closing_radius=.30, quad_segs=16):
    """Same observations; no fallback, component removal, or outcome tuning.

    Descriptors distinguish the full constructed occupied set from the exterior
    ring actually consumed by navigation (which fills any occupied-set holes).
    The unclosed disk union is a physical representation reference, not truth
    about a unique collective crowd boundary.
    """
    if estimator not in ESTIMATORS:
        raise ValueError('Unsupported boundary construction')
    started = perf_counter()
    reference, closed = disk_occupancy(positions, radii, 0. if estimator=='convex_hull' else closing_radius, quad_segs)
    occupied = occupied_hull(positions, radii) if estimator == 'convex_hull' else closed
    description_seconds = perf_counter() - started
    started = perf_counter()
    boundary, error = None, None
    try:
        if estimator == 'convex_hull':
            boundary = estimate_boundary(positions, offset, spacing, room, wall_margin, crowd_distance, radii)
        else:
            boundary = estimate_disk_union(positions, offset, spacing, room, wall_margin,
                crowd_distance, radii, closing_radius=closing_radius, quad_segs=quad_segs)
    except ValueError as exc:
        if str(exc).split(':')[0] not in {'BOUNDARY_INVALID', 'OFFSET_INVALID'}:
            raise
        error = str(exc)
    seconds = perf_counter() - started
    parts = [occupied] if occupied.geom_type == 'Polygon' else list(getattr(occupied, 'geoms', []))
    controller = None if boundary is None else Polygon(boundary.crowd)
    hull_area = occupied.convex_hull.area
    info = {'estimator': estimator, 'implementation': ('estimate_boundary' if estimator=='convex_hull' else 'estimate_disk_union'),
        'boundary_source': ESTIMATORS[estimator], 'status': 'ACCEPTED' if boundary is not None else error.split(':')[0],
        'failure_reason': error, 'construction_seconds': seconds, 'description_seconds': description_seconds,
        'input_semantics': 'observed centres plus per-person physical radii',
        'disk_quad_segs_effective': 32 if estimator=='convex_hull' else max(int(quad_segs),32),
        'disk_quad_segs_configured': None if estimator=='convex_hull' else int(quad_segs),
        'closing_radius_m': None if estimator=='convex_hull' else closing_radius,
        'offset_m': offset, 'offset_quad_segs': 32, 'offset_join': 'round', 'sample_spacing_m': spacing,
        'occupied_type': occupied.geom_type, 'component_count': len(parts),
        'occupied_holes': sum(len(p.interiors) for p in parts if p.geom_type=='Polygon'),
        'occupied_valid': bool(occupied.is_valid), 'occupied_area_m2': float(occupied.area),
        'occupied_perimeter_m': float(occupied.length),
        'observed_disk_union_area_m2': float(reference.area),
        'extra_envelope_area_m2': float(occupied.difference(reference).area),
        'concavity_area_m2': float(occupied.convex_hull.difference(occupied).area),
        'area_over_convex_hull_area': None if hull_area==0 else float(occupied.area/hull_area),
        'covers_observed_disks_32_strict': bool(occupied.covers(reference)),
        'uncovered_disk_area_m2': float(reference.difference(occupied).area),
        'controller_covers_acceptance_disks_16': None if controller is None else bool(all(
            controller.covers(Point(p).buffer(float(r),quad_segs=16)) for p,r in zip(positions,radii))),
        'controller_extra_area_m2': None if controller is None else float(controller.difference(reference).area),
        'controller_filled_hole_area_m2': None if controller is None else float(controller.difference(occupied).area),
        'curve_valid': boundary is not None, 'curve_length_m': None if boundary is None else boundary.length,
        'curve_area_m2': None if boundary is None else float(Polygon(boundary.deployment).area),
        'sample_count': None if boundary is None else len(boundary.deployment),
        'occupied_wkb_sha256': hashlib.sha256(occupied.wkb).hexdigest(),
        'reference_wkb_sha256': hashlib.sha256(reference.wkb).hexdigest()}
    return boundary, info, occupied, reference
