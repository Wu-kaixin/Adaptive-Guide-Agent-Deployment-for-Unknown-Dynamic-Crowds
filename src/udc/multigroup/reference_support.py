"""Observation-only candidate supports for the ORIGINAL external reference.

Current round-buffer transfer premises require a restricted-family proof.
A generic polygon counterexample is recorded by buffer_bound_development_v1.
Builders are therefore quarantined from runtime service certification; frozen
older experiments preserve their source and empirical outcomes unchanged.
Dominance remains a conditional theorem for a correctly supplied support set.
"""
from dataclasses import dataclass

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

from .geometry import outer_disk
from .partition_stability import partition_stability_certificate


@dataclass(frozen=True)
class ReferenceSupport:
    regions: tuple
    rows: tuple
    grouping_certificate: dict
    error_bound_m: float
    evidence: dict


def build_support(measurement, groups, config):
    """Build an experimental support, retaining its unproved transfer status.

    The following construction rationale is conditional on buffer inclusions
    that are not established for every generated source polygon. Empirical
    containment tests do not discharge this mathematical proof obligation.

    Outer: every construction disk is inside its observation-centred envelope;
    closing is a subset of positive dilation, and hole filling a subset of its
    convex hull. Add the closing radius and deployment offset outward.
    Inner: a disk of radius r+margin-eps is always occupied. Round positive
    buffering contains a disk of radius offset*cos(pi/64); a resampled chord
    is at most sample_spacing/2 from the offset polygon boundary. Its deep
    interior therefore cannot contain a reference point.
    """
    obs, epsilon = measurement.observation, measurement.position_error_bound_m
    rows = tuple(g.rows for g in groups)
    grouping = partition_stability_certificate(obs, rows, config['group_link_m'], epsilon)
    identity_method = config.get('support_identity', 'reduced_link_v1')
    if identity_method == 'collective_coverage_v1':
        from .collective_identity import certificate as identity_certificate
        grouping = identity_certificate(obs, rows, config['group_link_m'], epsilon, grouping)
    elif identity_method != 'reduced_link_v1':
        raise ValueError('Unknown observable identity certificate')
    cosine = np.cos(np.pi / 64)
    # A true split of an observed connected component has an observed link
    # across the split. Its true endpoints are at most link+2*eps apart.
    # Their resampled deployment interiors contain disks of this radius.
    # Overlap would violate ORIGINAL cross-component separation, so whenever
    # that original reference is defined a split is excluded by this test.
    minimum_core = float(np.min(obs.radii)) + config['construction_margin_m'] + config['offset_m'] * cosine - config['sample_spacing_m'] / 2
    conditional_identity = (grouping['cross_groups_separated_at_expanded_link']
                            and config['group_link_m'] + 2 * epsilon < 2 * minimum_core)
    grouping['identity_given_original_reference_defined'] = bool(conditional_identity)
    grouping['split_distance_upper_m'] = config['group_link_m'] + 2 * epsilon
    grouping['two_reference_core_diameters_m'] = 2 * minimum_core
    pad, regions, details = 1e-9, [], []
    for group_rows in rows:
        enclosures = [outer_disk(obs.positions[i], (obs.radii[i] + config['construction_margin_m']) / cosine + 1e-12 + epsilon)
                      for i in group_rows]
        hull = unary_union(enclosures).convex_hull
        closing_mode = config.get('support_closing_enclosure', 'positive_dilation_only')
        if closing_mode not in ('positive_dilation_only', 'round_buffer_sagitta'):
            raise ValueError('Unknown closing enclosure model')
        closing_reach = config['closing_radius_m'] * (1 - cosine) if closing_mode == 'round_buffer_sagitta' else config['closing_radius_m']
        reach = closing_reach + config['offset_m']
        outer = hull.buffer(reach / cosine + pad, quad_segs=16)
        inner = []
        for i in group_rows:
            guaranteed_radius = obs.radii[i] + config['construction_margin_m'] - epsilon
            if guaranteed_radius <= 0:
                continue
            excluded = guaranteed_radius + config['offset_m'] * cosine - config['sample_spacing_m'] / 2 - pad
            if excluded > 0:
                inner.append(Point(obs.positions[i]).buffer(excluded, quad_segs=16))
        region = outer.difference(unary_union(inner)) if inner else outer
        if not region.is_valid or region.is_empty:
            raise ValueError('Invalid reference support enclosure')
        regions.append(region)
        details.append({'outer_area_m2': outer.area, 'support_area_m2': region.area,
                        'inner_disk_count': len(inner), 'closing_enclosure': closing_mode,
                        'closing_outer_error_m': closing_reach})
    enclosure = config.get('support_enclosure', 'convex_outer_individual_inner_v1')
    if enclosure == 'monotone_sandwich_v1':
        from .sandwich_reference_support import group_enclosure
        for index, group_rows in enumerate(rows):
            sandwich, evidence = group_enclosure(measurement, group_rows, config)
            original = regions[index]
            original_area = original.area
            candidate = original.intersection(sandwich)
            if candidate.is_empty or not candidate.is_valid:
                raise ValueError('Invalid intersection of original-reference enclosures')
            outside = candidate.difference(original)
            if outside.is_empty:
                regions[index] = candidate
                evidence['strict_subset_check'] = 'passed'
            else:
                # An overlay result need not preserve strict finite-coordinate
                # containment. Reject this tightening rather than waive it.
                evidence.update(strict_subset_check='rejected_use_original',
                    rejected_difference_wkb_hex=outside.wkb_hex,
                    rejected_difference_area_m2=outside.area,
                    rejected_difference_length_m=outside.length)
            details[index]['monotone_sandwich'] = evidence
            details[index]['base_support_area_m2'] = original_area
            details[index]['support_area_m2'] = regions[index].area
    elif enclosure != 'convex_outer_individual_inner_v1':
        raise ValueError('Unknown original-reference support enclosure')
    return ReferenceSupport(tuple(regions), rows, grouping, epsilon,
                            {'groups': details, 'numeric_construction_pad_m': pad,
                             'allow_conditional_identity': bool(config.get('support_conditional_identity',False)),
                             'enclosure': enclosure,
                             'reference': 'original_world_external_resampled_curve_and_any_nonnegative_demand',
                             'condition': 'original reference construction is defined and group identity is stable',
                             'enclosure_transfer_status': 'UNPROVEN_GENERIC_BUFFER_BOUND',
                             'runtime_certification_quarantined': True,
                             'proof_gap_evidence': 'runs/step1_foundation_20261029/buffer_bound_development_v1'})


def certificate(support, retained, greedy, radius, delta=.002, tracking_bound=1e-10):
    retained, greedy = np.asarray(retained, float).reshape(-1, 2), np.asarray(greedy, float).reshape(-1, 2)
    if not np.isfinite(retained).all() or not np.isfinite(greedy).all() or not np.isfinite([radius, delta, tracking_bound]).all() or radius <= tracking_bound or tracking_bound < 0 or delta < 0:
        raise ValueError('Invalid support certificate inputs')
    if support.evidence.get('runtime_certification_quarantined', False):
        return {'status': 'UNCERTIFIED', 'accept_retained': False,
                'reason': 'support_enclosure_transfer_unproven', 'groups': [],
                'enclosure_transfer_status': support.evidence['enclosure_transfer_status'],
                'scope': 'candidate geometry may be audited, but empirical tests do not establish all-world containment'}
    if not (support.grouping_certificate['certified'] or (support.evidence.get('allow_conditional_identity',False) and support.grouping_certificate.get('identity_given_original_reference_defined'))):
        return {'status': 'UNCERTIFIED', 'accept_retained': False, 'reason': 'group_identity_not_certified', 'groups': []}
    def covered(robots, outward):
        if outward:
            return unary_union([outer_disk(p, radius + tracking_bound, quad_segs=32) for p in robots])
        return unary_union([Point(p).buffer(radius - tracking_bound, quad_segs=32) for p in robots])
    ret_inner, ret_outer = covered(retained, False), covered(retained, True)
    greedy_inner, greedy_outer = covered(greedy, False), covered(greedy, True)
    rows = []
    for region in support.regions:
        ret_h = 0. if region.difference(ret_inner).is_empty else 1.
        ret_l = 1. if region.intersection(ret_outer).is_empty else 0.
        greedy_h = 0. if region.difference(greedy_inner).is_empty else 1.
        greedy_l = 1. if region.intersection(greedy_outer).is_empty else 0.
        potential_loss = region.intersection(greedy_outer).difference(ret_inner)
        paired = 0. if potential_loss.is_empty else 1.
        upper = min(paired, max(0., ret_h - greedy_l))
        rows.append({'retained_interval': [ret_l, ret_h], 'greedy_interval': [greedy_l, greedy_h],
                     'upper_loss': upper, 'loss_region_area_m2': potential_loss.area,
                     'loss_region_empty': potential_loss.is_empty})
    accept = all(row['upper_loss'] <= delta for row in rows)
    return {'status': 'ORIGINAL_REFERENCE_SUPPORT_DOMINANCE' if accept else 'UNCERTIFIED',
            'accept_retained': accept, 'groups': rows, 'delta': delta, 'tracking_bound_m': tracking_bound,
            'reason': 'distribution_free_support_dominance' if accept else 'support_enclosure_too_wide',
            'scope': 'original reference, arbitrary bounded independent errors, defined reference and stable groups; float64 audit'}
