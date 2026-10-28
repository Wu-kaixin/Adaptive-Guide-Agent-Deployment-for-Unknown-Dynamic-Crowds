"""Thin frozen run then read-only 50-step audit; never retries science."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--study',required=True);p.add_argument('--workers',type=int,default=32)
    args=p.parse_args();study=Path(args.study).resolve();env=os.environ|{'PYTHONPATH':str(study/'source_snapshot/src')}
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):env[name]='1'
    subprocess.run([sys.executable,'-u','-m','udc.experiments.sequential_deployment','run','--study',str(study),'--workers',str(args.workers)],env=env,check=True)
    if json.loads((study/'summary.json').read_text(encoding='utf-8'))['stopped']:raise SystemExit('Scientific/program stop preserved; no retry')
    subprocess.run([sys.executable,'-u',str(Path(__file__).with_name('audit_sequential_study.py')),'--study',str(study),'--workers',str(args.workers)],env=env,check=True)
