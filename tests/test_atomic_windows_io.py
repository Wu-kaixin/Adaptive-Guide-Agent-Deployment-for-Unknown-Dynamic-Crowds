import numpy as np
import pytest

from udc.experiments import runner


def windows_permission_error(code=32):
    error=PermissionError('simulated Windows file lock')
    error.winerror=code
    return error


def test_transient_windows_replace_keeps_atomic_text(tmp_path,monkeypatch):
    target=tmp_path/'state.json';target.write_text('old')
    original=runner.os.replace;attempts=[];delays=[]
    def replace(source,destination):
        attempts.append(1)
        assert target.read_text()=='old'
        if len(attempts)<3:raise windows_permission_error()
        original(source,destination)
    monkeypatch.setattr(runner.os,'replace',replace)
    monkeypatch.setattr(runner,'sleep',delays.append)
    runner._atomic_text(target,'new')
    assert target.read_text()=='new'
    assert len(attempts)==3 and delays==[0.05,0.2]


def test_npz_uses_same_bounded_publication(tmp_path,monkeypatch):
    original=runner.os.replace;attempts=[]
    def replace(source,destination):
        attempts.append(1)
        if len(attempts)==1:raise windows_permission_error(5)
        original(source,destination)
    monkeypatch.setattr(runner.os,'replace',replace)
    monkeypatch.setattr(runner,'sleep',lambda _:None)
    target=tmp_path/'trajectory.npz'
    runner._atomic_npz(target,positions=np.arange(6).reshape(3,2))
    with np.load(target,allow_pickle=False) as saved:
        assert np.array_equal(saved['positions'],np.arange(6).reshape(3,2))
    assert len(attempts)==2


def test_other_permission_failures_are_not_retried(tmp_path,monkeypatch):
    attempts=[]
    def denied(*_):attempts.append(1);raise PermissionError('ordinary access denial')
    monkeypatch.setattr(runner.os,'replace',denied)
    monkeypatch.setattr(runner,'sleep',lambda _:pytest.fail('must not retry unrelated error'))
    with pytest.raises(PermissionError):runner._atomic_text(tmp_path/'state.json','new')
    assert len(attempts)==1


def test_persistent_windows_denial_preserves_old_file_and_raises(tmp_path,monkeypatch):
    target=tmp_path/'state.json';target.write_text('old')
    attempts=[]
    def denied(*_):attempts.append(1);raise windows_permission_error(5)
    monkeypatch.setattr(runner.os,'replace',denied)
    monkeypatch.setattr(runner,'sleep',lambda _:None)
    with pytest.raises(PermissionError):runner._atomic_text(target,'new')
    assert len(attempts)==3 and target.read_text()=='old'
