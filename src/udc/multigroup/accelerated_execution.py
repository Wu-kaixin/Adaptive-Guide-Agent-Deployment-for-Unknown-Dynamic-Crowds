"""Index an unchanged free-space geometry before the original graph builder.

No coordinates, predicates, node ordering, edges, distances or tolerances are
changed here. Shapely's prepared geometry caches its spatial search structure.
"""
from functools import wraps
import heapq

import numpy as np
import shapely
from shapely.geometry import LineString


BACKEND = 'prepared_free_space_graph_v1'


def prepare_and_build(original, free, obstacle):
    shapely.prepare(free)
    return original(free, obstacle)


def install(execution_module):
    original = execution_module._graph
    if getattr(original, '_udc_prepared_backend', False):
        raise ValueError('Prepared backend already installed')

    @wraps(original)
    def indexed(free, obstacle):
        return prepare_and_build(original, free, obstacle)

    indexed._udc_prepared_backend = True
    execution_module._graph = indexed
    return original


class LazyVisibilityGraph:
    """The original complete finite graph, with rows computed only on demand.

    Node order, segment orientation and edge arithmetic follow execution._graph
    and navigation._shortest. This does not prune an edge or change the search
    space. Dijkstra may stop when the target is popped, since all weights are
    nonnegative and strict relaxation cannot subsequently improve that target.
    """
    def __init__(self, free, obstacle):
        pieces = [obstacle] if obstacle.geom_type == 'Polygon' else list(getattr(obstacle, 'geoms', ()))
        coordinates = []
        for piece in pieces:
            if piece.geom_type == 'Polygon':
                coordinates.extend(piece.exterior.coords[:-1])
                for ring in piece.interiors:
                    coordinates.extend(ring.coords[:-1])
        points = np.asarray(coordinates, dtype=float).reshape(-1, 2)
        # Keep original node filtering before preparing the same immutable geometry.
        if len(points):
            points = points[shapely.covers(free, shapely.points(points))]
        self.points, self.free, self.obstacle = points, free, obstacle
        self.nodes = list(points)
        self.stats = {'vertices': len(points), 'expanded_base_rows': 0,
                      'queried_base_pairs': 0, 'full_candidate_pairs': len(points) * (len(points) - 1) // 2}
        shapely.prepare(free)

    def row(self, index, distance=None, distances=None):
        others = np.r_[np.arange(index), np.arange(index + 1, len(self.points))]
        first = np.maximum(index, others)
        second = np.minimum(index, others)
        weights = np.linalg.norm(self.points[first] - self.points[second], axis=1)
        # Original strict relaxation cannot change a distance when this test fails.
        # Skip only its expensive visibility predicate, with identical edge weights.
        if distances is not None:
            possible = distance + weights < np.asarray(distances)[others + 2]
            others, first, second, weights = others[possible], first[possible], second[possible], weights[possible]
        if not len(others):
            self.stats['expanded_base_rows'] += 1
            return []
        # Original _graph uses the higher node index first, including its rounding.
        segments = shapely.linestrings(np.stack([self.points[first], self.points[second]], axis=1))
        valid = shapely.covers(self.free, segments)
        self.stats['expanded_base_rows'] += 1
        self.stats['queried_base_pairs'] += len(others)
        return [(int(other) + 2, float(weight)) for other, weight in zip(others[valid], weights[valid])]


def lazy_shortest(start, target, free, obstacle, graph):
    direct = LineString([start, target])
    if free.covers(direct) and (obstacle is None or not direct.crosses(obstacle)):
        return np.asarray([start, target], dtype=float), float(direct.length)
    nodes = [np.asarray(start), np.asarray(target), *graph.nodes]
    endpoint = [[] for _ in nodes]
    for i in range(2, len(nodes)):
        for j in (0, 1):
            segment = LineString([nodes[i], nodes[j]])
            if free.covers(segment) and (obstacle is None or not segment.crosses(obstacle)):
                length = float(np.linalg.norm(nodes[i] - nodes[j]))
                endpoint[i].append((j, length))
                endpoint[j].append((i, length))
    distances, previous = [float('inf')] * len(nodes), [-1] * len(nodes)
    distances[0], queue = 0., [(0., 0)]
    while queue:
        distance, vertex = heapq.heappop(queue)
        if distance != distances[vertex]:
            continue
        if vertex == 1:
            break
        neighbors = endpoint[vertex] if vertex < 2 else endpoint[vertex] + graph.row(vertex - 2, distance, distances)
        # Endpoint IDs 0/1 precede the already ascending base IDs, as in the original.
        for neighbor, weight in neighbors:
            candidate = distance + weight
            if candidate < distances[neighbor]:
                distances[neighbor], previous[neighbor] = candidate, vertex
                heapq.heappush(queue, (candidate, neighbor))
    if not np.isfinite(distances[1]):
        return None, float('inf')
    indices, vertex = [], 1
    while vertex >= 0:
        indices.append(vertex)
        vertex = previous[vertex]
    return np.asarray([nodes[index] for index in indices[::-1]]), distances[1]


def install_lazy(execution_module):
    if getattr(execution_module._graph, '_udc_prepared_backend', False):
        raise ValueError('An acceleration backend is already installed')
    original_graph, original_shortest = execution_module._graph, execution_module._shortest

    def graph(free, obstacle):
        return LazyVisibilityGraph(free, obstacle)

    def shortest(start, target, free, obstacle, extra_nodes=(), graph=None):
        if not isinstance(graph, LazyVisibilityGraph):
            return original_shortest(start, target, free, obstacle, extra_nodes, graph)
        return lazy_shortest(start, target, free, obstacle, graph)

    graph._udc_prepared_backend = True
    execution_module._graph, execution_module._shortest = graph, shortest
    return original_graph, original_shortest
