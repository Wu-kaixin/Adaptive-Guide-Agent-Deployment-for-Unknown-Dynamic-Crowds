"""Verify a completed-event state before granting a new journal writer.

Never infers missing motion from a checkpoint or resumes a half-written event.
The caller must provide externally known current robot poses and time.
"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform

import numpy as np

from .foundation_contract import STATIC_CONTRACT
from .session_artifacts import SessionJournal


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def implementation_binding():
    package = Path(__file__).resolve().parents[1]
    project = package.parents[1]
    sources = {p.relative_to(project).as_posix(): digest(p) for p in sorted(package.rglob('*.py'))}
    for name in ('pyproject.toml', 'requirements/locks/windows-py312.txt'):
        path = project / name
        if path.exists():
            sources[name] = digest(path)
    return {'sources': sources, 'python': platform.python_version(),
        'implementation': platform.python_implementation(),
        'dependencies': {name: importlib.metadata.version(name)
            for name in ('numpy', 'scipy', 'shapely', 'PyYAML', 'threadpoolctl')}}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def recover(root, actual_positions, observed_time_s):
    journal = SessionJournal.open_existing(root)
    try:
        root = journal.root.resolve()
        header = read(root / 'session.json')
        if header.get('journal_schema_version') != 3 or header.get('contract') != STATIC_CONTRACT:
            raise ValueError('No completed-event recovery contract in this journal; preserve the original')
        receipt = read(root / 'session_receipt.json')
        if receipt != {'session.json': digest(root / 'session.json')}:
            raise ValueError('Session header changed')
        if header.get('implementation_binding') != implementation_binding():
            raise ValueError('Session source or environment changed; use an explicitly versioned new protocol')
        header_sha = receipt['session.json']
        journal._header_sha = header_sha
        previous_receipt, state = None, None
        previous_positions = np.asarray(header['initial_guides'], float)
        previous_time = 0.
        folders = sorted(root.glob('event_*'))
        required = {'frame.json', 'observation.npz', 'intent.json', 'metrics.json', 'planning.json',
                    'state.json', 'trajectory.npz', 'checkpoint.json'}
        for index, folder in enumerate(folders):
            if folder.is_symlink() or folder.name != f'event_{index:06d}':
                raise ValueError('Session event path or sequence is invalid')
            if not (folder / 'receipt.json').is_file():
                raise ValueError('Incomplete event: physical motion is not recoverable from an uncommitted checkpoint')
            receipt = read(folder / 'receipt.json')
            if not required <= set(receipt):
                raise ValueError('Committed event omits a required recovery artifact')
            for name, expected in receipt.items():
                path = (folder / name).resolve()
                if not path.is_relative_to(folder.resolve()) or not path.is_file() or digest(path) != expected:
                    raise ValueError('Session artifact changed: ' + name)
            checkpoint, metrics = read(folder / 'checkpoint.json'), read(folder / 'metrics.json')
            intent = read(folder / 'intent.json')
            if (intent['phase'] != 'before_planning_and_motion' or intent['event_index'] != index
                    or intent['session_header_sha256'] != header_sha
                    or intent['previous_event_receipt_sha256'] != previous_receipt
                    or intent['frame_id'] != read(folder / 'frame.json')['frame_id']):
                raise ValueError('Session pre-motion intent chain changed')
            if (checkpoint['checkpoint_contract'] != 'udc.step1.completed_event.v1'
                    or checkpoint['session_header_sha256'] != header_sha
                    or checkpoint['previous_event_receipt_sha256'] != previous_receipt
                    or checkpoint['next_event_index'] != index + 1):
                raise ValueError('Session recovery chain changed')
            with np.load(folder / 'trajectory.npz', allow_pickle=False) as data:
                if (not np.array_equal(data['positions'][0], previous_positions)
                        or not np.array_equal(data['positions'][-1], np.asarray(checkpoint['positions']))):
                    raise ValueError('Checkpoint would reset the saved physical state')
            if metrics['end_time_s'] != checkpoint['time_s'] or checkpoint['time_s'] < previous_time:
                raise ValueError('Checkpoint physical clock changed')
            state = checkpoint
            previous_positions, previous_time = np.asarray(state['positions'], float), state['time_s']
            previous_receipt = digest(folder / 'receipt.json')
        actual = np.asarray(actual_positions, float)
        if (actual.shape != previous_positions.shape or not np.isfinite(actual).all()
                or not np.array_equal(actual, previous_positions)):
            raise ValueError('Current robot feedback differs from the committed physical state; no reset permitted')
        if not np.isfinite(observed_time_s) or observed_time_s != previous_time:
            raise ValueError('Feedback must use the committed simulation clock; unrecorded elapsed motion needs a new protocol')
        journal._head, journal._next_index = previous_receipt, len(folders)
        return header, state, journal
    except Exception:
        journal.close()
        raise
