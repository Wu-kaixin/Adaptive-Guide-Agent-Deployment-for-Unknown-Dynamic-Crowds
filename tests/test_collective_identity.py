import numpy as np
import json

from udc.interfaces import Observation
from udc.multigroup.collective_identity import certificate
from udc.multigroup.partition_stability import partition_stability_certificate
from udc.multigroup.observation import static_groups


def observed(points):
    points = np.asarray(points, float)
    return Observation(points, np.full(len(points), .2), np.ones(len(points)))


def test_surrounding_connected_neighbors_collectively_attach_an_uncertain_center():
    angles = np.arange(8) * 2 * np.pi / 8
    obs = observed(np.vstack([[0., 0.], .8 * np.c_[np.cos(angles), np.sin(angles)]]))
    rows = (tuple(range(9)),)
    base = partition_stability_certificate(obs, rows, 1., .15)
    assert not base['certified']
    result = certificate(obs, rows, 1., .15, base)
    assert result['certified']
    assert json.loads(json.dumps(result))['certified']
    assert result['collective_coverage_groups'][0]['attachments'][0]['witness_row'] == 0
    rng = np.random.default_rng(812)
    for _ in range(64):
        angles = rng.uniform(0, 2 * np.pi, 9)
        shifted = observed(obs.positions + .15 * np.c_[np.cos(angles), np.sin(angles)])
        assert len(static_groups(shifted, 1.)) == 1


def test_a_single_uncertain_bridge_is_not_accepted():
    obs = observed([[0, 0], [.85, 0]])
    rows = ((0, 1),)
    base = partition_stability_certificate(obs, rows, 1., .15)
    assert not certificate(obs, rows, 1., .15, base)['certified']


def test_possible_cross_group_merge_stays_uncertified():
    obs = observed([[0, 0], [1.1, 0]])
    rows = ((0,), (1,))
    base = partition_stability_certificate(obs, rows, 1., .15)
    assert not certificate(obs, rows, 1., .15, base)['certified']
