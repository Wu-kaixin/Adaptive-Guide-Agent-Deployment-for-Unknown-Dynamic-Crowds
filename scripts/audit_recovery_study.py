"""Read-only parallel replay using a sealed auditor and original frozen evaluator."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

p=argparse.ArgumentParser();p.add_argument('--study',required=True);p.add_argument('--analysis-source',required=True);p.add_argument('--workers',type=int,default=32)
args=p.parse_args();study=Path(args.study).resolve();sys.path.insert(0,str(study/'source_snapshot/src'))
spec=importlib.util.spec_from_file_location('frozen_recovery_audit',Path(args.analysis_source).resolve())
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
if __name__=='__main__':print(json.dumps(module.replay(study,args.workers),ensure_ascii=False,indent=2),flush=True)
