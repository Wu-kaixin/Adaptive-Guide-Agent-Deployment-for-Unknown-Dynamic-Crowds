import numpy as np

from udc.analysis.feasible_world_witness import translation_grid, admissibility, crosses_budget
from udc.multigroup.observation import StaticTruth


def test_translations_respect_entire_observation_history_and_do_not_modify_it():
    positions = np.array([[2., 2.], [2.6, 2.]])
    observations = np.array([positions + [.03, 0], positions + [0, -.08]])
    saved = observations.copy()
    translations = translation_grid(positions, observations, .12)
    assert len(translations) == 32
    assert np.array_equal(saved, observations)
    assert all(np.linalg.norm(observations - (positions + v), axis=-1).max() < .12 for v in translations)
    assert translation_grid(positions, observations, .07) == []


def test_physical_invalid_alternate_world_cannot_be_used_as_witness():
    config = {'guide_radius_m': .2, 'clearance_m': .35, 'wall_distance_m': .55,
              'guide_distance_m': .75, 'group_link_m': .95}
    truth = StaticTruth([[2., 2.], [2.6, 2.]], [.2, .2], [1., 1.], [0, 0])
    observations = np.array([truth.positions])
    assert admissibility(truth, observations, [np.array([[4., 4.]])], [8., 8.], config, .12)['admissible_before_reference']
    bad = admissibility(truth, observations, [np.array([[2., 2.]])], [8., 8.], config, .12)
    assert not bad['admissible_before_reference']
    assert bad['layouts'][0]['human_clearance_m'] < 0
    assert not crosses_budget({'reference_defined': True, 'max_group_net_loss': .001}, bad, .002, .00001)


def test_witness_requires_both_admissible_worlds_and_a_strict_margin():
    baseline = {'reference_defined': True, 'max_group_net_loss': .00199}
    assert not crosses_budget(baseline, {'reference_defined': True, 'max_group_net_loss': .002005}, .002, .00001)
    assert crosses_budget(baseline, {'reference_defined': True, 'max_group_net_loss': .003}, .002, .00001)
    assert not crosses_budget({'reference_defined': True, 'max_group_net_loss': .003},
                             {'reference_defined': True, 'max_group_net_loss': .004}, .002, .00001)
