"""Thin launcher for an isolated original-snapshot evaluator process."""
import importlib.util
from pathlib import Path


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('runtime_frozen_replay', root / 'src/udc/analysis/frozen_replay_worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
