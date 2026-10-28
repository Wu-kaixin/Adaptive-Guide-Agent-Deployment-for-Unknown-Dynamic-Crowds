"""Thin launcher; the profile module loads scientific code from the old snapshot."""
import importlib.util
from pathlib import Path


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        'runtime_performance_profile', root / 'src/udc/analysis/performance_profile.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
