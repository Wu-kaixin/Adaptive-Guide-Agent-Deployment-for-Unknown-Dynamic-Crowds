import numpy as np
from shapely.geometry import box

from udc.multigroup.path_gate import segment_gate


def test_analytic_gate_rejects_full_segment_even_when_geometry_has_no_obstacle():
    current = np.array([[2., 1.], [3., 1.5]])
    evidence = segment_gate([2., 1.], [4., 1.], current, 0, box(0, 0, 10, 10), [10, 10],
                            {'guide_distance_m': .75, 'wall_distance_m': .4})
    assert evidence['reason'] == 'analytic_stationary_robot_clearance'
    assert evidence['center_distance_m'] == .5


def test_difference_gate_rejects_crossing_hole():
    free = box(0, 0, 10, 10).difference(box(4, 4, 6, 6))
    evidence = segment_gate([2., 5.], [8., 5.], np.array([[2., 5.]]), 0, free, [10, 10],
                            {'guide_distance_m': .75, 'wall_distance_m': .4})
    assert evidence['reason'] == 'segment_difference_outside_serial_free_space'


def test_gate_does_not_relax_strict_robot_threshold():
    current = np.array([[2., 2.], [3., 2.749999]])
    evidence = segment_gate([2., 2.], [4., 2.], current, 0, box(0, 0, 10, 10), [10, 10],
                            {'guide_distance_m': .75, 'wall_distance_m': .4})
    assert evidence is not None


def test_full_precision_seed2004_unsafe_edge_is_rejected_before_execution():
    a = np.array([21.96297872300922, 7.4167191652670175])
    b = np.array([22.423088338450714, 8.711777890646898])
    blocker = np.array([22.757533170976306, 7.492582622108118])
    evidence = segment_gate(a, b, np.array([a, blocker]), 0, box(0, 0, 50, 50), [50, 50],
                            {'guide_distance_m': .75, 'wall_distance_m': .4})
    assert evidence['reason'] == 'analytic_stationary_robot_clearance'
    assert .72330820310 < evidence['center_distance_m'] < .72330820311
