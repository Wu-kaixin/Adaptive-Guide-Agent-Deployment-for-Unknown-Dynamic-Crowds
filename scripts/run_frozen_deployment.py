"""Frozen deployment run/replay with an explicit process CPU partition."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--study',required=True);p.add_argument('--workers',type=int,default=32);p.add_argument('--cpu-start',type=int,default=0)
    args=p.parse_args();study=Path(args.study).resolve();allowed=psutil.Process().cpu_affinity();chosen=allowed[args.cpu_start:args.cpu_start+args.workers]
    if len(chosen)!=args.workers:raise ValueError('Requested CPU partition is unavailable')
    psutil.Process().cpu_affinity(chosen)
    env=os.environ|{'PYTHONPATH':str(study/'source_snapshot/src')}
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):env[name]='1'
    for action in ('run','replay'):
        subprocess.run([sys.executable,'-u','-m','udc.experiments.deployment_recovery',action,'--study',str(study),'--workers',str(args.workers)],env=env,check=True)
        if action=='run' and json.loads((study/'summary.json').read_text(encoding='utf-8'))['stopped']:raise SystemExit('Scientific stop preserved; no retry')
