"""One-shot, gated continuation of the three explicitly authorized Step 1 tasks."""
from datetime import datetime
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from udc.experiments.static_multigroup import ROOT,digest
from udc.experiments.frozen_resume import atomic_json


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def run(control):
    control=Path(control).resolve();plan=read(control/'workflow.json')
    with (control/'runner.lock').open('x',encoding='utf-8') as f:json.dump({'pid':os.getpid(),'started':datetime.now().astimezone().isoformat()},f)
    for relative,sha in plan['sealed_files'].items():
        if digest(control/relative)!=sha:raise ValueError('Sealed workflow source changed: '+relative)
    def status(phase,**details):atomic_json(control/'status.json',{'phase':phase,'pid':os.getpid(),'updated_at':datetime.now().astimezone().isoformat(),'scientific_retries':0,**details})
    env=os.environ.copy()
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):env[name]='1'
    def child(command,name,environment):
        with (control/(name+'_stdout.log')).open('xb') as out,(control/(name+'_stderr.log')).open('xb') as err:
            flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
            process=subprocess.Popen(command,cwd=ROOT,env=environment,stdout=out,stderr=err,creationflags=flags)
            while process.poll() is None:
                status(name,child_pid=process.pid);time.sleep(15)
        if process.returncode:raise RuntimeError(f'{name} failed with exit code {process.returncode}; no scientific retry')
    for gate in plan['existing_gates']:
        path=ROOT/gate['path']
        while not path.is_file():
            if gate.get('stderr') and (ROOT/gate['stderr']).is_file() and (ROOT/gate['stderr']).stat().st_size:
                raise RuntimeError('Existing gate failed: '+gate['path'])
            status('waiting_existing_read_only_gate',gate=gate['path']);time.sleep(30)
        data=read(path)
        if not data['all_match']:raise ValueError('Existing offline gate mismatch: '+gate['path'])
    support_study=ROOT/plan['support_source_study']
    support_output=ROOT/plan['support_audit_output']
    if not (support_output/'summary.json').is_file():
        child([sys.executable,'-u','-m','udc.analysis.reference_support_audit','--study',str(ROOT/'runs/static_phase_retention_20261027/main_v1'),
               '--output',str(support_output)],'general_support_fixed_actions',env|{'PYTHONPATH':str(support_study/'source_snapshot/src')})
    support=read(support_output/'summary.json')
    if support['violations'] or support['fixed_pairs']!=640 or any(r['certificate']['accept_retained'] for r in support['seed2210_counterexample_regressions']):
        raise ValueError('Original-reference general support gate failed')
    reports=[]
    def report(study,kind,name,audit_output=None):
        output=ROOT/'reports/step1_closure_20261028'/name
        if not (output/'results.json').is_file():
            arguments=[sys.executable,'-u',str(control/'engine_snapshot/report_closure_study.py'),kind,'--study',str(study),'--output',str(output),
                   '--analysis-source',str(control/'engine_snapshot/closure_reports.py')]
            if audit_output is not None:arguments.extend(['--audit-output',str(audit_output)])
            child(arguments,name+'_report',env)
        data=read(output/'results.json')
        if data['manifest_sha256']!=digest(study/'manifest.json'):raise ValueError('Report manifest mismatch')
        reports.append({'name':name,'report':str(output/'REPORT.zh.md'),'results':data})
    for source in plan['existing_reports']:
        report(ROOT/source['study'],'routing',source['name'])
    for stage in plan['stages']:
        study=ROOT/stage['study']
        if digest(study/'manifest.json')!=stage['manifest_sha256']:raise ValueError('Frozen study manifest changed')
        environment=env|{'PYTHONPATH':str(study/'source_snapshot/src')}
        if not (study/'summary.json').is_file():
            child([sys.executable,'-u','-m',stage['module'],'run','--study',str(study),'--workers',str(stage['workers'])],stage['name']+'_run',environment)
        summary=read(study/'summary.json')
        if summary['stopped']:raise ValueError('New scientific/program stop: '+stage['name'])
        audit_output=None
        if stage['kind']=='routing':
            if summary['recorded']!=summary['scheduled']:raise ValueError('Incomplete routing ledger')
            if not (study/'offline_replay.json').is_file():
                child([sys.executable,'-u','-m',stage['module'],'replay','--study',str(study),'--workers',str(stage['workers'])],stage['name']+'_audit',environment)
            audit=read(study/'offline_replay.json')
        else:
            if summary['recorded_step_records']!=summary['scheduled_step_records']:raise ValueError('Incomplete sequence ledger')
            audit_output=study/plan['sequential_audit_directory'] if plan.get('sequential_audit_directory') else study
            if not (audit_output/'offline_audit.json').is_file():
                arguments=[sys.executable,'-u',str(control/'engine_snapshot/audit_sequential_study.py'),'--study',str(study),'--workers',str(stage['workers'])]
                if plan.get('sequential_analysis_source'):
                    arguments.extend(['--analysis-source',str(control/plan['sequential_analysis_source']),'--audit-output',str(audit_output)])
                child(arguments,stage['name']+'_audit',environment)
            audit=read(audit_output/'offline_audit.json')
        if not audit['all_match']:raise ValueError('Offline reconstruction mismatch')
        report(study,stage['kind'],stage['name'],audit_output)
        if stage.get('activation_gate'):
            if audit['by_method']['counts_phase_guard']['nontrivial_hold_frames']==0 or audit['by_method']['recompute_all']['count_switch_events']==0:
                raise RuntimeError('RESEARCH_ACTIVATION_GATE: develop a declared threshold case before independent sequence confirmation; do not rerun or select seeds')
    result={'all_planned_batches_completed':True,'reports':reports,'general_support_fixed_actions':support,
        'original_17280_confirmation_remains_stopped':True,'step2_step3_started':False,
        'scope':'Original reference with declared observable sufficient conditions; verified serial recovery; 50-index bounded-reobservation experiment',
        'unrestricted_global_completeness_or_asymptotic_stability_claimed':False}
    atomic_json(control/'completion.json',result)
    target=ROOT/'reports/step1_closure_20261028/REPORT.zh.md'
    lines=['# Step 1 三项未完成工作的本轮结果','',f'完成时间：{datetime.now().astimezone().isoformat()}。', '',
        '原停止确认保持封存；新实验、旧动作回归和独立确认分开。原参考服务界具有明确充分条件，50步实验不等于任意时间稳定性。','']
    for item in reports:lines.extend([f"- [{item['name']}]({Path(item['report']).as_posix()})"])
    lines.extend(['','一般有界误差原参考推导见docs/STEP1_GENERAL_REFERENCE_BOUND.zh.md；所有回退、科学失败、程序停止及缺失保留。'])
    target.write_text('\n'.join(lines),encoding='utf-8');status('planned_batches_complete',report=str(target))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--control',required=True);args=p.parse_args()
    try:run(args.control)
    except Exception:
        atomic_json(Path(args.control)/'error.json',{'traceback':traceback.format_exc(),'scientific_retry':False,'updated_at':datetime.now().astimezone().isoformat()})
        traceback.print_exc();raise SystemExit(1)
