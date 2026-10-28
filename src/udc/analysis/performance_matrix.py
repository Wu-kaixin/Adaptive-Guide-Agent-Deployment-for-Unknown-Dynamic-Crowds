"""Bounded cold-process comparison on preselected, saved actions."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def case_run(output, index, study, task, cpu, baseline_from=None):
    import psutil
    values = []
    for mode in ('baseline', 'lazy'):
        if mode == 'baseline' and baseline_from:
            previous = json.loads((Path(baseline_from) / f'case{index:02d}_baseline_receipt.json').read_text(encoding='utf-8'))
            values.append(previous | {'reused_prior_benchmark': True})
            continue
        folder = output / f'case{index:02d}_{mode}'
        stdout, stderr = output / f'case{index:02d}_{mode}.stdout.log', output / f'case{index:02d}_{mode}.stderr.log'
        command = [sys.executable, str(ROOT / 'scripts/benchmark_frozen_execution.py'),
                   '--study', str(study), '--task', task, '--mode', mode,
                   '--output', str(folder), '--cpu', str(cpu)]
        environment = os.environ.copy()
        for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
            environment[name] = '1'
        started = time.perf_counter()
        with stdout.open('wb') as out, stderr.open('wb') as error:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=out, stderr=error,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            timeout = 180 if mode == 'baseline' else 300
            try:
                code = process.wait(timeout=timeout)
                status = 'completed' if code == 0 else 'diagnostic_error'
            except subprocess.TimeoutExpired:
                parent = psutil.Process(process.pid)
                descendants = parent.children(recursive=True)
                for child in reversed(descendants):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                parent.kill()
                process.wait()
                code, status = None, 'PROFILE_INCOMPLETE_WALL_BUDGET'
        value = {'mode': mode, 'status': status, 'exit_code': code,
                 'wall_seconds': time.perf_counter() - started, 'wall_budget_seconds': timeout,
                 'folder': str(folder)}
        if (folder / 'result.json').exists():
            value['result'] = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
        save(output / f'case{index:02d}_{mode}_receipt.json', value)
        values.append(value)
    result = {'index': index, 'study': str(study), 'task_id': task, 'cpu': cpu, 'modes': values}
    if all(v['status'] == 'completed' for v in values):
        result['execution_speedup'] = values[0]['result']['execution_seconds'] / max(values[1]['result']['execution_seconds'], 1e-12)
    save(output / f'case{index:02d}_comparison.json', result)
    print(json.dumps({'index': index, 'task': task, 'statuses': [v['status'] for v in values],
                      'speedup': result.get('execution_speedup'),
                      'lazy_matches_saved': values[1].get('result', {}).get('all_match')}, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--baseline-from')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    checkpoint = json.loads((ROOT / 'runs/step1_pause_optimize_20261028/checkpoint/checkpoint.json').read_text(encoding='utf-8'))
    if not checkpoint['archive_crc_verified']:
        raise ValueError('Preservation must complete first')
    base = ROOT / 'runs/static_multigroup_20261026'
    selected = [
        ('confirmation_v2_frozen', 'K1_circle_s2000_e0.12_sufficient_projected_mass_minimax'),
        ('confirmation_v2_frozen', 'K2_circle_s2000_e0.04_sufficient_projected_mass_demand'),
        ('confirmation_v2_frozen', 'K2_concave_s2003_e0.00_sufficient_projected_mass_demand'),
        ('confirmation_v2_frozen', 'K5_circle_s2000_e0.12_insufficient_projected_mass_minimax'),
        ('confirmation_v2_frozen', 'K5_circle_s2000_e0.12_sufficient_projected_mass_minimax'),
        ('confirmation_v2_frozen', 'K5_ellipse_s2000_e0.12_sufficient_projected_mass_minimax'),
        ('capacity_smoke_v2', 'K5_concave_s1201_e0.12_sufficient_projected_mass_minimax'),
        ('capacity_smoke_v2', 'K5_ellipse_s1201_e0.12_sufficient_projected_mass_minimax'),
        ('confirmation_v2_frozen', 'K1_irregular_s2000_e0.12_sufficient_projected_mass_minimax'),
        ('confirmation_v2_frozen', 'K5_ellipse_s2000_e0.12_moderate_projected_mass_minimax')]
    for study, task in selected:
        if not (base / study / 'results' / task / 'receipt.json').is_file():
            raise FileNotFoundError('Preselected case missing: ' + task)
    save(output / 'selection.json', {'frozen_before_timing': True,
         'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
         'selection_reason': 'Fixed small/heavy, supply, geometry and two failure witnesses; diagnostic, not independent confirmation.',
         'cases': selected, 'baseline_from': args.baseline_from,
         'backend_sha256': hashlib.sha256((ROOT / 'src/udc/multigroup/accelerated_execution.py').read_bytes()).hexdigest(),
         'checkpoint_archive_sha256': checkpoint['archive_sha256']})
    (output / 'backend_snapshot.py').write_bytes((ROOT / 'src/udc/multigroup/accelerated_execution.py').read_bytes())
    # Three independent process lanes; each paired case stays on its same CPU.
    groups = [list(range(lane, len(selected), 3)) for lane in range(3)]
    def lane_run(lane):
        return [case_run(output, index, base / selected[index][0], selected[index][1], 4 + 2 * lane, args.baseline_from)
                for index in groups[lane]]
    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in as_completed([pool.submit(lane_run, lane) for lane in range(3)]):
            results.extend(future.result())
    save(output / 'summary.json', {'results': sorted(results, key=lambda value: value['index']),
         'lazy_all_saved_outputs_match': all(v['modes'][1].get('result', {}).get('all_match') for v in results),
         'scientific_retries': 0, 'new_independent_scenes': 0})


if __name__ == '__main__':
    main()
