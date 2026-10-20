"""Post-run read-only identity audit; never participates in experiment decisions."""
from pathlib import Path
from collections import Counter
import numpy as np
import yaml
from udc.experiments.demand_pilot import read, arrays
from udc.experiments.nonconvex_rebuild import write, array_hash, sha
from udc.experiments.resource_confirmation import locked, OUTPUT
from udc.experiments.demand_confirmation import assignment_trace_check

root=Path(OUTPUT).resolve(); lock=locked(root,full=True); frozen=read(root/'inputs_frozen.json')
rows=read(root/'manifest_360.json')['rows']; checks=[]; initial_checks=[]
for key,source in frozen['sources'].items():
    data=arrays(root/'physical'/key/'input.npz'); n0=source['count']
    groups=[arrays(root/'initials'/f'{key}__N_{n0+n}'/'guides.npz') for n in (0,2,4)]
    c={'physical_key':key,'base_exact':np.array_equal(data['guides'],groups[0]['guides']),
       'nested_exact':all(np.array_equal(groups[i]['guides'],groups[i+1]['guides'][:len(groups[i]['guides'])]) for i in (0,1)),
       'ids_exact':all(np.array_equal(a['ids'],np.arange(n0+n)) for a,n in zip(groups,(0,2,4)))}
    assert all(v for k,v in c.items() if k!='physical_key'); initial_checks.append(c)
for row in rows:
    directory=root/'results'/row['task_id']; r=read(directory/'protocol_metrics.json'); raw=read(directory/'metrics.json')
    a=arrays(directory/'trajectory.npz'); init=frozen['starts'][row['initial_key']]
    data=arrays(Path(init['physical'])/'input.npz'); field=arrays(Path(init['condition'])/'demand.npz')
    expected=arrays(init['guides'])['guides']; source=frozen['sources'][row['physical_key']]
    checks_row={'task_id':row['task_id'],'frozen_positions_exact':np.array_equal(a['positions'][0],expected),
        'frozen_people_exact':np.array_equal(a['crowd_positions'],data['people']),
        'frozen_radii_exact':np.array_equal(a['crowd_radii'],data['radii']),
        'frozen_demand_exact':np.array_equal(a['crowd_demand'],field['demand']),
        'config_exact':yaml.safe_load((directory/'config.yaml').read_text(encoding='utf-8'))==yaml.safe_load(Path(init['config']).read_text(encoding='utf-8')),
        'orchestration_ok':r['orchestration_status']=='OK','raw_status_preserved':raw['termination_status']==r['runner_status'],
        'original_source_sha256':r['source_sha256']==lock['provenance']['source_sha256'],
        'independent_consistent':r['offline_consistent']}
    if r['target_valid']:
        checks_row['domain_hash_exact']=r['domain_sha256']==source['static_domain_sha256']
        checks_row['curve_hash_exact']=r['curve_sha256']==source['boundary_sha256']
        checks_row['phase_budget_exact']=r['phase']['candidates']==256
    if r['executed']:
        initial=arrays(root/'plans'/row['task_id']/'assignment.npz')['assignment']
        trace=assignment_trace_check(initial,a['assignment'],a['targets'],raw['recovery_attempts'])
        checks_row['assignment_chain_exact']=trace['accepted']
        checks_row['raw_execution_source_clean']=raw['commit']==lock['provenance']['commit'] and not raw['dirty'] and raw['source_sha256']==lock['provenance']['source_sha256']
        checks_row['budget_within_three']=r['recoveries']<=3
        checks_row['counterfactual_disabled']=raw['diagnostic_priority_stall_counterfactual'] is False
    else:
        checks_row['missing_motion_null']=r['motion'] is None and r['recoveries'] is None
    assert all(v for k,v in checks_row.items() if k!='task_id'),checks_row
    checks.append(checks_row)
details=[read(root/'results'/r['task_id']/'protocol_metrics.json') for r in rows]
motion=[r['motion'] for r in details if r['executed']]
integrations=read(root/'all_independent_integrations.json')
rejudges=read(root/'all_independent_checks.json')
assert all(c['main_matches'] and c['fine_matches'] for c in integrations)
assert all(all(c[k] for k in ('criteria_consistent','diagnostics_consistent','success_consistent')) for c in rejudges)
old=Path('runs/step1_nonconvex_demand_confirmation_v1/physical')
oldhash={array_hash(people=a['people'],radii=a['radii'],room=a['room']) for p in old.glob('*/input.npz') for a in [arrays(p)]}
newhash={array_hash(people=a['people'],radii=a['radii'],room=a['room']) for p in (root/'physical').glob('*/input.npz') for a in [arrays(p)]}
summary={'all_rows':len(rows),'rows_identity_checks':len(checks),'initial_physical_units':len(initial_checks),
    'old_60_personnel_hash_overlap':len(oldhash&newhash),'new_personnel_hash_unique':len(newhash),
    'executed_rows':len(motion),'executed_intervals':sum(m['executed_intervals'] for m in motion),
    'unsafe_intervals':sum(m['unsafe_interval_count'] for m in motion),
    'independent_rows':len(rejudges),'integrated_targets':len(integrations),
    'main_and_fine_integrations_match':True,'all_scientific_and_historical_locks_verified':True,
    'protected_historical_files':len(lock['historical_files']),'frozen_input_files':len(frozen['files']),
    'manifest_sha256':sha(root/'manifest_360.json'),'protocol_sha256':sha(root/'protocol.json'),
    'inputs_lock_sha256':sha(root/'inputs_frozen.json'),
    'recoveries':dict(Counter(r['recoveries'] for r in details if r['executed'])),
    'failed_queries':sum(not e.get('accepted',False) for r in rows for e in read(root/'results'/r['task_id']/'metrics.json').get('recovery_attempts',[])),
    'audit_script_sha256':sha(__file__)}
write(root/'final_identity_checks_360.json',checks); write(root/'nested_initial_checks_60.json',initial_checks)
write(root/'final_checks.json',summary); print(summary)
