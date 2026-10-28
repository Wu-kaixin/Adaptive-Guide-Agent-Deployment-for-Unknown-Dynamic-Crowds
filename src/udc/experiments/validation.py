"""Manifest-driven Step-1 validation, resume, re-evaluation and summaries."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path.cwd()

from udc.experiments.accounting import ledger_row, read_metrics, record_process_error
from udc.experiments.runner import METHODS, PROTOCOLS, _atomic_text, experiment, reevaluate_result


def _range(pair):
    if not isinstance(pair, list) or len(pair) != 2 or any(type(x) is not int for x in pair):
        raise ValueError('Seed ranges must be [first, last] integers')
    if pair[0] < 0 or pair[1] < pair[0]:
        raise ValueError('Invalid seed range')
    return range(pair[0], pair[1] + 1)


def load_manifest(path):
    path = Path(path)
    manifest = yaml.safe_load(path.read_text(encoding='utf-8'))
    required = {'schema_version', 'frozen_protocol', 'gates', 'scenarios'}
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise ValueError(f'Manifest requires {sorted(required)}')
    frozen = manifest['frozen_protocol']
    config_root = (path.resolve().parent / manifest.get('config_root', '..')).resolve()
    manifest['_config_root'] = str(config_root)
    if set(frozen['methods']) != METHODS or set(frozen['protocols']) != PROTOCOLS:
        raise ValueError('Manifest must freeze every method and both protocols')
    names = set()
    for item in manifest['scenarios']:
        if not isinstance(item, dict) or not {'name', 'config', 'fixed_n', 'stochastic'} <= set(item):
            raise ValueError('Each scenario requires name/config/fixed_n/stochastic')
        if item['name'] in names:
            raise ValueError(f"Duplicate scenario {item['name']}")
        names.add(item['name'])
        config = config_root / item['config']
        if not config.is_file():
            raise ValueError(f'Missing scenario config: {config}')
        if type(item['fixed_n']) is not int or item['fixed_n'] < 3:
            raise ValueError('fixed_n must be an integer >= 3')
        if not item['stochastic'] and (type(item.get('seed')) is not int or item['seed'] < 0):
            raise ValueError('Deterministic scenarios require an explicit nonnegative seed')
    return manifest


def build_tasks(manifest, output, phase, smoke=False):
    frozen = manifest['frozen_protocol']
    config_root = Path(manifest.get('_config_root', ROOT))
    seeds = list(_range(frozen[f'{phase}_seeds']))
    if smoke:
        seeds = seeds[:1]
    tasks = []
    for scenario in manifest['scenarios']:
        scenario_seeds = seeds if scenario['stochastic'] else [scenario['seed']]
        for seed in scenario_seeds:
            for method in frozen['methods']:
                for protocol in frozen['protocols']:
                    relative = Path(scenario['name']) / method / protocol / f'seed_{seed}'
                    tasks.append({
                        'scenario': scenario['name'],
                        'config': str(config_root / scenario['config']),
                        'seed': seed, 'method': method, 'protocol': protocol,
                        'fixed_n': scenario['fixed_n'] if protocol == 'fixed_n' else None,
                        'expected_statuses': scenario.get('expected_statuses'),
                        'output': str(Path(output) / relative),
                    })
    return tasks


def _metric_view(metrics):
    if not isinstance(metrics, dict):
        return {
            'active_guides': None, 'planned_max_gap': None, 'actual_max_gap': None,
            'max_gap_limit': None, 'max_tracking_error': None,
            'minimum_net_clearance': None, 'wall_time': None, 'task_sha256': None,
        }
    clearances = metrics.get('minimum_net_clearances') or {}
    return {
        'active_guides': metrics.get('active_guides'),
        'planned_max_gap': metrics.get('planned_max_gap'),
        'actual_max_gap': metrics.get('actual_max_gap'),
        'max_gap_limit': metrics.get('max_gap_limit'),
        'max_tracking_error': metrics.get('max_tracking_error'),
        'minimum_net_clearance': min(
            (value for value in clearances.values() if value is not None), default=None,
        ),
        'wall_time': metrics.get('wall_time'),
        'task_sha256': metrics.get('task_sha256'),
    }


def _batch_row(task, metrics, ledger, refusal, error_text):
    """Ledger status is authoritative. A refusal does not rewrite saved files."""
    process_failed = bool(ledger['unabsorbed_program_error'] or (
        error_text is not None and not refusal))
    no_saved_task = (not ledger['has_metrics']) and error_text is not None
    finished_record = bool(ledger['has_metrics'] and not ledger['unabsorbed_program_error'])
    completed = bool(finished_record and (error_text is None or refusal) and not process_failed)
    if process_failed or no_saved_task:
        success = False
        status = 'EXPERIMENT_ERROR'
    else:
        success = bool(ledger['success'])
        status = ledger['termination_status']
    if process_failed or refusal or error_text is not None:
        expectation_met = False
    else:
        expected = task['expected_statuses']
        expectation_met = True if expected is None else status in expected
    # Process errors do not contribute a batch time. Scientific failures keep
    # the saved time for inspection; success-only plots still ignore it.
    reported_time = None if process_failed or not ledger['has_metrics'] else ledger['physical_simulation_time']
    return {
        **{key: task[key] for key in [
            'scenario', 'seed', 'method', 'protocol', 'fixed_n', 'output'
        ]},
        'completed': completed,
        'expectation_met': expectation_met,
        'success': success,
        'termination_status': status,
        **_metric_view(metrics if ledger['has_metrics'] else None),
        'simulation_time': reported_time,
        'physical_success': ledger['physical_success'],
        'physical_termination_status': ledger['physical_termination_status'],
        'physical_simulation_time': ledger['physical_simulation_time'],
        'process_error': bool(ledger['process_error'] or process_failed),
        'output_refusal': bool(refusal),
        'error': None if not error_text else error_text.strip().splitlines()[-1],
    }


def _run_task(task, resume):
    directory = Path(task['output'])
    identity = {
        'scenario': task['scenario'], 'method': task['method'], 'protocol': task['protocol'],
    }
    error_text = None
    refusal = False
    try:
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1):
            experiment(
                task['config'], task['output'], task['seed'], False,
                task['protocol'], task['fixed_n'], resume, task['method'],
            )
    except Exception as exc:
        error_text = traceback.format_exc()
        refusal = isinstance(exc, FileExistsError)
        # Refusing to enter an existing directory must not plant an error scene
        # on a saved result. A real exception records the scene once and leaves
        # metrics and any earlier state in place.
        if not refusal:
            record_process_error(directory, error_text, identity)
    metrics, _readable = read_metrics(directory)
    ledger = ledger_row(directory, identity)
    return _batch_row(task, metrics, ledger, refusal, error_text)


def summarize(rows, output, manifest_hash, phase, workers):
    output = Path(output)
    fields = list(rows[0]) if rows else []
    temporary = output / 'runs.csv.tmp'
    with temporary.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    os.replace(temporary, output / 'runs.csv')
    groups = {}
    for row in rows:
        key = f"{row['scenario']}|{row['method']}|{row['protocol']}"
        group = groups.setdefault(key, {'runs': 0, 'completed': 0, 'successful': 0})
        group['runs'] += 1
        group['completed'] += int(row['completed'])
        group['successful'] += int(row['success'])
    for group in groups.values():
        group['success_rate'] = (group['successful'] / group['runs']
                                 if group['runs'] else None)
    summary = {
        'schema_version': 1, 'phase': phase,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'manifest_sha256': manifest_hash, 'workers': workers,
        'tasks': len(rows), 'completed': sum(row['completed'] for row in rows),
        'experiment_errors': sum(not row['completed'] for row in rows),
        'output_refusals': sum(bool(row.get('output_refusal')) for row in rows),
        'expectations_met': sum(row['expectation_met'] for row in rows),
        'successful': sum(row['success'] for row in rows),
        'termination_counts': {
            status: sum(row['termination_status'] == status for row in rows)
            for status in sorted({row['termination_status'] for row in rows})
        },
        'groups': groups,
    }
    _atomic_text(output / 'summary.json', json.dumps(summary, indent=2,
                                                     allow_nan=False) + '\n')
    return summary


def plot_summary(summary_path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    summary_path = Path(summary_path)
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    method_stats = {}
    for key, group in summary['groups'].items():
        _, method, protocol = key.split('|')
        stats = method_stats.setdefault((method, protocol), [0, 0])
        stats[0] += group['successful']; stats[1] += group['runs']
    labels = [f'{method}\n{protocol}' for method, protocol in method_stats]
    rates = [success / completed if completed else 0
             for success, completed in method_stats.values()]
    with summary_path.with_name('runs.csv').open(encoding='utf-8') as stream:
        rows=list(csv.DictReader(stream))
    def averages(field, successful_only=True):
        values=[]
        for method,protocol in method_stats:
            selected=[]
            for row in rows:
                if row['method']!=method or row['protocol']!=protocol:
                    continue
                if successful_only and row['success']!='True':
                    continue
                if row[field] not in {'',None}:
                    selected.append(float(row[field]))
            values.append(sum(selected)/len(selected) if selected else 0.)
        return values
    fig, axes = plt.subplots(2,2,figsize=(14,9),constrained_layout=True)
    panels=[
        ('Success rate',rates,(0,1.05)),
        ('Active guides (successful runs)',averages('active_guides'),None),
        ('Arrival simulation time [s]',averages('simulation_time'),None),
        ('Minimum net clearance [m]',averages('minimum_net_clearance'),None),
    ]
    for ax,(title,values,ylim) in zip(axes.ravel(),panels):
        ax.bar(range(len(labels)),values,color='#3778bf')
        ax.set(xticks=range(len(labels)),xticklabels=labels,title=title)
        if ylim: ax.set_ylim(*ylim)
        ax.grid(axis='y',alpha=.2)
    fig.suptitle(f"Step 1 {summary['phase']} validation")
    fig.savefig(summary_path.with_name('validation_metrics.png'), dpi=150)
    plt.close(fig)


def reevaluate_tree(output):
    """Recompute physical criteria. This does not clear a program-error scene.

    ``consistent`` compares the saved metrics success flag with the recomputed
    criteria. It does not promote a process error to a ledger success.
    """
    rows = []
    for metrics_path in sorted(Path(output).rglob('metrics.json')):
        try:
            result = reevaluate_result(metrics_path.parent)
            original = json.loads(metrics_path.read_text(encoding='utf-8'))
            ledger = ledger_row(metrics_path.parent)
            rows.append({
                'path': str(metrics_path.parent.relative_to(output)),
                'original_success': original['success'],
                'reevaluated_success': result['success'],
                'consistent': original['success'] == result['success'],
                'ledger_success': ledger['success'],
                'ledger_status': ledger['termination_status'],
                'process_error': ledger['process_error'],
                'unabsorbed_program_error': ledger['unabsorbed_program_error'],
            })
        except Exception as error:
            rows.append({'path': str(metrics_path.parent.relative_to(output)),
                         'original_success': None, 'reevaluated_success': None,
                         'consistent': False, 'error': repr(error)})
    report = {'runs': len(rows), 'consistent': sum(row['consistent'] for row in rows),
              'process_errors': sum(bool(row.get('process_error')) for row in rows),
              'results': rows}
    _atomic_text(Path(output) / 'reevaluation_summary.json', json.dumps(
        report, indent=2
    ) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', default=str(ROOT / 'experiments' / 'acceptance_manifest.yaml'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--phase', choices=['development', 'validation'],
                        default='development')
    parser.add_argument('--workers', default='auto')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--reevaluate', action='store_true')
    parser.add_argument('--plot-only', action='store_true')
    args = parser.parse_args()
    output = Path(args.output)
    if args.reevaluate:
        report = reevaluate_tree(output)
        print(json.dumps(report, indent=2))
        if report['consistent'] != report['runs']:
            raise SystemExit(1)
        return
    if args.plot_only:
        plot_summary(output / 'summary.json'); return
    manifest_path = Path(args.manifest)
    manifest = load_manifest(manifest_path)
    raw = manifest_path.read_bytes()
    manifest_hash = hashlib.sha256(raw).hexdigest()
    tasks = build_tasks(manifest, output, args.phase, args.smoke)
    output.mkdir(parents=True, exist_ok=True)
    frozen = {
        'schema_version': 1, 'phase': args.phase, 'smoke': args.smoke,
        'manifest_sha256': manifest_hash, 'task_count': len(tasks), 'tasks': tasks,
    }
    run_manifest = output / 'run_manifest.json'
    if run_manifest.exists():
        previous = json.loads(run_manifest.read_text(encoding='utf-8'))
        if previous != frozen:
            raise ValueError('Output contains a different frozen run manifest')
    else:
        _atomic_text(run_manifest, json.dumps(frozen, indent=2) + '\n')
    available = (len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity')
                 else os.cpu_count() or 1)
    workers = min(available if args.workers == 'auto' else int(args.workers),
                  max(1, len(tasks)))
    if workers < 1:
        parser.error('workers must be positive')
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_task, task, args.resume): task for task in tasks}
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (row['scenario'], row['seed'], row['method'], row['protocol']))
    summary = summarize(rows, output, manifest_hash, args.phase, workers)
    plot_summary(output / 'summary.json')
    print(json.dumps({key: value for key, value in summary.items() if key != 'groups'}, indent=2))
    if (summary['experiment_errors'] or summary['output_refusals']
            or summary['expectations_met'] != summary['tasks']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
