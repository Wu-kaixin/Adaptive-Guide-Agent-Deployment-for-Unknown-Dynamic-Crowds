"""Thin sequential runner; every scientific import occurs in the frozen child."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--study',required=True)
    parser.add_argument('--workers',type=int,default=32)
    args=parser.parse_args()
    study=Path(args.study).resolve()
    env=os.environ|{'PYTHONPATH':str(study/'source_snapshot/src')}
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):env[name]='1'
    for action in ('run','replay'):
        subprocess.run([sys.executable,'-u','-m','udc.experiments.recovery_study',action,'--study',str(study),'--workers',str(args.workers)],env=env,check=True)
        if action=='run' and json.loads((study/'summary.json').read_text(encoding='utf-8'))['stopped']:
            raise SystemExit('Scientific stop preserved; no retry')
