import pytest
import yaml

from udc.experiments.static_multigroup import ROOT
from udc.multigroup.session import StaticDeploymentSession


def model():
    return yaml.safe_load((ROOT / 'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))['model']


@pytest.mark.parametrize('field,value', [
    ('guide_distance_m', .74), ('wall_distance_m', .54),
    ('dt_s', float('nan')), ('max_speed_m_s', 0.),
    ('numeric_tolerance_m', 1e-3), ('hold_steps', True),
    ('max_speed_m_s', 'fast'), ('tracking_bound_m', True), ('true_service_delta', True),
    ('routing_sides', [1]), ('execution_policy', 'adaptive_forward')])
def test_invalid_model_never_creates_journal_or_releases_motion(tmp_path, field, value):
    config = model() | {field: value}
    with pytest.raises(ValueError):
        StaticDeploymentSession([[2., 2.]], [40., 30.], config, tmp_path / 'invalid')
    assert not (tmp_path / 'invalid').exists()


def test_more_conservative_physical_distances_are_allowed(tmp_path):
    config = model() | {'guide_distance_m': .85, 'wall_distance_m': .65}
    with StaticDeploymentSession([[2., 2.]], [40., 30.], config, tmp_path / 'conservative'):
        assert (tmp_path / 'conservative/session_receipt.json').exists()
