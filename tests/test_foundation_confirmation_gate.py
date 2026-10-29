import importlib.util
import json

from udc.experiments.static_multigroup import ROOT

spec = importlib.util.spec_from_file_location('confirmation_gate', ROOT / 'scripts/foundation_confirmation_gate.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def save(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def fixture(root):
    save(root, 'control/status.json', {'phase': 'completed'})
    save(root, 'control/completion.json', {'batch_completed': True})
    save(root, 'manifest.json', {'scheduled_step_records': 16308})
    save(root, 'summary.json', {'stopped': False, 'recorded_step_records': 16308,
        'scheduled_step_records': 16308, 'sequence_completed': 324, 'episodes_recorded': 324,
        'statuses': {'FULL_SUCCESS': 1, 'PARTIAL_SERVICE': 16307}})
    save(root, 'readonly_audit/offline_audit.json', {'all_match': True, 'mismatches': [],
        'checked_step_records': 16308, 'by_method': {'counts_phase_guard': {'nontrivial_hold_frames': 4},
        'recompute_all': {'count_switch_events': 1}}})


def test_full_development_and_replay_activate_confirmation(tmp_path):
    fixture(tmp_path)
    assert gate.readiness(tmp_path)['state'] == 'ready'


def test_one_unfinished_sequence_cannot_pass_gate(tmp_path):
    fixture(tmp_path)
    path = tmp_path / 'summary.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['sequence_completed'] -= 1
    save(tmp_path, 'summary.json', data)
    assert gate.readiness(tmp_path)['state'] == 'rejected'


def test_audit_mismatch_cannot_pass_gate(tmp_path):
    fixture(tmp_path)
    path = tmp_path / 'readonly_audit/offline_audit.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['mismatches'] = ['altered saved interval']
    save(tmp_path, 'readonly_audit/offline_audit.json', data)
    assert gate.readiness(tmp_path)['state'] == 'rejected'
