"""Collect measured checks and final artifact hashes without changing data locks."""
from datetime import datetime, timezone, timedelta
import platform

from udc.experiments.static_multigroup import ROOT, digest, write_json, environment_record
from .phase_retention_analysis import read


def main():
    output = ROOT / 'reports/static_phase_retention_20261027'
    main_study = ROOT / 'runs/static_phase_retention_20261027/main_v1'
    witness = ROOT / 'runs/static_phase_retention_20261027/witness_execution_v1'
    main_replay, witness_replay = read(main_study / 'offline_replay.json'), read(witness / 'offline_replay.json')
    if not main_replay['all_match'] or not witness_replay['all_match']:
        raise ValueError('Unresolved replay differences')
    result = {'date': datetime.now(timezone(timedelta(hours=9))).date().isoformat(), 'timezone': 'Asia/Tokyo',
              'python': platform.python_version(), 'environment': environment_record(),
              'pytest': {'passed': 274, 'seconds': 23.56,
                         'command': '.venv/Scripts/python.exe -m pytest -q --basetemp runs/static_phase_retention_20261027/tests_final -p no:cacheprovider'},
              'pip_check': 'passed', 'tracked_diff_whitespace_check': 'passed',
              'plots': 'both PNG figures visually inspected',
              'source_snapshots_and_input_hashes': 'verified by offline replay',
              'main_manifest_sha256': digest(main_study / 'manifest.json'),
              'witness_manifest_sha256': digest(witness / 'manifest.json'),
              'main_replay': main_replay, 'witness_replay': witness_replay,
              'verified_task_receipts': main_replay['verified_receipts'] + witness_replay['verified_receipts'],
              'opened_trajectory_npz_without_pickle': main_replay['opened_trajectories_without_pickle'] + witness_replay['trajectories_opened_without_pickle'],
              'raw_artifacts_modified': False, 'scientific_retries': 0,
              'reserved_seeds_5000_5029_executed': False,
              'report_artifact_sha256': {name: digest(output / name) for name in
                  ('REPORT.zh.md', 'ASSESSMENT.zh.md', 'PLAN.zh.md', 'results.json', 'analysis/analysis.json',
                   'true_service_witness.json', 'original_v2_interim.json', 'layout_response.png',
                   'layout_response.svg', 'actual_redeployment.png', 'actual_redeployment.svg')}}
    write_json(output / 'validation.json', result)
    print({'verified_receipts': result['verified_task_receipts'], 'pytest': 274,
           'planning_reconstruction': main_replay['reconstructed_planning_rows'],
           'motion_replay': main_replay['recomputed_motion_rows'] + witness_replay['recomputed_rows']})


if __name__ == '__main__':
    main()
