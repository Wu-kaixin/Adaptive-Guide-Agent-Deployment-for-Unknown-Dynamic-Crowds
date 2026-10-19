"""Fixed-input check after admitting a boundary-coincident guide start.

The complete segment predicate stays in place. This entry only replays saved
people and full-precision targets.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from threadpoolctl import threadpool_limits

from udc.experiments.corner_handoff import run_case
from udc.experiments.execution_progress import replay

V2 = Path('runs/step1_nonconvex_confirmation_v2')
SMALL = (
    ('elongated', 30, 'pairwise_euclidean'),
    ('wide_u', 30, 'equal_arc'),
    ('wide_u', 30, 'projected_mass'),
    ('wide_u', 30, 'pairwise_euclidean'),
    ('wide_u', 26, 'equal_arc'),
)


def _task(scene, seed, method, output):
    cell = V2 / 'inputs' / scene / f'seed_{seed}'
    record = json.loads((cell / 'input_record.json').read_text(encoding='utf-8'))
    destination = Path(output) / scene / f'seed_{seed}' / method
    return {
        'mode': 'both', 'scene': scene, 'seed': seed, 'method': method,
        'count': record['count'], 'v2': str(V2), 'destination': str(destination),
        'task_id': f'{scene}__seed_{seed}__{method}',
    }


def run_small(output, workers):
    tasks = [_task(*item, output) for item in SMALL]
    pending = [task for task in tasks if not (Path(task['destination']) / 'metrics.json').is_file()]
    if workers == 1 or len(pending) <= 1:
        for task in pending:
            print(json.dumps(run_case(task)), flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_case, task): task for task in pending}
            for future in as_completed(futures):
                print(json.dumps(future.result()), flush=True)
    seed22_dest = Path(output) / 'wide_u_seed22_equal_arc'
    if not (seed22_dest / 'metrics.json').is_file():
        with threadpool_limits(limits=1):
            row = replay(
                Path('runs/step1_nonconvex_record_rebuild_v1/inputs/wide_u/seed_22/input.yaml'),
                Path('runs/step1_nonconvex_record_rebuild_v1/repair_regression/wide_u/seed_22/equal_arc'),
                seed22_dest,
            )
        print(json.dumps({'task_id': 'wide_u_seed22_equal_arc', **{k: row.get(k) for k in ('status', 'success', 'time_s', 'replans')}}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runs/step1_navigation_control_consistency/small'))
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 2:
        raise ValueError('Use 1 or 2 episode workers')
    run_small(args.output, args.workers)


if __name__ == '__main__':
    main()
