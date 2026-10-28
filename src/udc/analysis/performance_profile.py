"""Independent frozen-action timing and bitwise parity; never writes old results."""
import argparse
import cProfile
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.025)


def run(args):
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    import psutil
    psutil.Process().cpu_affinity([args.cpu])
    root = Path(__file__).resolve().parents[3]
    study, output = Path(args.study).resolve(), Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(study / 'source_snapshot/src'))
    import numpy as np
    import shapely
    from udc.experiments import static_multigroup as frozen
    from udc.multigroup import execution as execution_module
    folder = study / 'results' / args.task
    receipt = json.loads((folder / 'receipt.json').read_text(encoding='utf-8'))
    if any(digest(folder / name) != sha for name, sha in receipt.items()):
        raise ValueError('Original saved task hash mismatch')
    saved = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
    task, config = saved['task'], saved['model']
    state = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
    metrics = json.loads((folder / 'metrics.json').read_text(encoding='utf-8'))
    with np.load(folder / 'trajectory.npz', allow_pickle=False) as stored:
        golden = {key: stored[key].copy() for key in stored.files}
    truth, _, measurement = frozen._load_inputs(study, task)
    start = time.perf_counter()
    spec = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))['spec']
    groups, domain = frozen.construct_groups(measurement, spec['room_m'], config)
    construction = time.perf_counter() - start
    if args.mode in ('prepared', 'lazy'):
        fixed_backend = output.parent / 'backend_snapshot.py'
        path = (Path(args.backend_file).resolve() if args.backend_file else
                fixed_backend if fixed_backend.exists() else root / 'src/udc/multigroup/accelerated_execution.py')
        loader = importlib.util.spec_from_file_location('runtime_prepared_backend', path)
        backend = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(backend)
        (backend.install_lazy if args.mode == 'lazy' else backend.install)(execution_module)
    graph_function, shortest_function = execution_module._graph, execution_module._shortest
    graphs, shortest_seconds = [], [0.]
    status = {'task_id': args.task, 'mode': args.mode, 'pid': os.getpid(),
              'started_at': datetime.now().astimezone().isoformat(timespec='seconds'),
              'scientific_source': str(Path(frozen.__file__).resolve()),
              'original_receipt_sha256': digest(folder / 'receipt.json'), 'graphs': graphs}

    def graph(free, obstacle):
        entry = {'graph_index': len(graphs), 'state': 'building'}
        graphs.append(entry)
        atomic(output / 'status.json', status | {'stage': 'graph_build'})
        before = free.wkb
        started = time.perf_counter()
        value = graph_function(free, obstacle)
        if args.mode == 'lazy':
            entry['lazy_stats'] = value.stats
            entry.update(state='complete', seconds=time.perf_counter() - started, vertices=len(value.nodes),
                         candidate_pairs=value.stats['full_candidate_pairs'], directed_edges=None,
                         geometry_unchanged=before == free.wkb)
        else:
            entry.update(state='complete', seconds=time.perf_counter() - started, vertices=len(value[0]),
                         candidate_pairs=len(value[0]) * (len(value[0]) - 1) // 2,
                         directed_edges=sum(map(len, value[1])), geometry_unchanged=before == free.wkb)
        atomic(output / 'status.json', status | {'stage': 'execution'})
        return value

    def shortest(*a, **kw):
        started = time.perf_counter()
        value = shortest_function(*a, **kw)
        shortest_seconds[0] += time.perf_counter() - started
        return value

    execution_module._graph, execution_module._shortest = graph, shortest
    atomic(output / 'status.json', status | {'stage': 'execution'})
    profiler = cProfile.Profile() if args.profile else None
    started = time.perf_counter()
    if profiler:
        profiler.enable()
    result = frozen.execute(golden['positions'][0], golden['targets'], domain, spec['room_m'], config)
    if profiler:
        profiler.disable()
        profiler.dump_stats(output / 'profile.pstats')
    execution_seconds = time.perf_counter() - started
    atomic(output / 'status.json', status | {'stage': 'independent_evaluation'})
    started = time.perf_counter()
    fresh = frozen.evaluate(result, golden['targets'], golden['target_groups'], groups, measurement,
                            truth, spec['room_m'], config)
    evaluation_seconds = time.perf_counter() - started
    array_matches = {}
    for key in ('positions', 'velocities', 'assignment'):
        actual, expected = np.ascontiguousarray(getattr(result, key)), np.ascontiguousarray(golden[key])
        array_matches[key] = actual.dtype == expected.dtype and actual.shape == expected.shape and actual.tobytes() == expected.tobytes()
    mismatches = [key for key in fresh if fresh[key] != metrics.get(key)]
    value = status | {'stage': 'complete', 'profiled': bool(profiler), 'construction_seconds': construction,
                      'execution_seconds': execution_seconds, 'graph_seconds': sum(g['seconds'] for g in graphs),
                      'shortest_seconds': shortest_seconds[0], 'evaluation_seconds': evaluation_seconds,
                      'array_bitwise_matches': array_matches, 'metric_mismatches': mismatches,
                      'execution_status_matches': result.status == state['execution_status'],
                      'evidence_matches': result.evidence == state['evidence'],
                      'paths_match': list(result.paths) == state['paths'],
                      'backend_sha256': digest(path) if args.mode != 'baseline' else None,
                      'shapely_version': shapely.__version__}
    value['all_match'] = (all(array_matches.values()) and not mismatches and value['execution_status_matches']
                          and value['evidence_matches'] and value['paths_match']
                          and all(g['geometry_unchanged'] for g in graphs))
    atomic(output / 'result.json', value)
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)
    if not value['all_match']:
        raise ValueError('Frozen execution parity failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mode', choices=['baseline', 'prepared', 'lazy'], required=True)
    parser.add_argument('--cpu', type=int, default=0)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--backend-file')
    run(parser.parse_args())
