import numpy as np

from udc.interfaces import Observation
from udc.multigroup.observation import static_groups
from udc.multigroup.partition_stability import partition_stability_certificate


def obs(points):
    return Observation(np.asarray(points, float), np.ones(len(points)) * .2, np.ones(len(points)))


def test_certificate_survives_random_bounded_perturbations():
    original = obs([[0., 0.], [.6, 0.], [3., 0.], [3.6, 0.]])
    partition = static_groups(original, .95)
    assert partition_stability_certificate(original, partition, .95, .1)['certified']
    rng = np.random.default_rng(37)
    for _ in range(100):
        angle = rng.uniform(0, 2 * np.pi, 4)
        radius = rng.uniform(0, .1, 4)
        perturbed = obs(original.positions + radius[:, None] * np.c_[np.cos(angle), np.sin(angle)])
        assert static_groups(perturbed, .95) == partition


def test_near_threshold_adversarial_merge_has_no_certificate():
    original = obs([[10., 10.], [10.62, 10.], [11.58, 10.], [12.2, 10.]])
    partition = static_groups(original, .95)
    assert len(partition) == 2
    assert not partition_stability_certificate(original, partition, .95, .01)['certified']
    moved = original.positions + np.array([[.01, 0.], [.01, 0.], [-.01, 0.], [-.01, 0.]])
    assert len(static_groups(obs(moved), .95)) == 1
