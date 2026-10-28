import numpy as np
import pytest

from udc.boundary import Boundary
from udc.multigroup.service import continuous_euclidean_service
from udc.multigroup.translation_service_bound import paired_annulus_bound, _runs, retention_certificate


def circle():
    angle = np.arange(96) * 2 * np.pi / 96
    points = np.c_[3 * np.cos(angle), 3 * np.sin(angle)]
    lengths = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    return Boundary(points, points, np.r_[0., np.cumsum(lengths[:-1])], float(lengths.sum()))


def test_cyclic_runs_do_not_split_wraparound_mass():
    runs = _runs(np.array([True, True, False, False, True]))
    assert len(runs) == 1 and list(runs[0]) == [4, 0, 1]


@pytest.mark.parametrize('seed', range(8))
def test_uniform_translation_loss_bound_contains_sampled_true_losses(seed):
    rng = np.random.default_rng(seed)
    boundary = circle()
    density = 1 + np.arange(96) / 96
    greedy = boundary.deployment[[0, 24, 48, 72]]
    retained = greedy + rng.normal(size=greedy.shape) * .0003
    upper, _ = paired_annulus_bound(boundary, density, retained, greedy, .12)
    for _ in range(15):
        bias = rng.normal(size=2)
        bias *= rng.uniform(0, .12) / np.linalg.norm(bias)
        actual = (continuous_euclidean_service(boundary, density, retained + bias, 1.)['uncovered']
                  - continuous_euclidean_service(boundary, density, greedy + bias, 1.)['uncovered'])
        assert actual <= upper


def test_identical_or_empty_layout_bounds():
    boundary = circle()
    points = boundary.deployment[[0, 24]]
    rho = np.ones(96)
    assert paired_annulus_bound(boundary, rho, points, points, .12)[0] == 0
    assert paired_annulus_bound(boundary, rho, points, [], .12)[0] == 0
    assert paired_annulus_bound(boundary, rho, [], points, .12)[0] == 1


def test_tracking_contract_bounds_independent_endpoint_errors():
    boundary, rng = circle(), np.random.default_rng(938)
    rho = 1 + np.arange(96) / 96
    greedy = boundary.deployment[[0, 24, 48, 72]]
    retained = greedy + .0002
    upper, _ = paired_annulus_bound(boundary, rho, retained, greedy, .12, tracking_bound=.0001)
    for _ in range(30):
        bias = rng.uniform(-.05, .05, 2)
        g = greedy + bias + rng.uniform(-.00005, .00005, greedy.shape)
        r = retained + bias + rng.uniform(-.00005, .00005, retained.shape)
        loss = continuous_euclidean_service(boundary, rho, r, 1.)['uncovered'] - continuous_euclidean_service(boundary, rho, g, 1.)['uncovered']
        assert loss <= upper


@pytest.mark.parametrize('model', ['bounded_disk', 'common_bias', 'common_bias_plus_independent_jitter'])
def test_old_independent_or_mixed_noise_never_gets_translation_certificate(model):
    certificate = retention_certificate(None, [], [], model, .12)
    assert certificate['status'] == 'UNCERTIFIED'
    assert certificate['accept_retained'] is False
