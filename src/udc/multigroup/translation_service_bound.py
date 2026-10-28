"""Conditional service bounds for an explicitly declared common-translation model.

The anchored, quantized reference is a NEW declared evaluation contract. It is
not silently identified with the original world-frame sampled reference.
Independent per-person errors, including the old bias-plus-jitter study, are
unsupported. All arithmetic is float64 numerical auditing, not interval proof.
"""
from dataclasses import dataclass

import numpy as np

from udc.interfaces import Observation
from udc.deployment.planners import demand_profile
from .geometry import construct_groups
from .observation import BoundedMeasurement
from .service import continuous_euclidean_service, _model


@dataclass(frozen=True)
class AnchoredReference:
    anchor: np.ndarray
    groups: tuple
    densities: tuple
    quantization_margin_m: float
    numerical_condition: bool


def anchored_reference(observation, room, config, quantum=1e-8, rounding_bound=1e-12):
    if not np.isfinite([quantum, rounding_bound]).all() or quantum <= 0 or rounding_bound < 0:
        raise ValueError('Positive finite quantum and nonnegative rounding bound required')
    anchor = np.asarray(observation.positions[0], dtype=float)
    relative = np.asarray(observation.positions) - anchor
    quantized = np.round(relative / quantum) * quantum
    margin = float(np.min(quantum / 2 - np.abs(relative - quantized)))
    local = Observation(quantized, observation.radii, observation.demand)
    groups, _ = construct_groups(BoundedMeasurement(local, 0., 'anchored_reference_v1'), room, config)
    densities = tuple(demand_profile(group.boundary, quantized[list(group.rows)],
                      observation.demand[list(group.rows)], config['bandwidth_m']) for group in groups)
    return AnchoredReference(anchor.copy(), groups, densities, margin, margin > 4 * rounding_bound)


def reference_service(reference, robots, radius):
    local_robots = np.asarray(robots).reshape(-1, 2) - reference.anchor
    values = [continuous_euclidean_service(group.boundary, density, local_robots, radius)['uncovered']
              for group, density in zip(reference.groups, reference.densities)]
    weights = np.asarray([group.demand_total for group in reference.groups], dtype=float)
    weights /= weights.sum()
    return {'groups': values, 'worst_group_uncovered': max(values),
            'weighted_uncovered': float(weights @ values), 'reference': 'anchored_quantized_external_v1'}


def _runs(mask):
    if not np.any(mask):
        return []
    if np.all(mask):
        return [np.arange(len(mask))]
    start = (int(np.flatnonzero(~mask)[0]) + 1) % len(mask)
    runs, active = [], []
    for offset in range(len(mask)):
        index = (start + offset) % len(mask)
        if mask[index]:
            active.append(index)
        elif active:
            runs.append(np.asarray(active, int))
            active = []
    if active:
        runs.append(np.asarray(active, int))
    return runs


def paired_annulus_bound(boundary, density, retained, greedy, translation_bound, radius=1., numeric_pad=1e-10, tracking_bound=0.):
    """Uniform upper bound on retained-minus-greedy unmet demand under translations.

    Directed target Hausdorff h implies a loss point lies in a greedy disk's
    inner annulus (r-h,r]. On a run with a uniform signed radial derivative,
    annulus length <=h/min_derivative. Non-monotone runs use their whole mass.
    """
    retained, greedy = np.asarray(retained, dtype=float).reshape(-1, 2), np.asarray(greedy, dtype=float).reshape(-1, 2)
    if not np.isfinite(retained).all() or not np.isfinite(greedy).all():
        raise ValueError('Finite robot positions required')
    if not np.isfinite([translation_bound, radius, tracking_bound]).all() or translation_bound < 0 or radius <= 0 or tracking_bound < 0:
        raise ValueError('Finite nonnegative translation bound and positive service radius required')
    if not len(greedy):
        return 0., {'directed_target_distance_m': 0., 'scope': 'greedy has no service'}
    if not len(retained):
        return 1., {'directed_target_distance_m': None, 'scope': 'retained has no service'}
    h = float(np.linalg.norm(greedy[:, None] - retained[None, :], axis=2).min(axis=1).max()) + 2 * tracking_bound
    if h == 0:
        return 0., {'directed_target_distance_m': 0., 'scope': 'greedy robot set contained in retained set'}
    h += 1e-12
    if h >= radius:
        return 1., {'directed_target_distance_m': h, 'scope': 'trivial large-displacement bound'}
    points, rho, lengths, total = _model(boundary, density)
    end = np.roll(points, -1, axis=0)
    tangent = (end - points) / lengths[:, None]
    rho_next = np.roll(rho, -1)
    segment_mass = lengths * (rho + rho_next) / 2
    epsilon, mass_bound, monotone, fallback = translation_bound + tracking_bound + 1e-12, 0., 0, 0
    for robot in greedy:
        relative = points - robot
        projection = np.clip(np.sum(relative * tangent, axis=1) * -1, 0, lengths)
        minimum = np.linalg.norm(relative + projection[:, None] * tangent, axis=1)
        maximum = np.maximum(np.linalg.norm(relative, axis=1), np.linalg.norm(end - robot, axis=1))
        eligible = (minimum - epsilon <= radius) & (maximum + epsilon >= radius - h)
        low = np.sum(relative * tangent, axis=1) - epsilon
        high = np.sum((end - robot) * tangent, axis=1) + epsilon
        for run in _runs(eligible):
            if np.all(low[run] > 0):
                derivative = min(1., float(low[run].min()) / radius)
            elif np.all(high[run] < 0):
                derivative = min(1., float(-high[run].max()) / radius)
            else:
                mass_bound += float(segment_mass[run].sum())
                fallback += 1
                continue
            arc_bound = min(float(lengths[run].sum()), h / derivative)
            rho_max = float(np.maximum(rho[run], rho_next[run]).max())
            mass_bound += min(float(segment_mass[run].sum()), rho_max * arc_bound)
            monotone += 1
    return min(1., mass_bound / total + numeric_pad), {'directed_target_distance_m': h,
             'monotone_runs': monotone, 'fallback_runs': fallback, 'scope': 'uniform common translation on declared anchored measure'}


def retention_certificate(reference, retained, greedy, model, error_bound, radius=1., delta=.002, tracking_bound=0.):
    if model != 'pure_common_translation' or not reference.numerical_condition:
        return {'status': 'UNCERTIFIED', 'accept_retained': False,
                'reason': 'unsupported_error_model' if model != 'pure_common_translation' else 'quantization_margin_insufficient'}
    if not np.isfinite([error_bound, radius, delta, tracking_bound]).all() or error_bound < 0 or radius <= 0 or delta < 0 or tracking_bound < 0:
        raise ValueError('Invalid model bound, radius or loss budget')
    retained_local = np.asarray(retained).reshape(-1, 2) - reference.anchor
    greedy_local = np.asarray(greedy).reshape(-1, 2) - reference.anchor
    rows = []
    effective_error = error_bound + tracking_bound
    for group, density in zip(reference.groups, reference.densities):
        lower_greedy = max(0., continuous_euclidean_service(group.boundary, density, greedy_local, radius + effective_error)['uncovered'] - 1e-10)
        upper_retained = (1. if effective_error > radius else min(1., continuous_euclidean_service(
            group.boundary, density, retained_local, radius - effective_error)['uncovered'] + 1e-10))
        paired, evidence = paired_annulus_bound(group.boundary, density, retained_local, greedy_local, error_bound, radius, tracking_bound=tracking_bound)
        upper = min(paired, max(0., upper_retained - lower_greedy))
        rows.append({'group_id': group.group_id, 'upper_loss': upper, 'greedy_lower': lower_greedy,
                     'retained_upper': upper_retained, 'paired_bound': paired, 'evidence': evidence})
    return {'status': 'MODEL_BOUND_NUMERICAL_AUDIT', 'accept_retained': all(row['upper_loss'] <= delta for row in rows),
            'groups': rows, 'delta': delta, 'error_bound_m': error_bound,
            'tracking_bound_m': tracking_bound,
            'scope': 'pure common translation plus declared rounding bound; anchored reference only, not original world-frame reference'}
