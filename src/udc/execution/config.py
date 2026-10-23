"""Load the configs that preflight and execution actually use.

The working-tree hash is of the files on disk. It is not the parent commit.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WIRING_YAML = ROOT / 'configs' / 'step2a' / 'static_two_groups.yaml'
POLICY_YAML = ROOT / 'configs' / 'step2a' / 'fixed_paths_no_recovery_v1.yaml'

WIRING_KEYS = (
    'boundary_policy', 'estimator', 'deployment_method', 'phase', 'phase_policy',
    'offset_m', 'sample_spacing_m', 'wall_margin_m', 'crowd_margin_m', 'crowd_distance_m',
    'max_gap_limit_m', 'arc_tolerance_m', 'planning_gap_m', 'demand_bandwidth_m',
    'guide_radius_m', 'clearance_m', 'guide_separation_m', 'numeric_tolerance_m',
    'quadrature_samples', 'planner_max_iterations', 'simplify_m', 'planning_margin_m',
    'path_start_atol_m', 'path_end_atol_m',
)
POLICY_KEYS = (
    'policy', 'recovery_policy', 'deadline_s', 'dt_s', 'max_steps', 'gain',
    'max_speed_m_s', 'position_tolerance_m', 'speed_tolerance_m_s', 'curve_tolerance_m',
    'waypoint_tolerance_m', 'hold_steps', 'stall_window', 'max_gap_limit_m',
    'displacement_atol_m', 'equivalent_to_step1_recovery',
)
SOURCE_ROOTS = (
    'src/udc/execution', 'src/udc/preflight', 'src/udc/allocation', 'src/udc/navigation',
    'src/udc/safety', 'src/udc/controller', 'src/udc/boundary', 'src/udc/deployment',
)


class ConfigMismatch(ValueError):
    """The file that claims to be the policy is not the config the code would use."""


def load_wiring(path=WIRING_YAML):
    document = _load(path)
    wiring = document.get('effective_wiring')
    if not isinstance(wiring, dict):
        raise ConfigMismatch('effective_wiring is missing from the Step 2a config')
    return _select(wiring, WIRING_KEYS, 'effective_wiring')


def load_execution_policy(path=POLICY_YAML):
    return _select(_load(path), POLICY_KEYS, 'execution policy')


def assert_same_config(actual, declared, label):
    if actual != declared:
        raise ConfigMismatch(f'{label} does not match the saved YAML')


def working_tree_provenance():
    """Hash the modules this stack imports. ``git_head`` alone is not this hash."""
    digest = hashlib.sha256()
    for relative in SOURCE_ROOTS:
        directory = ROOT / relative
        for path in sorted(directory.rglob('*.py')):
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    for relative in ('pyproject.toml', 'configs/step2a/static_two_groups.yaml',
                     'configs/step2a/fixed_paths_no_recovery_v1.yaml'):
        path = ROOT / relative
        digest.update(relative.encode())
        digest.update(path.read_bytes())
    head = _git('rev-parse', 'HEAD')
    dirty = bool(_git('status', '--porcelain'))
    return {
        'git_head': head,
        'git_dirty': dirty,
        'working_tree_source_sha256': digest.hexdigest(),
        'source_identity': 'working tree bytes, not the parent commit tree',
    }


def _load(path):
    path = Path(path)
    if not path.is_file():
        raise ConfigMismatch(f'missing config {path}')
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ConfigMismatch(f'{path} is not a mapping')
    return data


def _select(data, keys, label):
    missing = [key for key in keys if key not in data]
    if missing:
        raise ConfigMismatch(f'{label} is missing {missing}')
    selected = {key: data[key] for key in keys}
    if 'planning_gap_m' in selected and 'max_gap_limit_m' in selected and 'arc_tolerance_m' in selected:
        expected = selected['max_gap_limit_m'] - 2 * selected['arc_tolerance_m']
        if abs(expected - selected['planning_gap_m']) > 1e-12:
            raise ConfigMismatch('planning_gap_m is not max_gap_limit_m - 2*arc_tolerance_m')
    if selected.get('equivalent_to_step1_recovery') is True:
        raise ConfigMismatch('this policy is not the Step 1 recovery controller')
    return selected


def _git(*arguments):
    try:
        return subprocess.check_output(
            ['git', '-C', str(ROOT), *arguments], text=True, encoding='utf-8', errors='replace',
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
