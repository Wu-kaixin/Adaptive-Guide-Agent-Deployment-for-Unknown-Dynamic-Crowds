import json

from udc.analysis.path_failure_census import digest, select_cases


def make_results(tmp_path):
    tasks = []
    for task, status in [('old', 'PATH_NOT_FOUND'), ('new', 'PATH_NOT_FOUND'), ('ok', 'FULL_SUCCESS')]:
        folder = tmp_path / 'results' / task
        folder.mkdir(parents=True)
        (folder / 'metrics.json').write_text(json.dumps({'status': status}))
        (folder / 'receipt.json').write_text('{}')
        tasks.append({'task_id': task, 'unit_id': 'u'})
    tasks.append({'task_id': 'not_run', 'unit_id': 'v'})
    return {'tasks': tasks}


def test_checkpoint_failure_cohort_does_not_include_later_results(tmp_path):
    manifest = make_results(tmp_path)
    checkpoint = tmp_path / 'checkpoint'
    checkpoint.mkdir()
    expected = digest(tmp_path / 'results/old/receipt.json')
    (checkpoint / 'preserved_receipts.json').write_text(json.dumps({'old': expected}))
    units, hashes = select_cases(tmp_path, manifest, checkpoint)
    assert units == {'u': ['old']} and hashes == {'old': expected}


def test_final_failure_census_includes_all_recorded_failures_only(tmp_path):
    manifest = make_results(tmp_path)
    units, hashes = select_cases(tmp_path, manifest)
    assert units == {'u': ['old', 'new']} and set(hashes) == {'old', 'new'}
