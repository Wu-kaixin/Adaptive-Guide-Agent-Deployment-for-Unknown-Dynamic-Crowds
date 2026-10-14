"""Regressions for imported sources, true mass density and analysis accounting."""
from pathlib import Path
import hashlib
import json
import sqlite3

import numpy as np
import pytest
import yaml

from udc.analysis.pedestrian import analyze
from udc.analysis.summary import summarize_results
from udc.controller.coverage import _density
from udc.environment import Environment
from udc.experiments.config import load_config
from udc.experiments.runner import experiment, reevaluate_result

ROOT = Path(__file__).resolve().parents[1]


def test_mass_density_matches_unshifted_gaussian_relative_mass():
    people = np.array([[0., 0.], [1., .2], [-.5, .8]])
    points = np.array([[1., 0.], [4., 0.], [0., 2.]])
    demand = np.array([1., 3., .5])
    expected = np.exp(-np.sum((points[:, None]-people)**2, axis=2)/2) @ demand
    np.testing.assert_allclose(_density(points, people, demand, 1., 'mass_cvt'),
                               expected/expected.max(), rtol=1e-13)


def test_50_person_case_is_frozen_and_independently_accepted(tmp_path):
    config = ROOT/'configs/step1/static_50.yaml'
    environment = Environment(load_config(config))
    observed = environment.observe()
    expected = np.array([(16.+.75*c, 18.+.75*r) for r in range(5) for c in range(10)])
    np.testing.assert_array_equal(observed.positions, expected)
    environment.advance(10.)
    np.testing.assert_array_equal(environment.observe().positions, expected)
    output = tmp_path/'fifty'
    metrics = experiment(config, output, plots=False)
    assert metrics['success'] and metrics['crowd_static']
    assert metrics['active_guides'] == 8
    assert reevaluate_result(output)['success']
    report = analyze(tmp_path/'analysis', result=output)
    assert report['pedestrians'] == 50 and report['frames'] == 1
    assert report['source_kind'] == 'frozen_snapshot'


def test_sqlite_source_reads_first_frame_without_mutating_database(tmp_path):
    database = tmp_path/'trajectory.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE trajectory_data (frame INTEGER, id INTEGER, pos_x REAL, pos_y REAL)')
        connection.executemany('INSERT INTO trajectory_data VALUES (?,?,?,?)',
                              [(0, 3, 5., 5.), (0, 1, 3., 3.), (0, 2, 5., 3.),
                               (1, 3, 7., 7.), (1, 1, 4., 4.), (1, 2, 6., 4.)])
    cfg = load_config(ROOT/'configs/step1/square.yaml')
    cfg['crowd'].update(source='sqlite', count=3, trajectory_file=database.name)
    config = tmp_path/'case.yaml'; config.write_text(yaml.safe_dump(cfg))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    environment = Environment(load_config(config))
    environment.advance(1.)
    np.testing.assert_array_equal(environment.observe().positions, [[3., 3.], [5., 3.], [5., 5.]])
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_failures_and_program_errors_stay_in_summary_denominator(tmp_path):
    for index, success in enumerate([True, False]):
        directory = tmp_path/str(index); directory.mkdir()
        (directory/'metrics.json').write_text(json.dumps({
            'scene': 'square', 'method': 'equal_arc', 'protocol': 'fixed_n',
            'success': success, 'termination_status': 'SUCCESS' if success else 'TIMEOUT',
            'simulation_time': 5.,
        }))
    directory = tmp_path/'error'; directory.mkdir()
    (directory/'runner_error.txt').write_text('Example error')
    report = summarize_results(tmp_path)
    assert report['tasks'] == 3
    group = report['groups']['square|equal_arc|fixed_n']
    assert group['success_rate'] == .5
    assert group['successful_time_count'] == 1
    assert group['success_rate_ci95_wilson'][0] < .5 < group['success_rate_ci95_wilson'][1]


def test_invalid_configuration_failure_can_be_reevaluated(tmp_path):
    config = tmp_path/'invalid.yaml'; config.write_text('step: 2\n')
    output = tmp_path/'failed'
    metrics = experiment(config, output, plots=False)
    assert metrics['termination_status'] == 'INITIALIZATION_INVALID'
    assert not reevaluate_result(output)['success']
