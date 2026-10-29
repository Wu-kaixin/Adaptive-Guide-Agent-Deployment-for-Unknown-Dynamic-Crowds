from udc.analysis.buffer_bound_audit import exact_ring_covers, generic_example


def test_exact_binary_ring_classifier_includes_edges_and_excludes_exterior():
    ring = [[0.,0.],[4.,0.],[4.,4.],[0.,4.],[0.,0.]]
    assert exact_ring_covers(ring, [2.,2.])
    assert exact_ring_covers(ring, [0.,1.])
    assert not exact_ring_covers(ring, [-.001,1.])


def test_current_round_fillet_has_full_precision_counterexample_to_generic_cosine_bound():
    _, _, witness = generic_example()
    assert witness is not None
    assert witness['exact_inside_claimed_disk'] and witness['exact_outside_buffer_polygon']
    assert not witness['shapely_covers_point']
    assert witness['distance_outside_actual_buffer_m'] > .0007
