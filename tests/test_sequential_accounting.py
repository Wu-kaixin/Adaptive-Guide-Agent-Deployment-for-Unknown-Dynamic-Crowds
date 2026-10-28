from concurrent.futures import Future
import json

from udc.experiments import sequential_deployment as module


def test_orchestration_failure_preserves_every_unit_and_missing_denominator(tmp_path,monkeypatch):
    class FailedPool:
        def __init__(self,*a,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def submit(self,*args):
            future=Future();future.set_exception(RuntimeError('deliberate orchestration error'));return future
    manifest={'units':[{'unit_id':'a'},{'unit_id':'b'},{'unit_id':'c'}],'scheduled_step_records':9}
    monkeypatch.setattr(module,'verify',lambda study:manifest)
    monkeypatch.setattr(module,'ProcessPoolExecutor',FailedPool)
    result=module.run(tmp_path,3)
    assert result['stopped'] and result['recorded_step_records']==0 and result['not_recorded_steps']==9
    assert result['orchestration_error_units']==3
    assert {p.stem for p in (tmp_path/'orchestration_errors').glob('*.json')}=={'a','b','c'}
    assert (tmp_path/'unit_progress').is_dir()
