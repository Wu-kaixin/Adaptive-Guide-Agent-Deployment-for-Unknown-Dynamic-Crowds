"""Thin process entry point for one unchanged, frozen experimental unit."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True)
    parser.add_argument('--unit')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--backend-policy')
    parser.add_argument('--backend-receipt')
    args = parser.parse_args()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    study = Path(args.study).resolve()
    sys.path.insert(0, str(study / 'source_snapshot/src'))
    from udc.experiments import static_multigroup as frozen
    expected = study / 'source_snapshot/src/udc/experiments/static_multigroup.py'
    if Path(frozen.__file__).resolve() != expected:
        raise RuntimeError('Worker did not import the frozen scientific implementation')
    if args.verify:
        manifest = frozen.verify(study)
        value = {'verified': True, 'units': len(manifest['units']), 'tasks': len(manifest['tasks']),
                 'manifest_sha256': frozen.digest(study / 'manifest.json'), 'source': str(expected)}
    else:
        manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
        tasks = [task for task in manifest['tasks'] if task['unit_id'] == args.unit]
        if not tasks:
            raise ValueError('Unit is outside the frozen manifest')
        before = {task['task_id'] for task in tasks
                  if (study / 'results' / task['task_id'] / 'receipt.json').is_file()}
        backend_record = None
        if args.backend_policy:
            policy_path = Path(args.backend_policy).resolve()
            policy = json.loads(policy_path.read_text(encoding='utf-8'))
            if frozen.digest(study / 'manifest.json') != policy['manifest_sha256']:
                raise ValueError('Backend policy binds another study')
            source = Path(policy['backend_source']).resolve()
            if frozen.digest(source) != policy['backend_sha256']:
                raise ValueError('Frozen acceleration backend changed')
            loader = importlib.util.spec_from_file_location('frozen_runtime_acceleration', source)
            backend = importlib.util.module_from_spec(loader)
            loader.loader.exec_module(backend)
            from udc.multigroup import execution as execution_module
            backend.install_lazy(execution_module)
            backend_record = {'unit_id': args.unit, 'pid': os.getpid(),
                              'policy_sha256': frozen.digest(policy_path),
                              'backend_source': str(source), 'backend_sha256': policy['backend_sha256'],
                              'entrypoint_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                              'original_scientific_source': str(expected)}
            if not args.backend_receipt:
                raise ValueError('An acceleration backend requires a provenance receipt')
        # Pass the entire original unit, retaining its original candidate-budget context.
        rows = frozen._unit_worker((str(study), tasks, manifest['spec'], True))
        if backend_record is not None:
            backend_record['new_task_ids'] = [row['task_id'] for row in rows if row['task_id'] not in before]
            frozen.write_json(args.backend_receipt, backend_record)
        value = {'unit_id': args.unit, 'records': len(rows), 'pid': os.getpid(),
                 'stopped_on_error_or_safety': any(row['status'] in ('EXPERIMENT_ERROR', 'SAFETY_VIOLATION')
                                                   for row in rows),
                 'source': str(expected), 'backend': backend_record}
    print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
