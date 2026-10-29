"""Execute and independently audit an already-frozen foundation study.

Both children import the study's source snapshot, allowing later active-source
development without changing an ongoing run. This module only orchestrates.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

import psutil

from .static_multigroup import verify, write_json
from .frozen_resume import atomic_json


def run(study, workers):
    study = Path(study).resolve()
    manifest = verify(study)
    if manifest['design'].get('pre_execution_input_overlap'):
        exposure = json.loads((study / manifest['design']['pre_execution_input_overlap']).read_text(encoding='utf-8'))
        from .static_multigroup import digest
        if (not exposure['all_new_physical_inputs_unique']
                or exposure['study_manifest_sha256'] != digest(study / 'manifest.json')):
            raise ValueError('New confirmation input exposure gate failed')
    if not 1 <= workers <= manifest['design']['maximum_workers']:
        raise ValueError('Worker budget exceeded')
    control = study / 'control'
    control.mkdir(exist_ok=False)
    process = psutil.Process()
    write_json(control / 'process.json', {'pid': process.pid, 'create_time': process.create_time(),
        'exe': process.exe(), 'cmdline': process.cmdline(), 'study': str(study)})
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        environment[name] = '1'
    code = ("import sys;from pathlib import Path;"
            "study=Path(sys.argv[1]);sys.path.insert(0,str(study/'source_snapshot/src'));"
            "phase=sys.argv[2];workers=int(sys.argv[3]);"
            "from udc.experiments.sequential_deployment import run;"
            "from udc.analysis.sequential_audit import audit;"
            "print(run(study,workers) if phase=='execute' else audit(study,workers,study/'readonly_audit'),flush=True)")
    def status(phase, **extra):
        atomic_json(control / 'status.json', {'updated_at': datetime.now().astimezone().isoformat(),
            'phase': phase, 'goal_complete': False, **extra})
    try:
        for phase in ('execute', 'audit'):
            status(phase)
            with (control / (phase + '.log')).open('w', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, '-c', code, str(study), phase, str(workers)],
                    stdout=log, stderr=subprocess.STDOUT, env=environment)
                child_process = psutil.Process(child.pid)
                write_json(control / (phase + '_process.json'), {'pid': child.pid,
                    'create_time': child_process.create_time(), 'exe': child_process.exe(),
                    'cmdline': child_process.cmdline()})
                child.wait()
                if child.returncode:
                    raise RuntimeError(f'{phase} child exited {child.returncode}; see saved log')
            if phase == 'execute':
                summary = json.loads((study / 'summary.json').read_text(encoding='utf-8'))
                if summary['stopped']:
                    status('scientific_stop', summary=summary)
                    return
        audit = json.loads((study / 'readonly_audit/offline_audit.json').read_text(encoding='utf-8'))
        if not audit['all_match']:
            raise RuntimeError('Independent reconstruction mismatch')
        status('completed', all_replay_match=True, evidence=manifest['evidence'])
        write_json(control / 'completion.json', {'batch_completed': True, 'all_replay_match': True,
            'goal_complete': False, 'new_independent_confirmation_samples':
                len(manifest['units']) if manifest['evidence'] == 'prospective_confirmation' else 0})
    except Exception:
        write_json(control / 'orchestration_error.json', {'traceback': traceback.format_exc()})
        status('orchestration_error')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--study', required=True)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    run(args.study, args.workers)
