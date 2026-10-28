import json

import numpy as np
import pytest

from udc.experiments import translation_service_pilot as module
from udc.multigroup.geometry import ScientificFailure


@pytest.mark.parametrize('program_error', [False, True])
def test_sequence_failure_keeps_artifacts_and_unexecuted_denominator(tmp_path, monkeypatch, program_error):
    tasks = [{'task_id': str(i), 'sequence_id': 'fixed'} for i in range(3)]
    (tmp_path / 'manifest.json').write_text(json.dumps({'tasks': tasks}))
    np.savez(tmp_path / 'truth.npz', guides=np.zeros((2, 2)))
    def fail(_):
        if program_error:
            raise RuntimeError('deliberate program error')
        raise ScientificFailure('PARTITION_CHANGED', 'deliberate expected failure')
    monkeypatch.setattr(module, '_sequence_impl', fail)
    rows = module.sequence_worker((str(tmp_path), {'sequence_id': 'fixed', 'truth': 'truth.npz'}, {'model': {}}, {}))
    if program_error:
        assert len(rows) == 1 and rows[0]['status'] == 'EXPERIMENT_ERROR'
        assert not (tmp_path / 'results/1').exists()
    else:
        assert [r['status'] for r in rows] == ['PARTITION_CHANGED', 'NOT_EXECUTED_SEQUENCE_FAILURE', 'NOT_EXECUTED_SEQUENCE_FAILURE']
    for row in rows:
        assert (tmp_path / 'results' / row['task_id'] / 'receipt.json').is_file()
