"""Supervise one already-running frozen study and finish its local reports.

This is a one-shot process supervisor, not a scheduler or scientific retry.
It never starts or modifies the original experiment and sends no messages.
"""
import argparse
from collections import Counter
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from udc.experiments.static_multigroup import ROOT


def process_alive(pid):
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # Read-only limited information.
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), 'Cannot read experiment process status')
        return code.value == 259  # STILL_ACTIVE.
    finally:
        kernel.CloseHandle(handle)


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.025)


def run(runner_pid, runtime_amendment=None, replay_workers=1):
    study = ROOT / 'runs/static_multigroup_20261026/confirmation_v2_frozen'
    output = ROOT / 'reports/static_service_audit_20261026'
    manifest = json.loads((study / 'manifest.json').read_text(encoding='utf-8'))
    amendment = (json.loads(Path(runtime_amendment).read_text(encoding='utf-8'))
                 if runtime_amendment else None)
    worker_count = amendment['new_workers'] if amendment else manifest['spec']['runtime']['maximum_workers']
    backend = amendment.get('new_backend') if amendment else None
    started = datetime.now().astimezone().isoformat(timespec='seconds')

    def status(phase, **details):
        count = Counter(folder.name.split('_')[0] for folder in (study / 'results').iterdir()
                        if folder.is_dir() and (folder / 'receipt.json').is_file())
        value = {'phase': phase, 'supervisor_pid': os.getpid(), 'runner_pid': runner_pid,
                 'supervisor_started_at': started, 'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                 'recorded_rows': sum(count.values()), 'scheduled_rows': len(manifest['tasks']),
                 'by_K': dict(count), 'study': str(study), 'scientific_retries': 0,
                 'confirmation_complete': False, 'workers': worker_count,
                 'runtime_amendment': str(runtime_amendment) if runtime_amendment else None,
                 'operational_restart': bool(amendment), 'execution_backend': backend, **details}
        atomic_json(output / 'live_status.json', value)
        index = f'''# 本轮实验状态

更新时间：{value['updated_at']}。

阶段：**{phase}**；原确认记录 **{value['recorded_rows']:,}/{value['scheduled_rows']:,}** 行。
分群数量任务计数：`{dict(count)}`。正式确认完整状态：**{value['confirmation_complete']}**。

当前并发上限{worker_count}个计算进程、每进程数值线程1；保留原输入、主指标与样本量。
{('使用原封存计算函数及独立封存加速后端：' + backend + '；已完成结果已核验存档，新增记录另有后端来源凭据。') if backend else '使用原封存源码快照；运行配置修订由runtime_amendment记录。'}
一次性后台监督进程只等待原运行结束，随后离线复判并生成报告；不会重启或重试科学任务。

已完成的开发结果见[开发报告](DEVELOPMENT.zh.md)和[检查凭据](validation.json)。
机器可读状态见[live_status.json](live_status.json)。
'''
        if (output / 'REPORT.zh.md').exists():
            index += '\n最终确认与评估见[REPORT.zh.md](REPORT.zh.md)。\n'
        else:
            index += '\n原确认与离线复判结束后，将在本目录生成最终REPORT.zh.md；当前尚未生成。\n'
        (output / 'STATUS.zh.md').write_text(index, encoding='utf-8')

    def saved_summary():
        try:
            return json.loads((study / 'summary.json').read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    status('confirmation_running', runner_alive=process_alive(runner_pid))
    while saved_summary() is None:
        if not process_alive(runner_pid):
            status('runner_exited_without_summary', runner_alive=False,
                   action='Inspect existing artifacts before any continuation; no automatic scientific retry')
            return 2
        time.sleep(30)
        status('confirmation_running', runner_alive=True)
    environment = os.environ.copy()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        environment[name] = '1'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

    def child(command, phase, stdout_path, stderr_path, env):
        status(phase)
        with stdout_path.open('wb') as stdout, stderr_path.open('wb') as stderr:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stdout, stderr=stderr, creationflags=flags)
            while process.poll() is None:
                time.sleep(30)
                status(phase, postprocess_pid=process.pid)
        return process.returncode

    replay_path = output / 'confirmation_replay.json'
    if replay_path.exists():
        raise FileExistsError('Replay result already exists; inspect before restarting supervisor')
    replay_env = environment | {'PYTHONPATH': str(study / 'source_snapshot/src')}
    if replay_workers > 1:
        replay_command = [sys.executable, '-u', '-m', 'udc.analysis.parallel_frozen_replay',
                          '--study', str(study), '--output', str(output / 'parallel_frozen_replay'),
                          '--workers', str(replay_workers)]
        replay_env = environment | {'PYTHONPATH': str(ROOT / 'src')}
    else:
        replay_command = [sys.executable, '-u', '-m', 'udc.experiments.static_multigroup', 'replay', '--study', str(study)]
    code = child(replay_command, 'offline_frozen_replay', replay_path,
                 output / 'confirmation_replay_error.log', replay_env)
    if code != 0:
        status('offline_replay_failed', postprocess_exit_code=code)
        return code
    replay = json.loads(replay_path.read_text(encoding='utf-8'))
    if replay['mismatches']:
        status('offline_replay_mismatch', mismatches=replay['mismatches'])
        return 3
    code = child([sys.executable, '-u', '-m', 'udc.analysis.static_confirmation_report'],
                 'final_report_verification', output / 'confirmation_finalize.log',
                 output / 'confirmation_finalize_error.log', environment | {'PYTHONPATH': str(ROOT / 'src')})
    if code != 0:
        status('final_report_failed', postprocess_exit_code=code)
        return code
    receipt = json.loads((output / 'completion_receipt.json').read_text(encoding='utf-8'))
    status('complete' if receipt['confirmation_complete'] else 'protocol_stopped_and_reported',
           confirmation_complete=receipt['confirmation_complete'], replay_checked=replay['checked'], replay_mismatches=0,
           report=str(output / 'REPORT.zh.md'))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner-pid', type=int, required=True)
    parser.add_argument('--runtime-amendment')
    parser.add_argument('--replay-workers', type=int, default=1)
    args = parser.parse_args()
    try:
        exit_code = run(args.runner_pid, args.runtime_amendment, args.replay_workers)
    except Exception:
        traceback.print_exc()
        path = ROOT / 'reports/static_service_audit_20261026/live_status.json'
        previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        atomic_json(path, previous | {'phase': 'supervisor_failed', 'confirmation_complete': False,
                    'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                    'diagnostic': traceback.format_exc(), 'scientific_experiment_restarted': False})
        exit_code = 1
    raise SystemExit(exit_code)
