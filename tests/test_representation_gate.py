import numpy as np
import pytest

from udc.multigroup.representation_gate import serial_target_admissible


def test_gate_rejects_known_analytic_pass_polygon_interior():
    points = np.array([[0., 0.], [.758, 0.]])
    assert np.linalg.norm(points[0] - points[1]) > .75
    assert not serial_target_admissible(points, .75)


def test_gate_accepts_separated_targets_and_rejects_unsafe_targets():
    assert serial_target_admissible([[0, 0], [.77, 0]], .75)
    assert not serial_target_admissible([[0, 0], [.74, 0]], .75)
    assert serial_target_admissible([], .75)


def test_gate_validates_input():
    with pytest.raises(ValueError):
        serial_target_admissible([[np.nan, 0]], .75)
