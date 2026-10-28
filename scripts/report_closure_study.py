import argparse
import importlib.util
import json
from pathlib import Path
import sys

p=argparse.ArgumentParser();p.add_argument('kind',choices=['routing','sequential']);p.add_argument('--study',required=True);p.add_argument('--output',required=True);p.add_argument('--analysis-source')
p.add_argument('--audit-output')
args=p.parse_args();study=Path(args.study).resolve();sys.path.insert(0,str(study/'source_snapshot/src'))
source=Path(args.analysis_source).resolve() if args.analysis_source else study/'source_snapshot/src/udc/analysis/closure_reports.py'
if not source.exists():source=Path(__file__).resolve().parents[1]/'src/udc/analysis/closure_reports.py'
spec=importlib.util.spec_from_file_location('closure_report',source);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
if __name__=='__main__':
    options={'audit_output':args.audit_output} if args.kind=='sequential' and args.audit_output else {}
    print(json.dumps(getattr(module,args.kind)(study,Path(args.output).resolve(),**options),ensure_ascii=False,indent=2),flush=True)
