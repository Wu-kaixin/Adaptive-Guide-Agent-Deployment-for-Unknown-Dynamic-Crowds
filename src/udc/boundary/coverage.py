"""Versioned diagnostic coverage of explicit, inscribed disk polygons.

No tolerance acceptance and no geometry repair are provided. The default
estimator and independent acceptance remain unchanged (legacy q=16).
"""
from fractions import Fraction
import hashlib
from math import isqrt, pi, sin

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union


CONTRACT_VERSION = 'observed_disk_polygon_coverage_v1'
CANONICAL_QUAD_SEGS = 32


def radial_discretization_error(radius, quad_segs):
    """r*(1-cos(pi/(4q))), evaluated without subtractive cancellation, in m."""
    if not np.isfinite(radius) or radius < 0 or type(quad_segs) is not int or quad_segs < 1:
        raise ValueError('Finite nonnegative radius and positive integer q required')
    return float(2 * radius * sin(pi / (8 * quad_segs)) ** 2)


def person_disk_polygons(positions, radii, quad_segs=CANONICAL_QUAD_SEGS):
    """Canonical reference uses individual polygons, not a second union overlay.

    q=32 is an explicit inscribed polygon reference, not an analytic circle.
    A zero-radius physical disk is represented by a Point in this audit API.
    This does not change the legacy estimator's handling of radius zero.
    """
    positions, radii = np.asarray(positions, dtype=float), np.asarray(radii, dtype=float)
    if (positions.ndim != 2 or positions.shape[1] != 2 or radii.shape != (len(positions),)
            or not np.isfinite(positions).all() or not np.isfinite(radii).all()
            or np.any(radii < 0) or type(quad_segs) is not int or quad_segs < 1):
        raise ValueError('Invalid observed disk reference')
    return [Point(p) if r == 0 else Point(p).buffer(float(r), quad_segs=quad_segs)
            for p, r in zip(positions, radii)]


def _orientation(a, b, p):
    ax, ay = map(Fraction, a); bx, by = map(Fraction, b); px, py = map(Fraction, p)
    return (bx-ax)*(py-ay)-(by-ay)*(px-ax)


def _ring_location(point, coordinates):
    """Exact ray crossing for the dyadic rational values of saved floats."""
    x, y = point; inside = False
    for a, b in zip(coordinates[:-1], coordinates[1:]):
        in_box = min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1])
        crosses_y = (a[1] > y) != (b[1] > y)
        if not in_box and not crosses_y:
            continue
        determinant = _orientation(a, b, point)
        if in_box and determinant == 0:
            return 0
        if crosses_y and ((determinant > 0) == (b[1] > a[1])):
            inside = not inside
    return 1 if inside else -1


def exact_point_location(point, region):
    """-1 outside, 0 on boundary, 1 interior; Polygon or MultiPolygon only.

    This is a local evidence tool, not a general exact overlay kernel. It
    interprets the saved binary64 coordinates exactly, including every hole.
    """
    if region.geom_type == 'MultiPolygon':
        results = [exact_point_location(point, p) for p in region.geoms]
        return 1 if 1 in results else 0 if 0 in results else -1
    if region.geom_type != 'Polygon' or region.is_empty:
        raise ValueError('A nonempty polygonal region is required')
    outer = _ring_location(point, list(region.exterior.coords))
    if outer <= 0:
        return outer
    for hole in region.interiors:
        result = _ring_location(point, list(hole.coords))
        if result == 0:
            return 0
        if result == 1:
            return -1
    return 1


def _fraction_record(value):
    return {'numerator': str(value.numerator), 'denominator': str(value.denominator)}


def _sqrt_bounds(value, decimal_places=80):
    scale = 10 ** decimal_places
    integer = isqrt(value.numerator * scale * scale // value.denominator)
    low, high = Fraction(integer, scale), Fraction(integer+1, scale)
    assert low*low <= value < high*high
    def decimal(n):
        return str(n//scale)+'.'+str(n % scale).zfill(decimal_places)
    return {'lower_m': decimal(integer), 'upper_m': decimal(integer+1),
            'interval_width_m': '1e-'+str(decimal_places)}


def exact_point_boundary_distance(point, region):
    """Exact nearest segment squared distance plus certified decimal bracket.

    Includes all exterior and hole segments. Does not use a GEOS overlay or
    floating point distance to select the closest edge.
    """
    parts = list(region.geoms) if region.geom_type == 'MultiPolygon' else [region]
    best = None; witness = None
    p = tuple(map(Fraction, point))
    for part_index, part in enumerate(parts):
        for ring_index, ring in enumerate([part.exterior, *part.interiors]):
            coordinates = list(ring.coords)
            for edge, (start, end) in enumerate(zip(coordinates[:-1], coordinates[1:])):
                a, b = tuple(map(Fraction, start)), tuple(map(Fraction, end))
                d = (b[0]-a[0], b[1]-a[1]); rel = (p[0]-a[0], p[1]-a[1])
                length2 = d[0]**2+d[1]**2
                t = min(Fraction(1), max(Fraction(0), (rel[0]*d[0]+rel[1]*d[1])/length2)) if length2 else Fraction(0)
                distance2 = (rel[0]-t*d[0])**2+(rel[1]-t*d[1])**2
                if best is None or distance2 < best:
                    best = distance2
                    witness = {'part': part_index, 'ring': ring_index, 'edge': edge,
                        'start': list(start), 'end': list(end),
                        'projection_fraction': _fraction_record(t),
                        'orientation_determinant_m2': _fraction_record(_orientation(start, end, point))}
    if best is None:
        raise ValueError('No boundary segments')
    return {'point': list(point), 'exact_location': exact_point_location(point, region),
            'distance_squared_m2': _fraction_record(best), 'distance_bracket': _sqrt_bounds(best),
            'closest_segment': witness,
            'scope': 'exact rational geometry of saved binary64 coordinates, not ideal pre-overlay geometry'}


def exact_convex_reference_gap_enclosure(reference, region):
    """Limited offline certificate for a convex reference polygon.

    Split every reference edge at exact region-boundary intersections; clip
    every region boundary edge (including holes) to the reference. These are
    ALL possible boundaries of reference-minus-region. Their rational bounding
    box therefore encloses every missed point, without a floating overlay.
    If a region-boundary anchor exists, its box diagonal bounds missed-point
    distance to the region. This is deliberately not an overlay/repair API.
    """
    if (reference.geom_type != 'Polygon' or reference.is_empty or reference.interiors
            or not reference.is_valid or region.is_empty or not region.is_valid
            or region.geom_type not in ('Polygon','MultiPolygon')):
        raise ValueError('Valid convex Polygon reference without holes required')
    vertices=[tuple(map(Fraction,p)) for p in reference.exterior.coords[:-1]]
    def cross(a,b):return a[0]*b[1]-a[1]*b[0]
    def sub(a,b):return a[0]-b[0],a[1]-b[1]
    def at(a,d,t):return a[0]+t*d[0],a[1]+t*d[1]
    signed_area=sum(cross(a,b) for a,b in zip(vertices,vertices[1:]+vertices[:1]))
    sign=1 if signed_area>0 else -1
    if not all(sign*cross(sub(b,a),sub(c,b))>=0 for a,b,c in
            zip(vertices,vertices[1:]+vertices[:1],vertices[2:]+vertices[:2])):
        raise ValueError('Reference must be convex in exact saved coordinates')
    reference_edges=list(zip(vertices,vertices[1:]+vertices[:1]))
    parts=list(region.geoms) if region.geom_type=='MultiPolygon' else [region]
    boundary=[]
    for part in parts:
        for ring in [part.exterior,*part.interiors]:
            coords=[tuple(map(Fraction,p)) for p in ring.coords]
            boundary.extend(zip(coords[:-1],coords[1:]))
    def boxes_overlap(a,b,c,d):
        return all(max(min(a[k],b[k]),min(c[k],d[k]))<=min(max(a[k],b[k]),max(c[k],d[k])) for k in (0,1))
    outside=[]; inside_boundary=[]
    for a,b in reference_edges:
        d=sub(b,a);denom=d[0]**2+d[1]**2;cuts={Fraction(0),Fraction(1)}
        for c,e in boundary:
            if not boxes_overlap(a,b,c,e):continue
            s=sub(e,c);v=sub(c,a);det=cross(d,s)
            if det:
                t,u=cross(v,s)/det,cross(v,d)/det
                if 0<=t<=1 and 0<=u<=1:cuts.add(t)
            elif cross(v,d)==0:
                for point in (c,e):
                    rel=sub(point,a);t=(rel[0]*d[0]+rel[1]*d[1])/denom
                    if 0<=t<=1:cuts.add(t)
        cuts=sorted(cuts)
        for low,high in zip(cuts[:-1],cuts[1:]):
            if exact_point_location(at(a,d,(low+high)/2),region)==-1:
                outside.append((at(a,d,low),at(a,d,high)))
    bounds=reference.bounds;lo=(Fraction(bounds[0]),Fraction(bounds[1]));hi=(Fraction(bounds[2]),Fraction(bounds[3]))
    for a,b in boundary:
        if not boxes_overlap(a,b,lo,hi):continue
        d=sub(b,a);low,high=Fraction(0),Fraction(1);empty=False
        for c,e in reference_edges:
            edge=sub(e,c);base=sign*cross(edge,sub(a,c));slope=sign*cross(edge,d)
            if slope>0:low=max(low,-base/slope)
            elif slope<0:high=min(high,-base/slope)
            elif base<0:empty=True;break
            if low>high:empty=True;break
        if not empty and low<high:
            middle=at(a,d,(low+high)/2)
            if _ring_location(middle,list(reference.exterior.coords))==1:
                inside_boundary.append((at(a,d,low),at(a,d,high)))
    points=[p for segment in outside+inside_boundary for p in segment]
    box_record=None;bound=None
    if points:
        minimum=tuple(min(p[k] for p in points) for k in (0,1))
        maximum=tuple(max(p[k] for p in points) for k in (0,1))
        box_record={'minimum':[_fraction_record(v) for v in minimum],
            'maximum':[_fraction_record(v) for v in maximum],
            'display_minimum':[float(v) for v in minimum], 'display_maximum':[float(v) for v in maximum]}
        if inside_boundary:
            bound=_sqrt_bounds(sum((maximum[k]-minimum[k])**2 for k in (0,1)))
    # Separate boundary loops: distant tiny defects must not create an
    # artificially large whole-reference box. Never infer a depth bound for
    # an unanchored loop (e.g. a completely disjoint reference).
    parents={}
    def find(p):
        parents.setdefault(p,p)
        if parents[p]!=p:parents[p]=find(parents[p])
        return parents[p]
    for a,b in outside+inside_boundary:
        parents[find(a)]=find(b)
    groups={}
    for point in parents:groups.setdefault(find(point),[]).append(point)
    anchored={find(a) for a,b in inside_boundary}
    components=[];maximum_diagonal2=Fraction(0)
    for key,vertices_in_group in groups.items():
        minimum=tuple(min(p[k] for p in vertices_in_group) for k in (0,1))
        maximum=tuple(max(p[k] for p in vertices_in_group) for k in (0,1))
        diagonal2=sum((maximum[k]-minimum[k])**2 for k in (0,1))
        maximum_diagonal2=max(maximum_diagonal2,diagonal2)
        components.append({'boundary_graph_vertices':len(vertices_in_group),
            'has_region_boundary_anchor':key in anchored,
            'box_minimum':[_fraction_record(v) for v in minimum],
            'box_maximum':[_fraction_record(v) for v in maximum],
            'box_diagonal_bound':_sqrt_bounds(diagonal2)})
    component_bound=_sqrt_bounds(maximum_diagonal2) if components and all(c['has_region_boundary_anchor'] for c in components) else None
    return {'reference_edges_examined':len(reference_edges),'region_edges_examined':len(boundary),
        'reference_outside_open_subsegments':len(outside),'region_boundary_subsegments_in_reference_interior':len(inside_boundary),
        'gap_enclosing_box_exact':box_record,'global_box_diagonal_bound':bound,
        'gap_boundary_components':components,
        'entire_reference_gap_distance_upper_bound':component_bound,
        'no_gap_boundaries_found':not points,
        'proof_scope':'all finite polygonal edges and region holes; exact saved-coordinate rational intersections; convex reference only; no acceptance change'}


def audit_person_disk_coverage(region, positions, radii, *, quad_segs=32):
    """Full-region strict diagnostic; never admits an approximate cover.

    Acceptance requires *each* explicit P_q to satisfy GEOS covers, including
    its edges and interior and every region hole. A failed predicate remains
    rejected even if difference is empty/zero-area or all vertices are inside.
    A union-overlay predicate is reported separately and never substitutes
    for the individual reference collection.
    """
    disks = person_disk_polygons(positions, radii, quad_segs)
    if region.is_empty or not region.is_valid or region.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError('Valid nonempty Polygon/MultiPolygon region required')
    records = []
    for index, (disk, radius) in enumerate(zip(disks, radii)):
        strict = bool(region.covers(disk)); difference = disk.difference(region)
        record = {'person_index': index, 'raw_strict_covers': strict,
            'de9im': region.relate(disk), 'difference_type': difference.geom_type,
            'difference_empty': bool(difference.is_empty), 'difference_area_m2': float(difference.area),
            'difference_length_m': float(difference.length),
            'reference_wkb_sha256': hashlib.sha256(disk.wkb).hexdigest(),
            'ideal_radial_discretization_error_m': radial_discretization_error(float(radius), quad_segs),
            'exact_outside_vertex_witness': None}
        if not strict:
            points = [disk.coords[0]] if disk.geom_type == 'Point' else list(disk.exterior.coords[:-1])
            # Evidence search only. Missing vertex witness NEVER implies cover.
            for point in points:
                if not region.covers(Point(point)) and exact_point_location(point, region) == -1:
                    record['exact_outside_vertex_witness'] = list(point)
                    break
        record['classification'] = ('STRICT_PREDICATE_ACCEPTED' if strict else
            'REJECT_EXACT_SAVED_COORDINATE_WITNESS' if record['exact_outside_vertex_witness'] is not None
            else 'REJECT_NO_EXACT_VERTEX_CERTIFICATE')
        records.append(record)
    union = unary_union(disks)
    accepted = all(r['raw_strict_covers'] for r in records)
    return {'contract_version': CONTRACT_VERSION,
        'mode': 'canonical_q32_strict_diagnostic' if quad_segs == 32 else 'reference_resolution_comparison_only',
        'reference_quad_segs': quad_segs, 'reference_edges_per_positive_radius_person': 4*quad_segs,
        'reference_semantics': 'individual inscribed polygons; zero-radius Point; not analytic disks',
        'region_sha256': hashlib.sha256(region.wkb).hexdigest(),
        'region_components': 1 if region.geom_type == 'Polygon' else len(region.geoms),
        'region_holes': sum(len(p.interiors) for p in ([region] if region.geom_type == 'Polygon' else region.geoms)),
        'raw_strict_individuals_all': accepted,
        'raw_strict_union_overlay': bool(region.covers(union)),
        'union_difference_area_m2': float(union.difference(region).area),
        'candidate_accepted': accepted, 'approximate_acceptance_enabled': False,
        'coverage_acceptance_tolerance_m': 0.0, 'geometry_coordinate_displacement_m': 0.0,
        'guarantee': 'recorded full-polygon GEOS strict predicate; not an exact containment proof or analytic disk guarantee',
        'persons': records}
