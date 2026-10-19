"""Explicit common-phase postprocessing on the ORIGINAL periodic curve.

This is an algorithm change, never silently applied to a baseline planner.
The caller freezes the finite phase grid before observing search outcomes.
No curve cutting, projection into free space, or change of density is used.
"""
from dataclasses import replace

import numpy as np

from udc.navigation.paths import target_domain_check
from .planners import _interpolate, periodic_gaps, coverage_statistics, quadrature_mass


def admissible_phase(boundary, plan, domain, density, *, phase_bins,
                     gap_limit, separation, quadrature_samples):
    """Check a bounded full-period grid; keep the original circular gap pattern.

    Choose the least original-demand cost, then the smallest circular shift.
    For constant density use the exact continuous cost sum(gap**3)/(12*L),
    which is invariant to common phase. Also save the original quadrature cost.
    Failure to find a witness is not a proof of infeasibility.
    """
    if type(phase_bins) is not int or phase_bins < 2:
        raise ValueError('phase_bins must be an integer >= 2')
    density = np.asarray(density, dtype=float)
    q, mass, length = quadrature_mass(boundary, density, quadrature_samples)
    original = target_domain_check(domain, plan.positions, plan.arc_positions)
    before_cost = coverage_statistics(q, mass, plan.arc_positions, length, np.inf)['weighted_cost']
    if original['accepted']:
        return plan, {'algorithm': 'static_domain_phase_v1', 'changed': False,
                      'original': original, 'phase_bins': phase_bins,
                      'attempts': [], 'selected_phase_m': 0., 'cost_before': before_cost,
                      'cost_after': before_cost, 'proof_scope': 'original target witness'}
    sites = np.asarray(plan.arc_positions, dtype=float)
    gaps = periodic_gaps(np.sort(sites % length), length)
    constant = bool(np.all(density == density[0]))
    exact_cost = float(np.sum(gaps**3) / (12 * length)) if constant else None
    attempts, feasible = [], []
    for index in range(phase_bins):
        # Includes zero; represents the full period, with negative shifts saved
        # for the second half. No adaptive budget extension or local optimiser.
        phase = (index if index <= phase_bins // 2 else index - phase_bins) * length / phase_bins
        shifted = np.sort((sites + phase) % length)
        points = _interpolate(boundary, shifted)
        distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
        np.fill_diagonal(distances, np.inf)
        shifted_gaps = periodic_gaps(shifted, length)
        admission = target_domain_check(domain, points, shifted)
        arc_ok = bool(shifted_gaps.min() >= separation - 1e-12
                      and shifted_gaps.max() <= gap_limit + 1e-9)
        pair_ok = bool(distances.min() >= separation)
        cost = coverage_statistics(q, mass, shifted, length, np.inf)['weighted_cost']
        accepted = admission['accepted'] and arc_ok and pair_ok
        record = {'phase_m': float(phase), 'accepted': accepted,
                  'invalid_targets': admission['invalid_target_indices'],
                  'min_arc_gap_m': float(shifted_gaps.min()),
                  'max_arc_gap_m': float(shifted_gaps.max()),
                  'min_pair_distance_m': float(distances.min()),
                  'quadrature_cost': cost, 'exact_constant_density_cost': exact_cost}
        attempts.append(record)
        if accepted:
            score = exact_cost if constant else cost
            feasible.append((score, abs(phase), phase, shifted, points, cost))
    if not feasible:
        return None, {'algorithm': 'static_domain_phase_v1', 'changed': False,
                      'original': original, 'phase_bins': phase_bins, 'attempts': attempts,
                      'cost_before': before_cost, 'proof_scope': 'bounded grid exhausted; no infeasibility proof'}
    _, _, phase, shifted, points, cost = min(feasible, key=lambda item: item[:3])
    candidate = replace(plan, positions=points, arc_positions=shifted,
                        max_gap=float(periodic_gaps(shifted, length).max()),
                        method=plan.method + '+static_domain_phase_v1',
                        exit_reason='STATIC_DOMAIN_COMMON_PHASE', iterations=0,
                        cost=np.array([cost]))
    return candidate, {'algorithm': 'static_domain_phase_v1', 'changed': True,
                       'original': original, 'phase_bins': phase_bins, 'attempts': attempts,
                       'selected_phase_m': float(phase), 'cost_before': before_cost,
                       'cost_after': cost, 'constant_density_exact_cost': exact_cost,
                       'proof_scope': 'feasible witness on original periodic curve; not global optimum'}
