"""Select frozen controller/evaluator sources before loading audit logic."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--study', required=True)
parser.add_argument('--workers', type=int, default=2)
parser.add_argument('--analysis-source')
parser.add_argument('--project-root')
args = parser.parse_args()
root = Path(args.project_root).resolve() if args.project_root else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(args.study).resolve() / 'source_snapshot' / 'src'))
spec = importlib.util.spec_from_file_location('step1_new_audit', Path(args.analysis_source) if args.analysis_source else root / 'src/udc/analysis/step1_new_studies.py')
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
if __name__ == '__main__':
    print(json.dumps(module.analyze(args.study, args.workers), ensure_ascii=False, indent=2))
