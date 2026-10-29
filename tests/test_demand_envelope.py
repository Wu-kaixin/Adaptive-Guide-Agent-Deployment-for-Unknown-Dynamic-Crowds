import numpy as np
from scipy.special import logsumexp
from shapely.geometry import box

from udc.interfaces import Observation
from udc.multigroup.observation import BoundedMeasurement
from udc.multigroup.demand_envelope import gaussian_field_bounds


def test_uncertain_positions_and_all_queries_stay_inside_log_field_envelope():
    obs = Observation(np.array([[0., 0.], [1., .5]]), np.full(2, .2), np.array([1., 4.]))
    measurement = BoundedMeasurement(obs, .12, 'bounded')
    region = box(.2, -.1, .7, .3)
    result = gaussian_field_bounds(measurement, (0, 1), region, 1.2)
    rng = np.random.default_rng(4012)
    for _ in range(64):
        angles = rng.uniform(0, 2 * np.pi, 2)
        actual = obs.positions + .12 * np.c_[np.cos(angles), np.sin(angles)]
        point = rng.uniform([.2, -.1], [.7, .3])
        value = logsumexp(np.log(obs.demand) - np.sum((actual - point) ** 2, axis=1) / (2 * 1.2 ** 2))
        assert result['log_field_lower'] <= value <= result['log_field_upper']
    assert result['curve_mass_bound'] is None
    assert result['service_loss_bound'] is None


def test_far_field_does_not_underflow_into_a_fake_zero_mass_bound():
    measurement = BoundedMeasurement(Observation(np.array([[0., 0.]]), np.array([.2]), np.ones(1)), .12, 'bounded')
    result = gaussian_field_bounds(measurement, (0,), box(1e6, 1e6, 1e6 + 1, 1e6 + 1), 1.2)
    assert np.isfinite(result['log_field_lower'])
    assert result['log_field_upper'] < -1e10
