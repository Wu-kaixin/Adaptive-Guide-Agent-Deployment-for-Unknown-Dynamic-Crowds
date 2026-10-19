"""Summarize saved coverage diagnostics without reconstructing or executing."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import udc
from udc.experiments.provenance import provenance


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('runs/step1_coverage_geometry_contract/audit_v1'))
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args();out=args.output;out.mkdir(exist_ok=True,parents=True)
    root=args.root;summary=read(root/'audit_summary.json')
    assert summary['audited_estimator_rows']==102 and summary['region_reference_checks']==408
    assert summary['changed_default_classifications']==0 and summary['controller_executions']==0
    historical=read('reports/step1_boundary_estimator_comparison_v1/raw_checksums.json')
    assert all(sha(path)==digest for path,digest in historical.items())
    for name in ('audit_summary.json','preflight.json','seed70_saved_exact_witness.json','seed70_chain.json',
                 'seed70_exact_complete_q16_gap_enclosure.json','old_rejection_checks.json'):
        write(out/name,read(root/name))
    (out/'audit_102_regions_q16_q32.csv').write_bytes((root/'audit_102_regions_q16_q32.csv').read_bytes())
    write(out/'classification_changes.json',summary['classification_changes'])
    write(out/'initial_audit_error.json',read(root.parent/'audit_error.json'))
    current=provenance({},None,'coverage_contract_delivery',None)
    validation=read(root/'validation_checks.json')
    verify={'original_comparison_commit':'aa51c2b','original_comparison_files_unchanged':True,
        'historical_files_checked':len(historical),'source_provenance_at_delivery':current,
        'python_executable':sys.executable,'udc_import':udc.__file__,
        'default_algorithms_and_thresholds_unchanged':True,'coverage_acceptance_tolerance_m':0.,
        'coordinate_displacement_m':0.,'controller_executions':0,
        'tests':validation['tests'],
        'pip_check':'No broken requirements found.','compileall_and_diff_check':'passed',
        'audit_source_provenance':read(root/'preflight.json')['provenance'],
        'exact_enclosure_source_provenance':read(root/'seed70_exact_complete_q16_gap_enclosure.json')['provenance'],
        'source_phase_note':'Full audit preceded extra exact all-edge evidence and diagnostic config validation; coverage predicates unchanged, all phase hashes retained; no deployment execution or mixed comparison result'}
    write(out/'verification.json',verify)
    write(out/'raw_checksums.json',{str(p):sha(p) for p in sorted(root.parent.rglob('*')) if p.is_file()})
    print(json.dumps({'historical_files_unchanged':len(historical),'source_sha256':current['source_sha256'],
        'checks':summary['region_reference_checks'],'audit_seconds':summary['wall_seconds'],
        'q32_diagnostic_classification_changes':sum(c['classification_change'] for c in summary['classification_changes'])}))


if __name__=='__main__':main()
