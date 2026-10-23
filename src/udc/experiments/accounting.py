"""One saved task directory contributes one ledger row.

Physical acceptance is the success flag and criteria stored in ``metrics.json``.
Process status is separate. ``runner_error.txt`` is a program-error scene.
If metrics already declare ``EXPERIMENT_ERROR``, that declaration absorbs the
scene: the file is evidence, not a second task, and it is not a reason to
restore a success flag. If metrics declare any other outcome, or are missing
or unreadable, the scene is unabsorbed and the ledger status is
``EXPERIMENT_ERROR``. The saved metrics are left unchanged.

``orchestration_error.json`` is not this marker. Later protocols keep it
beside the runner metrics and account it in their own ledger.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from pathlib import Path


PROGRAM_ERROR_STATUS = 'EXPERIMENT_ERROR'
ERROR_SCENE = 'runner_error.txt'
PROCESS_NOTE = 'process_error.json'
ORCHESTRATION_NOTE = 'orchestration_error.json'
IDENTITY_FIELDS = ('scene', 'method', 'protocol')


def _atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            'w', encoding='utf-8', newline='\n', dir=path.parent, delete=False) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def read_metrics(directory):
    """Return ``(metrics_or_None, readable)``. Missing is ``(None, False)``."""
    path = Path(directory) / 'metrics.json'
    if not path.is_file():
        return None, False
    metrics = _read_json(path)
    if not isinstance(metrics, dict):
        return None, False
    return metrics, True


def error_scene_present(directory):
    return (Path(directory) / ERROR_SCENE).is_file()


def metrics_declare_program_error(metrics):
    return isinstance(metrics, dict) and metrics.get('termination_status') == PROGRAM_ERROR_STATUS


def unabsorbed_program_error(directory):
    """Program-error scene that metrics have not already declared.

    A missing or unreadable metrics file does not absorb the scene.
    """
    directory = Path(directory)
    if not error_scene_present(directory):
        return False
    metrics, readable = read_metrics(directory)
    if not readable:
        return True
    return not metrics_declare_program_error(metrics)


def _identity_from(payload):
    if not isinstance(payload, dict):
        return None
    if not all(name in payload for name in IDENTITY_FIELDS):
        return None
    return tuple(payload[name] for name in IDENTITY_FIELDS)


def _directory_identity(directory, metrics, identity):
    found = _identity_from(metrics)
    if found is not None:
        return found
    directory = Path(directory)
    for name in ('state.json', PROCESS_NOTE):
        found = _identity_from(_read_json(directory / name))
        if found is not None:
            return found
    if isinstance(identity, dict):
        mapped = {
            'scene': identity.get('scene', identity.get('scenario')),
            'method': identity.get('method'),
            'protocol': identity.get('protocol'),
        }
        found = _identity_from(mapped)
        if found is not None:
            return found
    return None


def _finite_time(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and value == value and value not in (float('inf'), float('-inf')):
        return float(value)
    return None


def ledger_row(directory, identity=None):
    """Account one directory once.

    ``success`` and ``termination_status`` are the ledger. Physical fields
    repeat what a readable metrics file says, including when the ledger
    withholds that success.
    """
    directory = Path(directory)
    metrics, readable = read_metrics(directory)
    scene = error_scene_present(directory)
    unabsorbed = bool(scene and (not readable or not metrics_declare_program_error(metrics)))
    absorbed = bool(scene and readable and metrics_declare_program_error(metrics))
    names = _directory_identity(directory, metrics if readable else None, identity)
    if unabsorbed or not readable:
        status = PROGRAM_ERROR_STATUS
        success = False
    else:
        status = metrics.get('termination_status')
        success = bool(metrics.get('success'))
    physical_success = bool(metrics.get('success')) if readable else None
    physical_status = metrics.get('termination_status') if readable else None
    physical_time = _finite_time(metrics.get('simulation_time')) if readable else None
    return {
        'group_key': '|'.join(str(part) for part in names) if names is not None else PROGRAM_ERROR_STATUS,
        'success': success,
        'termination_status': status,
        'simulation_time': physical_time if success else None,
        'physical_success': physical_success,
        'physical_termination_status': physical_status,
        'physical_simulation_time': physical_time,
        'process_error': bool(unabsorbed or (readable and metrics_declare_program_error(metrics))),
        'unabsorbed_program_error': unabsorbed,
        'absorbed_program_error': absorbed,
        'has_metrics': readable,
        'has_error_scene': scene,
    }


def record_process_error(directory, error_text, identity=None):
    """Save a program-error scene without replacing metrics or an earlier scene.

    A finished ``state.json`` is also left in place. Identity for a failure
    that has no metrics yet is written to ``process_error.json`` and, only
    when no state file exists, to a failed ``state.json``.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    text = '' if error_text is None else str(error_text)
    scene = directory / ERROR_SCENE
    if not scene.exists():
        _atomic_text(scene, text if text.endswith('\n') or text == '' else text + '\n')
    else:
        later = directory / 'later_process_error.txt'
        if not later.exists():
            _atomic_text(later, text if text.endswith('\n') or text == '' else text + '\n')
    if (directory / PROCESS_NOTE).exists():
        return
    payload = identity or {}
    note = {
        'status': PROGRAM_ERROR_STATUS,
        'preserved_metrics': (directory / 'metrics.json').is_file(),
        'preserved_error_scene': True,
        'scene': payload.get('scene', payload.get('scenario')),
        'method': payload.get('method'),
        'protocol': payload.get('protocol'),
        'error': text.strip().splitlines()[-1] if text.strip() else '',
    }
    _atomic_text(directory / PROCESS_NOTE, json.dumps(note, indent=2) + '\n')
    if (directory / 'metrics.json').is_file() or (directory / 'state.json').exists():
        return
    _atomic_text(directory / 'state.json', json.dumps({
        'status': 'failed',
        'error': note['error'],
        'scene': note['scene'],
        'method': note['method'],
        'protocol': note['protocol'],
    }, indent=2) + '\n')


def _relative(root, directory):
    try:
        return directory.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return directory.as_posix()


def audit_accounting_tree(root, example_limit=20):
    """Read-only classification. This function does not write or delete."""
    root = Path(root)
    counts = Counter()
    examples = {}

    def note(kind, directory):
        counts[kind] += 1
        bucket = examples.setdefault(kind, [])
        if len(bucket) < example_limit:
            bucket.append(_relative(root, directory))

    if not root.exists():
        return {'root': str(root), 'exists': False, 'task_directories': 0,
                'counts': {}, 'examples': {}}
    seen = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name != '__pycache__']
        names = set(filenames)
        if 'metrics.json' not in names and ERROR_SCENE not in names:
            continue
        directory = Path(dirpath)
        resolved = directory.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        metrics, readable = read_metrics(directory)
        scene = ERROR_SCENE in names
        status = metrics.get('termination_status') if readable else None
        success = bool(metrics.get('success')) if readable else False
        if scene and readable and status != PROGRAM_ERROR_STATUS:
            note('unabsorbed_conflict', directory)
        elif scene and not readable:
            note('error_before_metrics', directory)
        elif scene and readable and status == PROGRAM_ERROR_STATUS:
            note('absorbed_program_error', directory)
        elif readable and status == PROGRAM_ERROR_STATUS:
            note('declared_program_error_without_scene', directory)
        elif readable and success:
            note('clean_success', directory)
        elif readable:
            note('scientific_or_other_saved_outcome', directory)
        if readable and status == PROGRAM_ERROR_STATUS and success:
            note('experiment_error_status_with_success_flag', directory)
        if ORCHESTRATION_NOTE in names and not scene:
            note('orchestration_sidecar_without_runner_error', directory)
        row = ledger_row(directory)
        if row['physical_success'] and not row['success']:
            note('physical_success_excluded_from_ledger', directory)
    return {
        'root': str(root),
        'exists': True,
        'task_directories': len(seen),
        'counts': dict(counts),
        'examples': examples,
        'wrote_files': False,
    }
