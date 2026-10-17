import numpy as np
import pytest

from udc.experiments.scene_closure import (
    perturb_guides, perturb_people, resolve_probe_seeds,
)


def test_person_and_guide_draws_are_reproducible_and_change_the_base():
    base = np.array([[12.0, 14.4], [12.7, 14.4], [12.0, 15.1]])
    first = perturb_people(base, 20, 0.04)
    second = perturb_people(base, 20, 0.04)
    other = perturb_people(base, 21, 0.04)
    assert np.array_equal(first, second)
    assert not np.array_equal(first, base)
    assert not np.array_equal(first, other)
    guides, shifts = perturb_guides(30.0, 1.0, 11, 20, 0.35)
    again, shifts_again = perturb_guides(30.0, 1.0, 11, 20, 0.35)
    assert np.array_equal(guides, again)
    assert np.array_equal(shifts, shifts_again)
    assert np.max(np.abs(shifts)) <= 0.35


def test_probe_seed_list_rejects_used_and_formal_seeds():
    spec = {'seeds': [20, 21, 22, 23, 24]}
    assert resolve_probe_seeds(spec, None) == [20, 21, 22, 23, 24]
    assert resolve_probe_seeds(spec, [22, 20]) == [20, 22]
    with pytest.raises(ValueError):
        resolve_probe_seeds(spec, [15])
    with pytest.raises(ValueError):
        resolve_probe_seeds(spec, [5000])
