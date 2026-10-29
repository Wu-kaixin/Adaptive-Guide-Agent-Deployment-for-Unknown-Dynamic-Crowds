"""Log-domain Gaussian nodal-demand bounds for an uncertain spatial region.

These are pointwise field bounds, NOT bounds on curve mass or service loss.
No planar area is treated as demand mass, and no true reference is an input.
"""
import numpy as np
from scipy.special import logsumexp
from shapely.geometry import Point


def gaussian_field_bounds(measurement, rows, region, bandwidth_m):
    sigma = float(bandwidth_m)
    if (not np.isfinite(sigma) or sigma <= 0 or region.is_empty or not region.is_valid
            or not np.isfinite(region.bounds).all()):
        raise ValueError('Positive bandwidth and finite nonempty valid region required')
    rows = np.asarray(rows)
    if not np.issubdtype(rows.dtype, np.integer):
        raise ValueError('Observed person rows must be integer indices')
    observation = measurement.observation
    if (rows.ndim != 1 or not len(rows) or len(set(rows)) != len(rows)
            or np.any(rows < 0) or np.any(rows >= len(observation.positions))):
        raise ValueError('Need distinct valid observed person rows')
    centers = observation.positions[rows]
    epsilon = measurement.position_error_bound_m
    pad = 1e-9
    low_x, low_y, high_x, high_y = region.bounds
    corners = np.array([[low_x, low_y], [low_x, high_y], [high_x, low_y], [high_x, high_y]])
    distance_lower = np.maximum(0., np.array([region.distance(Point(p)) for p in centers]) - epsilon - pad)
    distance_upper = np.linalg.norm(corners[None] - centers[:, None], axis=2).max(axis=1) + epsilon + pad
    log_weights = np.log(observation.demand[rows])
    log_lower = float(logsumexp(log_weights - distance_upper ** 2 / (2 * sigma ** 2)))
    log_upper = float(logsumexp(log_weights - distance_lower ** 2 / (2 * sigma ** 2)))
    if not np.isfinite([log_lower, log_upper]).all() or log_lower > log_upper:
        raise ValueError('Invalid finite log-domain Gaussian envelope')
    return {'log_field_lower': log_lower, 'log_field_upper': log_upper,
            'scope': 'unnormalized Gaussian nodal field on every point in declared region',
            'position_error_bound_m': epsilon, 'numeric_construction_pad_m': pad,
            'curve_mass_bound': None, 'service_loss_bound': None,
            'formal_interval_arithmetic': False}
