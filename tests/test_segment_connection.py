"""Complete segment predicate against the five-point sample."""
import numpy as np
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
