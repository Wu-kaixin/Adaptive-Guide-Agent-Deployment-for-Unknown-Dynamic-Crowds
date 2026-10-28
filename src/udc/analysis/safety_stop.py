"""Compare one fixed saved unsafe query on frozen original and explicit backend."""
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
import shapely
from shapely.geometry import LineString
from udc.multigroup import execution
from udc.experiments.static_multigroup import digest, write_json


def diagnose(folder, backend):
    folder, backend = Path(folder), Path(backend)
    free = shapely.from_wkb((folder / 'free.wkb').read_bytes())
    obstacle = shapely.from_wkb((folder / 'obstacle.wkb').read_bytes())
    with np.load(folder / 'fixed_query.npz', allow_pickle=False) as data:
        start, target, saved = data['start'], data['target'], data['saved_path']
    graph_start = time.perf_counter()
    graph = execution._graph(free, obstacle)
    graph_seconds = time.perf_counter() - graph_start
    path, length = execution._shortest(start, target, free, obstacle, graph=graph)
    original_seconds = time.perf_counter() - graph_start
    spec = importlib.util.spec_from_file_location('audited_acceleration', backend)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_graph, original_shortest = module.install_lazy(execution)
    try:
        lazy_start = time.perf_counter()
        lazy_path, lazy_length = execution._shortest(start, target, free, obstacle,
                                                   graph=execution._graph(free, obstacle))
        lazy_seconds = time.perf_counter() - lazy_start
    finally:
        execution._graph, execution._shortest = original_graph, original_shortest
    result = {'original_graph_seconds': graph_seconds, 'original_query_seconds': original_seconds,
              'lazy_query_seconds': lazy_seconds, 'original_path_matches_saved_bitwise': np.array_equal(path, saved),
              'lazy_path_matches_saved_bitwise': np.array_equal(lazy_path, saved),
              'original_lazy_path_bitwise_equal': np.array_equal(path, lazy_path),
              'original_length': length, 'lazy_length': lazy_length,
              'original_path': None if path is None else path.tolist(),
              'backend_sha256': digest(backend), 'shapely': shapely.__version__, 'geos': shapely.geos_version_string,
              'mode': 'fixed query diagnostic; not a new scientific episode or retry'}
    write_json(folder / 'backend_query_comparison.json', result)
    return result
