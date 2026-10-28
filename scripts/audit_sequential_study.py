"""Select frozen decision/evaluator sources before read-only episode replay."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

p=argparse.ArgumentParser();p.add_argument('--study',required=True);p.add_argument('--workers',type=int,default=32)
p.add_argument('--analysis-source');p.add_argument('--audit-output')
args=p.parse_args();study=Path(args.study).resolve();sys.path.insert(0,str(study/'source_snapshot/src'))
source=Path(args.analysis_source).resolve() if args.analysis_source else study/'source_snapshot/src/udc/analysis/sequential_audit.py'
spec=importlib.util.spec_from_file_location('frozen_sequential_audit',source);module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module;spec.loader.exec_module(module)
if __name__=='__main__':print(json.dumps(module.audit(study,args.workers,output=args.audit_output),ensure_ascii=False,indent=2),flush=True)
