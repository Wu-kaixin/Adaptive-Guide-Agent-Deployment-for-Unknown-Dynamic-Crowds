import importlib.util
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'runs/static_multigroup_20261026/confirmation_v2_frozen/source_snapshot/src'))
spec = importlib.util.spec_from_file_location('safety_stop_audit', root / 'src/udc/analysis/safety_stop.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if __name__ == '__main__':
    print(json.dumps(module.diagnose(root / 'reports/step1_safety_stop_20261028',
        root / 'runs/step1_pause_optimize_20261028/engine_snapshot/accelerated_execution.py'), indent=2))
