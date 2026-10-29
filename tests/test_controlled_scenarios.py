import numpy as np

from udc.multigroup.controlled_scenarios import grid_truth, observed_safe_guides
from udc.multigroup.observation import measure, BoundedMeasurement


def test_population_is_controlled_separately_and_wall_variants_are_paired_translations():
    original = grid_truth(3, 5, 4200, 90173)
    translated = grid_truth(3, 5, 4200, 90173, .02)
    assert len(original.positions) == 75
    assert np.bincount(original.group_ids).tolist() == [25, 25, 25]
    np.testing.assert_array_equal(original.positions[:, 1], translated.positions[:, 1])
    np.testing.assert_allclose(np.diff(original.positions, axis=0), np.diff(translated.positions, axis=0), rtol=0, atol=4e-15)
    assert abs((translated.positions[:, 0] - translated.radii).min() - .02) < 1e-14
    np.testing.assert_array_equal(original.demand, translated.demand)


def test_largest_near_wall_case_has_safe_nested_initialization_without_truth_in_filter():
    truth = grid_truth(5, 9, 4200, 90173, .02)
    measured = measure(truth, .04, 9)
    measurement = BoundedMeasurement(measured.observation, .12, 'test_bound')
    model = {'guide_radius_m': .2, 'clearance_m': .35, 'guide_distance_m': .75}
    guides = observed_safe_guides(measurement, 80, [40., 30.], model)
    assert guides.shape == (80, 2)
    assert np.array_equal(guides[:25], observed_safe_guides(measurement, 25, [40., 30.], model))
    assert (np.linalg.norm(guides[:, None] - measurement.observation.positions[None], axis=2)
            >= measurement.observation.radii + .12 + .2 + .35).all()
    i, j = np.triu_indices(80, 1)
    assert np.linalg.norm(guides[i] - guides[j], axis=1).min() >= .75
