from datetime import datetime, timezone
import json

import pytest

from udc.experiments import cpu_affinity


class Process:
    def __init__(self, pid, parent, cpus=None, created=100, name='python.exe', children=()):
        self.pid, self.parent, self.cpus = pid, parent, cpus
        self.created, self.label, self.descendants = created, name, list(children)
        self.pins, self.priorities = [], []

    def ppid(self):
        return self.parent

    def create_time(self):
        return self.created

    def cpu_affinity(self, cpus=None):
        if cpus is None:
            return self.cpus
        self.pins.append(cpus)

    def nice(self, priority):
        self.priorities.append(priority)

    def children(self, recursive=True):
        return self.descendants

    def name(self):
        return self.label


def configure(monkeypatch, tmp_path, *, foreign=False):
    console = Process(90, 11, name='conhost.exe')
    compute = Process(21, 11)
    wrapper = Process(11, 999 if foreign else 7, children=[console, compute])
    objects = {7: Process(7, 1, cpus=[0, 1]), 11: wrapper}
    monkeypatch.setattr(cpu_affinity.psutil, 'Process', objects.__getitem__)
    alive = iter([True, False])
    monkeypatch.setattr(cpu_affinity.psutil, 'pid_exists', lambda _: next(alive))
    monkeypatch.setattr(cpu_affinity.time, 'sleep', lambda _: None)
    (tmp_path / 'units').mkdir()
    (tmp_path / 'units/u.process.json').write_text(json.dumps({
        'pid': 11, 'unit_id': 'u', 'started_at': datetime.fromtimestamp(100, timezone.utc).isoformat()}))
    return wrapper, compute, console


def test_affinity_pins_python_and_launcher_but_not_console(monkeypatch, tmp_path):
    wrapper, compute, console = configure(monkeypatch, tmp_path)
    cpu_affinity.manage(tmp_path, 7, 2)
    assert wrapper.pins == [[0]] and compute.pins == [[0]]
    assert console.pins == []
    status = json.loads((tmp_path / 'cpu_affinity_status.json').read_text())
    assert status['active_assignments']['11']['compute_pids'] == [21]


def test_affinity_never_changes_unrelated_reused_pid(monkeypatch, tmp_path):
    wrapper, compute, _ = configure(monkeypatch, tmp_path, foreign=True)
    cpu_affinity.manage(tmp_path, 7, 2)
    assert wrapper.pins == [] and compute.pins == []


def test_affinity_without_windows_priority_classes(monkeypatch, tmp_path):
    monkeypatch.delattr(cpu_affinity.psutil, 'NORMAL_PRIORITY_CLASS', raising=False)
    wrapper, compute, _ = configure(monkeypatch, tmp_path)
    cpu_affinity.manage(tmp_path, 7, 2)
    assert wrapper.pins == [[0]] and compute.pins == [[0]]
    assert wrapper.priorities == [] and compute.priorities == []


def test_all_core_policy_requires_matching_allowed_cpu_count(monkeypatch, tmp_path):
    configure(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match='allowed logical CPUs'):
        cpu_affinity.manage(tmp_path, 7, 32)
