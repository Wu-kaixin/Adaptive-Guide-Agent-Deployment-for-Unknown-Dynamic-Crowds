"""Hash scientific code and immutable inputs; anchor Git queries to this project."""
import hashlib
import importlib.metadata
import platform
import subprocess
from pathlib import Path

import numpy as np
import yaml


def _git(*arguments):
    try:
        return subprocess.check_output(
            ['git', '-C', str(Path(__file__).parent), *arguments],
            text=True, encoding='utf-8', errors='replace',
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def provenance(config, observation, protocol, fixed_n):
    package = Path(__file__).resolve().parents[1]
    source = hashlib.sha256()
    for path in sorted(package.rglob('*.py')):
        source.update(path.relative_to(package).as_posix().encode())
        source.update(path.read_bytes())
    project = package.parent.parent
    metadata = project / 'pyproject.toml'
    if metadata.is_file():
        source.update(metadata.read_bytes())
    source_hash = source.hexdigest()
    config_hash = hashlib.sha256(yaml.safe_dump(config, sort_keys=True).encode()).hexdigest()
    raw = None if observation is None else np.ascontiguousarray(np.c_[
        observation.positions, observation.radii, observation.demand,
    ]).tobytes()
    input_hash = None if raw is None else hashlib.sha256(raw).hexdigest()
    commit = _git('rev-parse', 'HEAD')
    request_hash = hashlib.sha256(
        f'{commit}:{source_hash}:{config_hash}:{protocol}:{fixed_n}'.encode()
    ).hexdigest()
    dependencies = {}
    for name in ['numpy', 'scipy', 'shapely', 'PyYAML', 'jupedsim']:
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    return {
        'commit': commit, 'dirty': bool(_git('status', '--porcelain')),
        'patch_sha256': hashlib.sha256((_git('diff', 'HEAD', '--binary') or '').encode()).hexdigest(),
        'source_sha256': source_hash, 'config_sha256': config_hash,
        'input_snapshot_sha256': input_hash, 'task_request_sha256': request_hash,
        'task_sha256': hashlib.sha256(f'{request_hash}:{input_hash}'.encode()).hexdigest(),
        'python': platform.python_version(), 'platform': platform.platform(),
        'dependencies': dependencies,
    }
