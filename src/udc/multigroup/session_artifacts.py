"""Append-only delivery journal for the static session API."""
import hashlib
import json
from pathlib import Path

import numpy as np


class JournalInUseError(RuntimeError):
    pass


def _value(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def _write(path, value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, default=_value) + '\n'
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class SessionJournal:
    def __init__(self, root, header):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=False)
        self._lock = None
        self._head = None
        self._next_index = 0
        self._pending_index = None
        self._acquire()
        try:
            self._header_sha = _write(self.root / 'session.json', header)
            _write(self.root / 'session_receipt.json', {'session.json': self._header_sha})
        except Exception:
            self.close()
            raise

    def _acquire(self):
        handle = (self.root / '.writer.lock').open('a+b')
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            import os
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise JournalInUseError('Session journal already has an active writer') from error
        self._lock = handle

    @classmethod
    def open_existing(cls, root):
        journal = cls.__new__(cls)
        journal.root = Path(root)
        journal._lock, journal._head, journal._next_index = None, None, 0
        journal._header_sha = None
        journal._pending_index = None
        if not (journal.root / 'session.json').is_file():
            raise ValueError('Session header is missing')
        journal._acquire()
        return journal

    @property
    def closed(self):
        return self._lock is None

    def close(self):
        handle, self._lock = getattr(self, '_lock', None), None
        if handle is not None:
            # Closing the OS handle releases the byte/advisory lock even
            # after a process exits without deleting the persistent file.
            handle.close()

    def __del__(self):
        self.close()

    def check_header(self):
        header_hash = hashlib.sha256((self.root / 'session.json').read_bytes()).hexdigest()
        receipt = json.loads((self.root / 'session_receipt.json').read_text(encoding='utf-8'))
        if header_hash != self._header_sha or receipt != {'session.json': self._header_sha}:
            raise ValueError('Session header changed before motion or event commit')

    def check_head(self):
        self.check_header()
        if self._next_index:
            folder = self.root / f'event_{self._next_index - 1:06d}'
            path = folder / 'receipt.json'
            if hashlib.sha256(path.read_bytes()).hexdigest() != self._head:
                raise ValueError('Committed event receipt changed before motion')
            for name, expected in json.loads(path.read_text(encoding='utf-8')).items():
                artifact = (folder / name).resolve()
                if not artifact.is_relative_to(folder.resolve()) or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
                    raise ValueError('Committed event artifact changed before motion')

    def begin(self, index, frame):
        if self.closed or index != self._next_index:
            raise RuntimeError('Journal writer is closed or event index is not its committed successor')
        if self._pending_index is not None:
            raise RuntimeError('An uncommitted session event already exists')
        self.check_head()
        folder = self.root / f'event_{index:06d}'
        folder.mkdir(exist_ok=False)
        observation = frame.measurement.observation
        _write(folder / 'frame.json', {'contract': frame.contract, 'frame_id': frame.frame_id,
            'time_s': frame.time_s, 'person_ids': frame.person_ids,
            'position_error_bound_m': frame.measurement.position_error_bound_m,
            'measurement_model': frame.measurement.model})
        np.savez_compressed(folder / 'observation.npz', positions=observation.positions,
                            radii=observation.radii, demand=observation.demand)
        _write(folder / 'intent.json', {'phase': 'before_planning_and_motion', 'event_index': index,
            'frame_id': frame.frame_id, 'session_header_sha256': self._header_sha,
            'previous_event_receipt_sha256': self._head})
        self._pending_index = index

    def append(self, index, record, checkpoint=None):
        if self.closed or index != self._next_index or self._pending_index != index:
            raise RuntimeError('Event completion requires its owned pre-motion intent')
        self.check_head()
        folder = self.root / f'event_{index:06d}'
        result = record.execution
        _write(folder / 'metrics.json', record.metrics)
        _write(folder / 'planning.json', record.planning)
        _write(folder / 'state.json', {'execution_status': result.status,
            'evidence': result.evidence, 'paths': result.paths, 'actual_positions': result.positions[-1]})
        np.savez_compressed(folder / 'trajectory.npz', positions=result.positions, velocities=result.velocities,
                            assignment=result.assignment, targets=record.targets, target_groups=record.target_groups)
        files = ['frame.json', 'observation.npz', 'intent.json', 'metrics.json', 'planning.json', 'state.json', 'trajectory.npz']
        if 'traceback' in record.metrics:
            with (folder / 'runner_error.txt').open('x', encoding='utf-8') as stream:
                stream.write(record.metrics['traceback'])
            files.append('runner_error.txt')
        if checkpoint is not None:
            _write(folder / 'checkpoint.json', {**checkpoint,
                'checkpoint_contract': 'udc.step1.completed_event.v1',
                'next_event_index': index + 1,
                'session_header_sha256': self._header_sha,
                'previous_event_receipt_sha256': self._head})
            files.append('checkpoint.json')
        self._head = _write(folder / 'receipt.json', {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in files})
        self._next_index = index + 1
        self._pending_index = None
        return folder
