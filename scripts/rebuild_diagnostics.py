"""Build new diagnostic locks over reconstructed data; old locks remain untouched."""
from pathlib import Path
import argparse
import json
import sys

from rebuild_missing_data import PROJECT, OUTPUT, snapshot, initialize, read, write, sha

def references(root):
    return {str(p.resolve()): sha(p) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}

def phase():
    import yaml
    from udc.experiments import phase_attribution as pa
    from udc.experiments.provenance import provenance
    source = OUTPUT / 'step1_nonconvex_demand_pilot_v1'
    assert read(source / 'reconstruction_summary.json')['completed'] == 36
    root = OUTPUT / 'step1_nonconvex_phase_attribution_v1'
    if root.exists(): raise FileExistsError('Diagnostic attempts are immutable')
    spec = yaml.safe_load((PROJECT / pa.SPEC).read_text(encoding='utf-8'))
    spec['source_run'] = str(source)
    old = read(source / 'protocol.json')
    rows = [r for r in read(source / 'manifest.json')['tasks'] if r['method'] in spec['methods']]
    assert len(rows) == 24
    write(root / 'protocol.json', {'spec': spec, 'old_protocol': old, 'references': references(source),
          'provenance': provenance({}, None, 'fixed_n', None), 'source_run': str(source),
          'reconstruction': True, 'no_new_person_draw_or_lloyd': True})
    write(root / 'layout_manifest_24.json', {'rows': rows, 'main_policy_rows': 96, 'sensitivity_P3_rows': 24})
    write(root / 'reconstruction.json', {'historical_original': False, 'independent_confirmation': False,
          'source_ref': 'e7abbc12', 'derived_from': str(source), 'scope': '96 policy rows, 24 half-grid diagnostics, 24 P3 executions'})
    pa.analyze(root)
    pa.run(root, 'first', 1)
    pa.run(root, 'remaining', 2)
    details = [read(p) for p in (root / 'execution').glob('*/protocol_metrics.json')]
    assert len(details) == 24 and not any(r['status'] in ('EXPERIMENT_ERROR', 'SAFETY_VIOLATION') for r in details)
    write(root / 'reconstruction_summary.json', {'policy_rows': 96, 'half_grid_rows': 24, 'completed': 24,
          'successes': sum(r['success'] for r in details), 'historical_original': False, 'rows': details})

def recovery():
    import yaml
    from udc.experiments import recovery_diagnostics as rd
    from udc.experiments.provenance import provenance
    import udc.analysis
    # The original A protocol was committed before its matrix exporter. Keep
    # production sources frozen and load the later read-only analysis adapter.
    udc.analysis.__path__.append(str(PROJECT / 'src/udc/analysis'))
    source = OUTPUT / 'step1_nonconvex_resource_response_v1'
    assert read(source / 'reconstruction_summary.json')['completed'] == 360
    root = OUTPUT / 'recovery_observation'
    if root.exists(): raise FileExistsError('Diagnostic attempts are immutable')
    row = next(r for r in read(source / 'manifest.json')['rows'] if r['scene'] == 'wide_u' and r['seed'] == 92 and r['count'] == 23 and r['method'] == 'projected_mass')
    result = source / 'results' / row['task_id']; plans = source / 'plans' / row['task_id']
    detail = read(result / 'protocol_metrics.json')
    initial = read(source / 'inputs_frozen.json')['starts'][row['initial_key']]
    entry = {**row, 'original_status': detail['status'], 'original_physical_success': detail['offline_success'],
             'original_result': str(result), 'original_plan': str(plans), 'physical': initial['physical'],
             'config': str(result / 'config.yaml'), 'initial_assignment': str(plans / 'assignment.npz')}
    spec = yaml.safe_load((PROJECT / rd.SPEC).read_text(encoding='utf-8')); spec['focal'] = row['task_id']
    write(root / 'manifest_360.json', [entry])
    write(root / 'protocol.json', {'spec': spec, 'provenance': provenance({}, None, 'fixed_n', None),
          'protected': references(source), 'references': references(source), 'manifest_sha256': sha(root / 'manifest_360.json'),
          'config_sha256': sha(PROJECT / rd.SPEC), 'reconstruction': True,
          'initially_unchanged_science': {str(p): sha(p) for p in Path(rd.__file__).resolve().parents[1].rglob('*.py') if any(n in p.parts for n in ('controller', 'navigation', 'safety', 'evaluation'))}})
    write(root / 'reconstruction.json', {'historical_original': False, 'independent_confirmation': False,
          'source_ref': 'f634725f', 'derived_from': str(source), 'scope': 'new A/B observation of reconstructed fixed-target failure'})
    rd.execute(root, entry, 'A_baseline')
    recovery_finish()

def recovery_finish():
    import ast
    from udc.experiments import recovery_diagnostics as rd
    import udc.analysis
    udc.analysis.__path__.append(str(PROJECT / 'src/udc/analysis'))
    from udc.analysis.recovery_diagnostics import matrix
    root = OUTPUT / 'recovery_observation'
    value = rd.lock(root, full=True)
    row = read(root / 'manifest_360.json')[0]
    baseline = read(root / 'A_baseline' / row['task_id'] / 'summary.json')
    assert baseline['criteria_consistent'] and baseline['targets_exact'] and baseline['input_exact']
    assert all(v['exact'] for v in baseline['prefix'].values())
    write(root / 'analysis_adapter.json', {'production_sources_unchanged': True,
          'adapter': 'src/udc/analysis/recovery_diagnostics.py', 'adapter_sha256': sha(PROJECT / 'src/udc/analysis/recovery_diagnostics.py'),
          'reason': 'Matrix exporter was not yet committed at f634725f; finish saved A observation without rerunning it',
          'A_execution_retries': 0})
    if not (root / 'matrix_A/summary.json').exists():
        matrix(root, 'A')
    # B was added after the baseline commit. Load only the already documented
    # counterfactual function into the frozen baseline's existing namespace;
    # no production file or threshold is changed.
    adapter_path = PROJECT / 'src/udc/experiments/recovery_diagnostics.py'
    parsed = ast.parse(adapter_path.read_text(encoding='utf-8'))
    function = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == 'boundary_contact_counterfactual')
    namespace = dict(rd.__dict__)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(adapter_path) + ':frozen_B_adapter', 'exec'), namespace)
    write(root / 'B_adapter.json', {'source': str(adapter_path), 'source_sha256': sha(adapter_path),
          'scope': 'existing documented B function only; frozen A production namespace and original 1e-7/1e-8 guards',
          'production_files_modified': False})
    namespace['boundary_contact_counterfactual'](root)
    write(root / 'reconstruction_summary.json', {'completed': 2, 'historical_original': False,
          'attempts': ['A_baseline', 'B_navigation_only'], 'A_execution_retries': 0})

def deadline():
    import yaml
    from udc.experiments import deadline_diagnostics as dd
    from udc.experiments.provenance import provenance
    source = OUTPUT / 'step1_nonconvex_resource_confirmation_v1'
    assert read(source / 'reconstruction_summary.json')['completed'] == 360
    root = OUTPUT / 'step1_execution_deadline_diagnostics_v1'
    if root.exists(): raise FileExistsError('Diagnostic attempts are immutable')
    row = next(r for r in read(source / 'manifest.json')['rows'] if r['scene'] == 'wide_u' and r['seed'] == 113 and r['count'] == 21 and r['method'] == 'equal_arc')
    detail = read(source / 'results' / row['task_id'] / 'protocol_metrics.json')
    if detail['status'] != 'TIMEOUT': raise RuntimeError('Reconstructed focal case differs; no automatic continuation')
    initial = read(source / 'inputs_frozen.json')['starts'][row['initial_key']]
    spec = yaml.safe_load((PROJECT / dd.SPEC).read_text(encoding='utf-8')); spec['focal'] = row['task_id']
    _, proof = dd.deadline_entry(600)
    science = {str(p): sha(p) for p in Path(dd.__file__).resolve().parents[1].rglob('*.py')}
    write(root / 'protocol.json', {'spec': spec, 'source': provenance({}, None, 'fixed_n', None),
          'production_sources': science, 'protected': references(source),
          'config_file_sha256': sha(PROJECT / dd.SPEC), 'protocol_text_sha256': sha(PROJECT / dd.REPORT / 'PROTOCOL.zh.md'),
          'row': row, 'initial': initial, 'original_result': str(source / 'results' / row['task_id']),
          'original_plan': str(source / 'plans' / row['task_id']), 'old_source': read(source / 'protocol.json')['provenance'],
          'original_config': yaml.safe_load(Path(initial['config']).read_text(encoding='utf-8')), 'deadline_transform': proof,
          'reconstruction': True})
    write(root / 'reconstruction.json', {'historical_original': False, 'independent_confirmation': False,
          'source_ref': 'c807c85f', 'derived_from': str(source), 'scope': 'one exact 40s reconstruction replay plus one bounded 60s diagnostic'})
    dd.execute(root, 'baseline_40s')
    dd.execute(root, 'diagnostic_continuation_60s')
    write(root / 'reconstruction_summary.json', {'completed': 2, 'historical_original': False,
          'baseline': read(root / 'baseline_40s/summary.json'), 'continuation': read(root / 'diagnostic_continuation_60s/summary.json')})

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--stage', choices=['phase', 'recovery', 'recovery_finish', 'deadline'], required=True)
    args = parser.parse_args()
    ref = {'phase': 'e7abbc12', 'recovery': 'f634725f', 'recovery_finish': 'f634725f', 'deadline': 'c807c85f'}[args.stage]
    snapshot(ref); initialize(ref)
    globals()[args.stage]()
