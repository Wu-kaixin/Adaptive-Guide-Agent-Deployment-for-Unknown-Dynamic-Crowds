"""Thin isolated launcher for read-only geometry failure classification."""
import importlib.util
from pathlib import Path


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('runtime_failure_census', root / 'src/udc/analysis/path_failure_census.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
