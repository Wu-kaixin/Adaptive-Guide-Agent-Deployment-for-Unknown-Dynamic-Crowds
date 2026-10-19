"""Complete segment predicate against the five-point sample."""
import numpy as np
import pytest
from shapely.geometry import Point, Polygon

from udc.navigation import segment_in_free, segment_in_free_sampled
from udc.navigation.paths import ENDPOINT_TOLERANCE_M


class _Corridor:
    """Wide corridor whose conservative obstacle contains a narrow notch and a hole.

    Physical free space is the whole corridor, so a chord through the notch is
    not rejected by the physical test. Five uniform samples miss both intrusions.
    """

    def __init__(self):
        corridor = Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0)])
        notch = Polygon([(3.2, 0.0), (3.4, 0.0), (3.4, 1.2), (3.2, 1.2)])
        hole = Polygon([(6.2, 0.4), (6.4, 0.4), (6.4, 0.8), (6.2, 0.8)])
        self.free = corridor.difference(notch).difference(hole)
        self.physical_free = corridor
        self.conservative_obstacle = corridor.difference(self.free)

    def signed_free_distance(self, point):
        geometry = Point(np.asarray(point, dtype=float))
        distance = float(self.free.boundary.distance(geometry))
        return distance if self.free.covers(geometry) else -distance


def test_sparse_samples_accept_a_chord_through_a_narrow_notch_and_a_hole():
    domain = _Corridor()
    start, end = np.array([0.0, 0.6]), np.array([10.0, 0.6])
    assert segment_in_free_sampled(start, end, domain)
    records = []
    assert segment_in_free(start, end, domain, audit=records.append) is False
    assert records and records[0]['reason'] == 'five_samples_within_tolerance_but_segment_penetrates'
    assert records[0]['witness_penetration_m'] > ENDPOINT_TOLERANCE_M
    assert records[0]['sample_penetration_m'] <= ENDPOINT_TOLERANCE_M


def test_installed_boundary_edge_and_closing_seam_stay_legal():
    domain = _Corridor()
    # Top edge, including the polygon's closing seam, lies on the boundary.
    assert segment_in_free(np.array([0.0, 2.0]), np.array([10.0, 2.0]), domain)
    assert segment_in_free(np.array([0.0, 2.0]), np.array([5.0, 2.0]), domain)
    # A side of the notch is an installed boundary edge, not a shortcut through it.
    assert segment_in_free(np.array([3.2, 0.0]), np.array([3.2, 1.2]), domain)
    # The hole ring is a boundary. Crossing the hole is not.
    assert segment_in_free(np.array([6.2, 0.4]), np.array([6.4, 0.4]), domain)
    assert segment_in_free(np.array([6.2, 0.4]), np.array([6.4, 0.8]), domain) is False


def _axis_aligned_penetration(start, end, bounds):
    """Exact max penetration of a horizontal or vertical chord into an AABB.

    This is the reference. It does not call the segment predicate.
    """
    xmin, ymin, xmax, ymax = bounds
    start = np.asarray(start, float)
    end = np.asarray(end, float)
    if abs(start[1] - end[1]) < 1e-15:
        y = float(start[1])
        x0, x1 = sorted((float(start[0]), float(end[0])))
        if y < ymin or y > ymax:
            return 0.0
        left, right = max(x0, xmin), min(x1, xmax)
    elif abs(start[0] - end[0]) < 1e-15:
        x = float(start[0])
        y0, y1 = sorted((float(start[1]), float(end[1])))
        if x < xmin or x > xmax:
            return 0.0
        left, right = max(y0, ymin), min(y1, ymax)
        xmin, xmax, ymin, ymax = ymin, ymax, xmin, xmax
        y = x
    else:
        raise ValueError('reference only covers axis-aligned chords')
    if left >= right - 1e-15:
        return 0.0
    center = 0.5 * (xmin + xmax)
    if left <= center <= right:
        horizontal = center - xmin
    else:
        horizontal = max(min(left - xmin, xmax - left), min(right - xmin, xmax - right))
    vertical = min(y - ymin, ymax - y)
    return float(min(horizontal, vertical))


def test_exact_rectangle_penetration_agrees_with_the_complete_predicate():
    from shapely.geometry import Polygon
    obstacle = Polygon([(3.0, 0.0), (4.0, 0.0), (4.0, 1.0), (3.0, 1.0)])
    room = Polygon([(-1.0, -1.0), (10.0, -1.0), (10.0, 3.0), (-1.0, 3.0)])
    domain = type('Domain', (), {})()
    domain.free = room.difference(obstacle)
    domain.physical_free = room
    domain.conservative_obstacle = obstacle
    bounds = (3.0, 0.0, 4.0, 1.0)
    chord = (np.array([0.0, 0.5]), np.array([8.0, 0.5]))
    edge = (np.array([3.0, 0.0]), np.array([4.0, 0.0]))
    shallow = (np.array([3.0 + 1e-9, 0.5]), np.array([1.0, 0.5]))
    deep = (np.array([3.0 + 1e-6, 0.5]), np.array([1.0, 0.5]))
    assert _axis_aligned_penetration(*chord, bounds) == pytest.approx(0.5)
    assert segment_in_free(*chord, domain) is False
    assert _axis_aligned_penetration(*edge, bounds) == pytest.approx(0.0)
    assert segment_in_free(*edge, domain) is True
    assert _axis_aligned_penetration(*shallow, bounds) <= 1e-7
    assert segment_in_free(*shallow, domain) is True
    assert _axis_aligned_penetration(*deep, bounds) > 1e-7
    assert segment_in_free(*deep, domain) is False


def test_tolerance_accepts_a_nanometre_contact_and_rejects_a_micrometre_penetration():
    domain = _Corridor()
    shallow = np.array([3.2 + 1e-9, 0.6])
    deep = np.array([3.2 + 1e-6, 0.6])
    outside = np.array([1.0, 0.6])
    assert segment_in_free(shallow, outside, domain)
    assert segment_in_free(deep, outside, domain) is False


def test_a_chord_through_the_hole_is_rejected_when_samples_miss_it():
    domain = _Corridor()
    # x = 4, 5, 6, 7, 8. The hole is x in [6.2, 6.4], so every sample is outside it.
    start, end = np.array([4.0, 0.6]), np.array([8.0, 0.6])
    assert segment_in_free_sampled(start, end, domain)
    assert segment_in_free(start, end, domain) is False
    assert domain.free.interiors
