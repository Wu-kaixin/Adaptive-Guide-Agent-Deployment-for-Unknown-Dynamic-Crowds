import pytest

from udc.analysis.phase_retention_analysis import primary_analysis, score_difference_bounds


def sample(missing=False):
    spec = {'group_counts': [2], 'shapes': ['concave'], 'seeds': {'start': 1, 'count': 2},
            'base_measurement_models': ['a', 'b'], 'statistics': {'bootstrap_seed': 17, 'bootstrap_repetitions': 1000}}
    rows = []
    for seed in (1, 2):
        for mode, control, treatment in [('a', 1., 0.), ('b', 3., 0. if seed == 1 else 2.)]:
            for method, value in [('continuous_euclidean_greedy', control), ('euclidean_retain_0.002', treatment)]:
                if missing and seed == 2 and mode == 'b' and method.startswith('euclidean_retain_'):
                    continue
                rows.append({'sequence_id': f'K2_concave_s{seed}_{mode}', 'kind': 'planning',
                             'perturbation_m': .001, 'layout': method, 'matched_target_rms_m': value})
    return {'spec': spec}, rows


def test_models_stay_in_the_same_independent_seed_block():
    manifest, rows = sample()
    result = primary_analysis(manifest, rows)
    assert result['scheduled_independent_blocks'] == 2
    assert result['strata'][0]['valid_blocks'] == 2
    assert result['equal_stratum_mean_delta_m'] == pytest.approx(-1.5)
    assert result['paired_physical_block_bootstrap_95_interval_m'] == pytest.approx([-2., -1.])


def test_one_missing_model_keeps_the_physical_block_in_sensitivity_bounds():
    manifest, rows = sample(missing=True)
    result = primary_analysis(manifest, rows)
    assert result['strata'][0]['missing_blocks'] == 1
    assert result['equal_stratum_mean_delta_m'] == -2.
    assert result['full_denominator_mean_bounds_m'] == pytest.approx([-2., 10.5])
    assert result['paired_physical_block_bootstrap_95_interval_m'] is None


def test_score_bounds_keep_known_half_of_incomplete_pair():
    assert score_difference_bounds(2., None, 50.) == (-48., 2.)
    with pytest.raises(ValueError):
        score_difference_bounds(51., 2., 50.)
