"""One-shot sequential workflow for explicitly frozen Step 1 studies.

This process neither retries scientific tasks nor resumes the stopped original.
The application heartbeat supplies user notification, not this process.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from udc.experiments.frozen_resume import atomic_json
from udc.experiments.static_multigroup import ROOT, digest


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def run(control):
    control = Path(control).resolve()
    plan = read(control / 'workflow.json')
    for relative, sha in plan['sealed_files'].items():
        if digest(control / relative) != sha:
            raise ValueError('WORKFLOW_SOURCE_CHANGED: ' + relative)
    status_file = control / 'status.json'
    def status(phase, **details):
        atomic_json(status_file, {'phase': phase, 'pid': os.getpid(), 'updated_at': datetime.now().astimezone().isoformat(),
                    'scientific_retries': 0, 'original_confirmation_will_not_be_resumed': True, **details})
    def child(command, name, environment):
        status(name, command=command)
        with (control / (name + '_stdout.log')).open('xb') as out, (control / (name + '_stderr.log')).open('xb') as err:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=out, stderr=err, creationflags=flags)
            while process.poll() is None:
                status(name, child_pid=process.pid)
                time.sleep(15)
        if process.returncode:
            raise RuntimeError(f'{name} failed with exit code {process.returncode}; no automatic retry')
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        environment[name] = '1'
    # The original has hit a predeclared safety stop. Let only already-running
    # units drain and the original evaluator/report finish before taking CPUs.
    original_receipt = ROOT / 'reports/static_service_audit_20261026/completion_receipt.json'
    while not original_receipt.is_file():
        live = read(ROOT / 'reports/static_service_audit_20261026/live_status.json')
        if live['phase'] in ('supervisor_failed', 'runner_exited_without_summary', 'offline_replay_failed', 'offline_replay_mismatch', 'final_report_failed'):
            raise RuntimeError('Original reporting needs attention: ' + live['phase'])
        status('waiting_original_stopped_batch_report', original_phase=live['phase'])
        time.sleep(30)
    original = read(original_receipt)
    if original['offline_replay']['mismatches']:
        raise ValueError('Original evaluator replay mismatch')
    audits = []
    for stage in plan['stages']:
        study = ROOT / stage['study']
        if digest(study / 'manifest.json') != stage['manifest_sha256']:
            raise ValueError('New study manifest changed')
        env = environment | {'PYTHONPATH': str(study / 'source_snapshot/src')}
        child([sys.executable, '-u', '-m', stage['module'], 'run', '--study', str(study), '--workers', str(stage['workers'])], stage['name'] + '_run', env)
        summary = read(study / 'summary.json')
        if summary['stopped'] or summary['recorded_rows'] != summary['scheduled_rows']:
            raise ValueError('Scientific stop or incomplete new study: ' + stage['name'])
        child([sys.executable, '-u', str(control / 'engine_snapshot/audit_step1_new_study.py'),
               '--study', str(study), '--workers', str(stage['audit_workers']), '--project-root', str(ROOT),
               '--analysis-source', str(control / 'engine_snapshot/step1_new_studies.py')],
              stage['name'] + '_audit', env)
        audit = read(study / 'offline_audit.json')
        if not audit['all_match']:
            raise ValueError('New study audit mismatch: ' + stage['name'])
        audits.append({'name': stage['name'], 'study': str(study), 'audit': audit})
    report = ROOT / 'reports/step1_followthrough_20261028/REPORT.zh.md'
    result = {'scoped_batches_complete': True, 'original_confirmation_complete': original['confirmation_complete'],
              'original_recorded_rows': original['recorded_rows'], 'original_scheduled_rows': original['scheduled_rows'],
              'new_studies': audits, 'full_guide_complete': False,
              'remaining_research_gates': ['general independent observation error on original reference',
                  'safe alternative routing after rejected path', '50-step integer allocation and phase switching'],
              'step2_step3_started': False}
    atomic_json(control / 'completion.json', result)
    text = ['# Step 1 本轮实验结果', '',
            '本轮预定新版本实验与离线复核完成。原确认因安全规则停止，原失败和未执行分母保留；不能标记为完整原确认。', '',
            '服务界限定新锚定量化参考与纯共同平移误差。一般独立误差、替代路径和50次整数切换仍是未闭合研究问题。', '']
    for item in audits:
        text.extend([f"## {item['name']}", '', f"[完整报告]({Path(item['study']).as_posix()}/REPORT.zh.md)", '', '```json', json.dumps(item['audit'], ensure_ascii=False, indent=2), '```', ''])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('\n'.join(text), encoding='utf-8')
    status('scoped_batches_complete', full_guide_complete=False, report=str(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--control', required=True)
    args = parser.parse_args()
    try:
        run(args.control)
    except Exception:
        diagnostic = traceback.format_exc()
        atomic_json(Path(args.control) / 'error.json', {'diagnostic': diagnostic,
                    'scientific_retry_performed': False, 'updated_at': datetime.now().astimezone().isoformat()})
        traceback.print_exc()
        raise SystemExit(1)
