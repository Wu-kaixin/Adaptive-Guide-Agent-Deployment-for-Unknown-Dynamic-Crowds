import numpy as np
import pytest
import shapely
from shapely.geometry import Point, Polygon, box

from udc.multigroup.accelerated_execution import install, prepare_and_build, LazyVisibilityGraph, lazy_shortest
from udc.multigroup.execution import _graph
from udc.navigation.paths import _shortest


@pytest.mark.parametrize('kind', ['box', 'holes', 'disk', 'components'])
def test_preparation_preserves_complete_graph_and_geometry(kind):
    room = box(0, 0, 10, 10)
    obstacle = {'box': box(4, 2, 6, 8),
                'holes': Polygon([(2, 2), (8, 2), (8, 8), (2, 8)], holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]]),
                'disk': Point(5, 5).buffer(2, quad_segs=4),
                'components': box(4, -1, 6, 11)}[kind]
    free = room.difference(obstacle)
    original_wkb = free.wkb
    original = _graph(free, obstacle)
    accelerated = prepare_and_build(_graph, free, obstacle)
    assert free.wkb == original_wkb
    assert np.array_equal(np.asarray(original[0]), np.asarray(accelerated[0]))
    assert original[1] == accelerated[1]
    assert shapely.is_prepared(free)


def test_install_is_explicit_and_refuses_duplicate_install():
    class Module:
        _graph = staticmethod(_graph)
    module = Module()
    original = install(module)
    assert original is _graph
    with pytest.raises(ValueError, match='already installed'):
        install(module)


@pytest.mark.parametrize('seed', range(12))
def test_lazy_shortest_preserves_original_path_bits_and_length(seed):
    rng = np.random.default_rng(seed)
    obstacles = shapely.union_all([Point(point).buffer(.4, quad_segs=2)
                                  for point in rng.uniform(2, 8, size=(5, 2))])
    free = box(0, 0, 10, 10).difference(obstacles)
    start, target = np.array([1., 5.]), np.array([9., 5.])
    eager = _shortest(start, target, free, obstacles, graph=_graph(free, obstacles))
    lazy = lazy_shortest(start, target, free, obstacles, LazyVisibilityGraph(free, obstacles))
    assert eager[1] == lazy[1]
    assert eager[0].tobytes() == lazy[0].tobytes()


def test_lazy_keeps_disconnected_failure():
    obstacle = box(4, -1, 6, 11)
    free = box(0, 0, 10, 10).difference(obstacle)
    path, length = lazy_shortest([1., 5.], [9., 5.], free, obstacle, LazyVisibilityGraph(free, obstacle))
    assert path is None and np.isinf(length)


@pytest.mark.parametrize('coordinates', [
    [(4, 2), (6, 2), (6, 8), (4, 8)],
    [(4, 2), (6, 2), (6, 2), (6, 8), (4, 8)],
    [(4, 4.999999999), (6, 4.999999999), (6, 5.000000001), (4, 5.000000001)]])
def test_lazy_preserves_equal_cost_and_duplicate_node_ties(coordinates):
    obstacle = Polygon(coordinates)
    free = box(0, 0, 10, 10).difference(obstacle)
    start, target = [1., 5.], [9., 5.]
    expected = _shortest(start, target, free, obstacle, graph=_graph(free, obstacle))
    actual = lazy_shortest(start, target, free, obstacle, LazyVisibilityGraph(free, obstacle))
    assert expected[1] == actual[1]
    assert expected[0].tobytes() == actual[0].tobytes()
