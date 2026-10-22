"""Finish accounting from saved artifacts after an I/O interruption; no execution."""
from pathlib import Path
from collections import Counter
import argparse
import shutil

from rebuild_missing_data import OUTPUT, PROJECT, SUITES, initialize, read, write, sha

def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--suite', required=True, choices=list(SUITES))
    args = parser.parse_args(); name, kind, ref = SUITES[args.suite]
    if kind != 'all_phase': raise ValueError('Saved-accounting recovery only supports the shared demand runner')
    initialize(ref)
    import numpy as np
    from udc.experiments.demand_confirmation import independent_row
    root = OUTPUT / ('step1_' + name); manifest = read(root / 'manifest.json')['rows']
    audit = PROJECT / 'reports/project_rebuild_20261023' / ('saved_accounting_' + args.suite)
    audit.mkdir(parents=True, exist_ok=True)
    checkpoint = root / 'checkpoint.json'
    if checkpoint.exists() and not (audit / 'checkpoint_before.json').exists(): shutil.copy2(checkpoint, audit / 'checkpoint_before.json')
    results, recovered = [], []
    for row in manifest:
        directory = root / 'results' / row['task_id']; detail_path = directory / 'protocol_metrics.json'
        if detail_path.exists():
            detail = read(detail_path)
        else:
            metrics = read(directory / 'metrics.json')
            protected = {p.name: sha(p) for p in directory.iterdir() if p.is_file() and p.name in ('config.yaml', 'metrics.json', 'trajectory.npz', 'state.json')}
            with np.load(directory / 'trajectory.npz', allow_pickle=False) as saved:
                assert len(saved['positions']) == 1, 'Executed row needs a separate audit; never regenerate its protocol result automatically'
            assert metrics['termination_status'] in ('INITIALIZATION_INVALID', 'BOUNDARY_INVALID', 'CAPACITY_SHORTFALL', 'PLAN_SEARCH_EXHAUSTED')
            replay = independent_row(root, row)
            assert replay['criteria_consistent'] and replay['diagnostics_consistent'] and not replay['executed']
            assert protected == {name: sha(directory / name) for name in protected}
            detail = {**row, 'stage': 'input', 'target_valid': False, 'initial_paths_ok': False, 'executed': False,
                      'motion': None, 'planned_cost': None, 'fine_cost': None, 'actual_cost': None, 'recoveries': None,
                      'timing': {}, 'phase': None, 'status': metrics['termination_status'], 'success': False,
                      'offline_consistent': True, 'offline_success': False, 'failure_reason': metrics.get('failure_reason'),
                      'accounting_recovered_from_saved_evidence': True, 'scientific_execution_retried': False}
            write(detail_path, detail)
            recovered.append({'task_id': row['task_id'], 'protected_sha256_before_and_after': protected, 'reevaluation': replay})
        if detail['status'] in ('EXPERIMENT_ERROR', 'SAFETY_VIOLATION') or not detail['offline_consistent']:
            raise RuntimeError('Unresolved program/scientific audit failure: ' + row['task_id'])
        results.append({'task_id': row['task_id'], 'status': detail['status'], 'success': detail['success'], 'offline_consistent': detail['offline_consistent']})
    write(audit / 'recovery.json', {'operation': 'saved artifact read and independent rejudge only', 'execution_retries': 0,
          'interruption': 'PermissionError replacing reevaluated_metrics.json; original process log retained', 'recovered_rows': recovered})
    write(checkpoint, {'rows': results})
    summary = {'suite': args.suite, 'tasks': len(manifest), 'completed': len(results), 'statuses': dict(Counter(r['status'] for r in results)),
               'successes': sum(r['success'] for r in results), 'workers': 2, 'original_historical_data': False,
               'independent_confirmation': False, 'infrastructure_interruptions': len(recovered), 'execution_retries': 0, 'rows': results}
    write(root / 'reconstruction_summary.json', summary)
    print({k: v for k, v in summary.items() if k != 'rows'})

if __name__ == '__main__': main()
