import numpy as np
import pytest

from udc.analysis.directional_world_witness import directional_translations, exact_history_feasible


def test_disk_intersection_ray_search_reaches_beyond_conservative_triangle_ball():
    positions = np.array([[0.,0.],[1.,0.]])
    observations = np.array([positions + [.08,0], positions + [.07,0]])
    moves = directional_translations(positions, observations, .12, [0,1], directions=4)
    longest = max(moves, key=lambda r: np.linalg.norm(r['translation_m']))
    assert np.linalg.norm(longest['translation_m']) > 4 * (.12 - .08) * .9
    assert all(exact_history_feasible(positions + r['translation_m'], observations, .12) for r in moves)


def test_group_ray_moves_only_selected_people_and_preserves_all_error_balls():
    positions = np.array([[0.,0.],[10.,0.]])
    observations = np.array([positions + [[.08,0],[0,.10]]])
    moves = directional_translations(positions, observations, .12, [0], directions=4)
    for move in moves:
        candidate = positions.copy()
        candidate[0] += move['translation_m']
        assert np.array_equal(candidate[1], positions[1])
        assert exact_history_feasible(candidate, observations, .12)


def test_infeasible_initial_world_cannot_seed_directional_witness_search():
    with pytest.raises(ValueError, match='initially feasible'):
        directional_translations(np.zeros((1,2)), np.array([[[.13,0.]]]), .12, [0])
