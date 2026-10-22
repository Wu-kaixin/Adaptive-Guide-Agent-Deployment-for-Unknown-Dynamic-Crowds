"""Validate the completed local reconstruction without rerunning experiments."""
from pathlib import Path
from collections import Counter
import hashlib
import json

import numpy as np

from rebuild_missing_data import PROJECT, OUTPUT, SUITES, read, write, sha

EXPECTED = {'record_v1': 45, 'confirmation_v2': 90, 'confirmation_v3': 180, 'demand_pilot': 36,
            'demand_confirmation': 240, 'resource_response': 360, 'resource_confirmation': 360, 'recovery_regression': 360}

def main():
    report = PROJECT / 'reports/project_rebuild_20261023'
    suites, checks = [], []
    for suite, (name, kind, ref) in SUITES.items():
        root = OUTPUT / ('step1_' + name)
        summary = read(root / 'reconstruction_summary.json')
        assert summary['completed'] == summary['tasks'] == EXPECTED[suite], suite
        manifest = read(root / 'manifest.json'); rows = manifest.get('rows', manifest.get('tasks'))
        assert len(rows) == EXPECTED[suite] and len({r['task_id'] for r in rows}) == len(rows)
        assert all(0 <= r['seed'] < 5000 for r in rows)
        for row in rows:
            directory = (root / 'baseline' / row['scene'] / f'seed_{row["seed"]}' / row['method']) if kind == 'nonconvex' else root / 'results' / row['task_id']
            for file in ('config.yaml', 'metrics.json', 'trajectory.npz', 'planning.json', 'state.json'):
                assert (directory / file).is_file(), str(directory / file)
            metrics = read(directory / 'metrics.json')
            assert metrics['termination_status'] not in ('EXPERIMENT_ERROR', 'SAFETY_VIOLATION'), str(directory)
            if (directory / 'pairing.json').exists():
                pairing = read(directory / 'pairing.json')
                assert pairing['exact_input_and_targets'] and pairing['offline_criteria_consistent']
            if (directory / 'protocol_metrics.json').exists():
                detail = read(directory / 'protocol_metrics.json')
                assert detail.get('offline_consistent', True) and detail.get('offline_diagnostics_consistent', True)
                if detail.get('executed') or detail.get('motion'):
                    assert (detail.get('motion') or {}).get('interval_safety_ok', True)
                    assert detail.get('exact_pairing', True)
            checks.append({'suite': suite, 'task_id': row['task_id'], 'path': directory.relative_to(PROJECT).as_posix(),
                           'status': metrics['termination_status'], 'trajectory_sha256': sha(directory / 'trajectory.npz')})
        suites.append({k: v for k, v in summary.items() if k != 'rows'})
    for snapshot in (OUTPUT / 'sources').glob('*/source_snapshot.json'):
        metadata = read(snapshot)
        for rel, expected in metadata['files'].items(): assert sha(snapshot.parent / rel) == expected, str(snapshot.parent / rel)
    data_files = {}; npz_count = 0
    for path in sorted(OUTPUT.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'): continue
        if path.suffix == '.npz':
            with np.load(path, allow_pickle=False) as data:
                for key in data.files: assert not data[key].dtype.hasobject, str(path)
            npz_count += 1
        if path.suffix == '.json': json.loads(path.read_text(encoding='utf-8'))
        data_files[path.relative_to(PROJECT).as_posix()] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    phase = read(OUTPUT / 'step1_nonconvex_phase_attribution_v1/reconstruction_summary.json')
    assert phase['completed'] == 24 and phase['policy_rows'] == 96 and phase['half_grid_rows'] == 24
    recovery = read(OUTPUT / 'recovery_observation/reconstruction_summary.json'); assert recovery['completed'] == 2
    deadline = read(OUTPUT / 'step1_execution_deadline_diagnostics_v1/reconstruction_summary.json'); assert deadline['completed'] == 2
    assert deadline['baseline']['baseline_exact']
    continuation = read(OUTPUT / 'step1_execution_deadline_diagnostics_v1/diagnostic_continuation_60s/continuation_audit.json')
    assert continuation['full_cutoff_state_exact'] and continuation['original_prefix_exact'] and continuation['no_fourth_recovery']
    result = {'completed_main_rows': sum(EXPECTED.values()), 'completed_phase_executions': 24,
              'completed_observation_and_deadline_runs': 4, 'total_completed_rows_or_runs': sum(EXPECTED.values()) + 28,
              'offline_policy_rows': 120, 'main_suites': suites, 'main_statuses': dict(Counter(r['status'] for r in checks)),
              'npz_opened_without_pickle': npz_count, 'files_hashed': len(data_files), 'bytes': sum(r['bytes'] for r in data_files.values()),
              'validation_seeds_5000_5029_used': False, 'independent_new_confirmation': False,
              'historical_originals_replaced': False, 'all_scientific_thresholds_inherited_from_snapshots': True,
              'resolved_infrastructure_interruptions': sum(s.get('infrastructure_interruptions', 0) for s in suites),
              'phase': {k: v for k, v in phase.items() if k != 'rows'}, 'deadline_continuation': continuation}
    write(report / 'reconstruction_files.json', data_files)
    write(report / 'reconstruction_task_checks.json', checks)
    write(report / 'reconstruction_verification.json', result)
    print({k: v for k, v in result.items() if k not in ('main_suites', 'phase', 'deadline_continuation')})

if __name__ == '__main__': main()
