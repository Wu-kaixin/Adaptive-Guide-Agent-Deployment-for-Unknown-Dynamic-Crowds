import numpy as np
import pytest
from shapely.geometry import box
from types import SimpleNamespace

from udc.multigroup.phase_retention import PhaseOption, select_phase, admissible


def option(name, euclidean, arc=.4, sampled=.5):
    return PhaseOption(name, np.array([0.]), np.array([[0., 0.]]), sampled, arc, euclidean, .1)


def test_selection_metrics_are_distinct_on_the_same_options():
    a, b = option('phase_0', .3, .4, .1), option('phase_1', .1, .2, .5)
    assert select_phase([a, b], 'sampled_arc_greedy')[0] is a
    assert select_phase([a, b], 'continuous_arc_greedy')[0] is b
    assert select_phase([a, b], 'continuous_euclidean_greedy')[0] is b


def test_retention_has_explicit_service_slack_and_releases_bad_anchor():
    best = option('phase_0', .2)
    anchor = option('anchor_continuous_euclidean_greedy', .201)
    chosen, evidence = select_phase([best, anchor], 'euclidean_retain_0.002')
    assert chosen is anchor and evidence['retained']
    assert evidence['observed_euclidean_slack'] <= .002
    bad = option('anchor_continuous_euclidean_greedy', .203)
    chosen, evidence = select_phase([best, bad], 'euclidean_retain_0.002')
    assert chosen is best and not evidence['retained']


def test_unavailable_anchor_cannot_be_retained_and_unknown_method_rejected():
    best = option('phase_0', .2)
    assert not select_phase([best], 'euclidean_retain_0.002')[1]['retained']
    with pytest.raises(ValueError):
        select_phase([best], 'unknown')


def test_admission_preserves_all_pair_euclidean_safety():
    domain = SimpleNamespace(free=box(-3, -3, 3, 3))
    assert not admissible([[0., 0.], [.74999, 0.]], domain, .75)
    assert admissible([[0., 0.], [.75001, 0.]], domain, .75)
    assert not admissible([[4., 0.]], domain, .75)


def test_real_shared_phase_pool_preserves_count_and_retention_bound():
    import yaml
    from pathlib import Path
    from udc.multigroup.scenarios import make_truth
    from udc.multigroup.observation import measure
    from udc.multigroup.geometry import construct_groups
    from udc.multigroup.phase_retention import phase_pool, GREEDY, METHODS
    root = Path(__file__).resolve().parents[1]
    spec = yaml.safe_load((root / 'configs/step1/static_multigroup_v2.yaml').read_text(encoding='utf-8'))
    truth = make_truth(1, 'circle', 930, 999)
    nominal = measure(truth, .08, 930)
    groups, domain = construct_groups(nominal, spec['room_m'], spec['model'])
    pool = phase_pool(groups[0], nominal, domain, 5, spec['model'])
    anchors = {m: select_phase(pool, m)[0].positions for m in GREEDY}
    shared = phase_pool(groups[0], nominal, domain, 5, spec['model'], anchors)
    assert all(len(c.positions) == 5 for c in shared)
    assert all(admissible(c.positions, domain, .75) for c in shared)
    for method in METHODS:
        chosen, evidence = select_phase(shared, method)
        assert any(chosen is c for c in shared)
        if method.startswith('euclidean_retain_'):
            assert evidence['observed_euclidean_slack'] <= float(method.rsplit('_', 1)[1]) + 1e-12
