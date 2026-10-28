"""Parallelize independent original-evaluator replays, preserving its comparisons."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[3]


def run(study, output, workers):
    study, output = Path(study).resolve(), Path(output).resolve()
    if not 1 <= workers <= (os.cpu_count() or 1):
        raise ValueError('Invalid replay worker count')
    output.mkdir(parents=True, exist_ok=False)
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        environment[name] = '1'
    verified = subprocess.run([sys.executable, str(ROOT / 'scripts/frozen_unit_worker.py'),
                               '--study', str(study), '--verify'], env=environment, cwd=ROOT,
                              capture_output=True, text=True, check=True)
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    local, slots = threading.local(), itertools.count()

    def initialize():
        local.cpu = next(slots)

    def unit_run(unit):
        folder = output / unit['unit_id']
        folder.mkdir()
        with (folder / 'result.json').open('wb') as stdout, (folder / 'error.log').open('wb') as stderr:
            code = subprocess.run([sys.executable, str(ROOT / 'scripts/replay_frozen_unit.py'),
                                   '--study', str(study), '--unit', unit['unit_id'], '--cpu', str(local.cpu)],
                                  cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0).returncode
        if code:
            raise RuntimeError('Frozen replay failed; inspect ' + str(folder))
        return json.loads((folder / 'result.json').read_text(encoding='utf-8'))

    values = []
    with ThreadPoolExecutor(max_workers=workers, initializer=initialize) as pool:
        for future in as_completed([pool.submit(unit_run, unit) for unit in manifest['units']]):
            values.append(future.result())
    mismatches = sorted(task for value in values for task in value['mismatches'])
    result = {'checked': sum(value['checked'] for value in values), 'mismatches': mismatches,
              'all_match': not mismatches, 'verified_receipts': sum(value['verified_receipts'] for value in values),
              'replay_workers': workers, 'independent_units': len(values),
              'implementation': 'Original frozen evaluator and exact original saved-metric comparisons; process dispatch only.',
              'frozen_verification': json.loads(verified.stdout)}
    (output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=32)
    args = parser.parse_args()
    print(json.dumps(run(args.study, args.output, args.workers), ensure_ascii=False, indent=2), flush=True)
