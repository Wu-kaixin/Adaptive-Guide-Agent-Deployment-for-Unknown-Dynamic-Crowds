"""Frozen reconstruction inputs, separated streams, and snapshot safety."""
import copy
import json

import numpy as np
import pytest
import yaml

from udc.environment import Environment
from udc.experiments.config import load_config
from udc.experiments.nonconvex_rebuild import draw_cell, load_protocol, array_hash, rejected_result, run_task


def test_rebuild_draw_is_shared_and_does_not_couple_people_to_guide_jitter():
    spec, base = load_protocol('configs/step1/nonconvex_record_rebuild_v1.yaml')
    scene = next(item for item in base['scenes'] if item['name'] == 'wide_u')
    first, streams = draw_cell(spec, base, scene, 21)
    again, _ = draw_cell(spec, base, scene, 21)
    for key in first:
        np.testing.assert_array_equal(first[key], again[key])
    changed = copy.deepcopy(spec)
    changed['guide_arc_jitter_half_width_m'] *= 0.5
    other, _ = draw_cell(changed, base, scene, 21)
    np.testing.assert_array_equal(other['people'], first['people'])
    assert not np.array_equal(other['guides'], first['guides'])
    assert streams['people_spawn_key'] != streams['guides_spawn_key']
    assert streams['inset_line_length_m'] == 112.
    assert np.all(np.min(np.abs(first['guides'] - 1.), axis=1) < 1e-12) or np.all(
        np.minimum(np.min(np.abs(first['guides'] - 1.), axis=1),
                   np.min(np.abs(first['guides'] - 29.), axis=1)) < 1e-12)
    assert np.max(np.abs(first['guide_arc_jitter'])) <= 0.35
    assert array_hash(people=first['people'], guides=first['guides']) != array_hash(
        people=first['people'], guides=other['guides'])


def test_explicit_guide_snapshot_keeps_full_precision_and_does_not_alias(tmp_path):
    config = load_config('configs/step1/static_50.yaml')
    env = Environment(config)
    points = env.initialize_guides(config['guides'])
    points[0, 1] += 0.1234567890123
    config['guides']['initial_positions'] = points.tolist()
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    loaded = load_config(path)
    restored = env.initialize_guides(loaded['guides'])
    np.testing.assert_array_equal(restored, points)
    restored[0] = 0.
    np.testing.assert_array_equal(env.initialize_guides(loaded['guides']), points)
    config['guides']['initial_positions'][0][0] = float('nan')
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    with pytest.raises(ValueError, match='initial_positions'):
        load_config(path)


def test_rejected_input_keeps_artifacts_and_three_failure_rows(tmp_path):
    cell = tmp_path / 'input'
    cell.mkdir()
    np.savez(cell / 'input.npz', people=np.array([[5., 5.], [5.1, 5.]]),
             guides=np.array([[1., 1.], [1., 9.], [9., 9.]]),
             radii=np.array([0.2, 0.2]), demand=np.ones(2), room=np.array([10., 10.]))
    (cell / 'input.yaml').write_text('step: 1\n', encoding='utf-8')
    for method in ('equal_arc', 'projected_mass', 'pairwise_euclidean'):
        target = tmp_path / method
        result = rejected_result(target, {'method': method, 'count': 3, 'input_sha256': 'fixed'},
                                 cell, 'INITIALIZATION_INVALID', 'separation failed; no redraw')
        assert not result['success'] and result['steps'] == 0
        assert all((target / name).is_file() for name in
                   ('metrics.json', 'state.json', 'trajectory.npz', 'planning.json', 'config.yaml', 'reevaluated_metrics.json'))
        offline = json.loads((target / 'reevaluated_metrics.json').read_text(encoding='utf-8'))
        assert offline['criteria_consistent'] and not offline['success']


def test_existing_evidence_is_not_overwritten_when_source_changes(tmp_path):
    from udc.experiments.nonconvex_rebuild import sha
    cell=tmp_path/'inputs/cell'
    cell.mkdir(parents=True)
    (tmp_path/'protocol.json').write_text(json.dumps({'spec':{},'base_spec':{}}))
    (cell/'input.npz').write_bytes(b'frozen')
    (cell/'input.yaml').write_text('frozen')
    (cell/'input_record.json').write_text(json.dumps({'input_file_sha256':sha(cell/'input.npz'),
                                                    'config_sha256':sha(cell/'input.yaml')}))
    run=tmp_path/'baseline/wide_u/seed_22/equal_arc'
    run.mkdir(parents=True)
    (run/'state.json').write_text('{"status":"completed"}')
    saved='{"source_sha256":"historic","termination_status":"PATH_UNREACHABLE"}'
    (run/'metrics.json').write_text(saved)
    row={'cell':'inputs/cell','scene':'wide_u','seed':22,'method':'equal_arc','count':19}
    with pytest.raises(FileExistsError,match='choose a new --tag'):
        run_task((str(tmp_path),row,'baseline'))
    assert (run/'metrics.json').read_text()==saved


def test_confirmation_protocol_changes_only_seeds_and_recorded_statistics():
    previous,_=load_protocol('configs/step1/nonconvex_record_rebuild_v1.yaml')
    confirmation,_=load_protocol('configs/step1/nonconvex_confirmation_v2.yaml')
    assert confirmation['seeds']==list(range(25,35))
    for key in ('scenes','methods','guide_counts','people_jitter_std_m','guide_arc_jitter_half_width_m',
                'guide_phase_fraction','random_namespace','density','solver_maxiter','per_start_timeout_s','phase_fractions'):
        assert confirmation[key]==previous[key]
    assert confirmation['statistics']['task_denominator']==90
    assert len(confirmation['scenes'])*len(confirmation['methods'])*len(confirmation['seeds'])==90


def test_explicit_interruption_resume_restarts_only_identical_inputs(tmp_path,monkeypatch):
    from udc.controller import ABCGController
    from udc.experiments.runner import experiment
    destination=tmp_path/'interrupted'
    def interrupt(*a,**k):
        raise KeyboardInterrupt()
    with monkeypatch.context() as patch:
        patch.setattr(ABCGController,'step',interrupt)
        with pytest.raises(KeyboardInterrupt):
            experiment('configs/step1/static_50.yaml',destination,plots=False,protocol='fixed_n',fixed_n=8,method='equal_arc')
    state=json.loads((destination/'state.json').read_text())
    assert state['status']=='started' and 'source_sha256' in state
    result=experiment('configs/step1/static_50.yaml',destination,plots=False,protocol='fixed_n',fixed_n=8,
                      method='equal_arc',resume=True,restart_interrupted=True)
    assert result['success']
    assert (destination/'resumed_interruption.json').is_file()
