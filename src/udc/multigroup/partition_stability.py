"""Sufficient connected-component stability certificate for bounded point moves."""
import numpy as np

from udc.interfaces import Observation
from .observation import static_groups


def partition_stability_certificate(observation, partition, link, perturbation):
    if not np.isfinite([link, perturbation]).all() or link <= 0 or perturbation < 0:
        raise ValueError('Positive link and nonnegative finite perturbation required')
    labels = np.full(len(observation.positions), -1, int)
    intra = link > 2 * perturbation
    for k, rows in enumerate(partition):
        index = np.asarray(rows, int)
        if not len(index) or np.any(labels[index] != -1):
            raise ValueError('Need a disjoint complete partition')
        labels[index] = k
        if len(index) > 1 and intra:
            subset = Observation(observation.positions[index], observation.radii[index], observation.demand[index])
            intra = len(static_groups(subset, link - 2 * perturbation)) == 1
    if np.any(labels < 0):
        raise ValueError('Need a complete partition')
    distances = np.linalg.norm(observation.positions[:, None] - observation.positions[None], axis=2)
    cross = distances[labels[:, None] != labels[None]]
    minimum_cross = float(cross.min()) if len(cross) else None
    separated = minimum_cross is None or minimum_cross > link + 2 * perturbation
    return {'certified': bool(intra and separated), 'within_groups_connected_at_reduced_link': bool(intra),
            'cross_groups_separated_at_expanded_link': bool(separated), 'minimum_cross_distance_m': minimum_cross,
            'perturbation_bound_m': perturbation,
            'scope': 'every point moves at most eta in Euclidean distance; grouping only, not target continuity'}
