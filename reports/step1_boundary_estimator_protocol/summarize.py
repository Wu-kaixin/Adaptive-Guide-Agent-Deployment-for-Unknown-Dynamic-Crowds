"""Report existing paired protocol results; never generate or execute inputs."""
import argparse
from collections import Counter
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

import psutil
import udc


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path,value):
    Path(path).write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def paired_table(rows):
    table=Counter(); units=[]
    for scene,seed in sorted({(r['scene'],r['seed']) for r in rows if r['common_input_legal']}):
        pair={r['estimator']:r for r in rows if r['scene']==scene and r['seed']==seed}
        if len(pair)!=2: raise ValueError('Incomplete paired unit')
        if any(r['termination_status']=='NOT_EXECUTED' for r in pair.values()):
            state='unresolved_due_to_nonexecution'
        else:
            success={k:bool((r['common_task'] or {}).get('complete_deployment_success')) for k,r in pair.items()}
            state=('both_success' if all(success.values()) else 'both_failed' if not any(success.values())
                   else 'convex_only_success' if success['convex_hull'] else 'disk_only_success')
        table[state]+=1
        units.append({'scene':scene,'seed':seed,'joint_status':state,
            'convex_hull':pair['convex_hull']['termination_status'],
            'disk_union_closed':pair['disk_union_closed']['termination_status']})
    denominator=len(units)
    delta=None if table['unresolved_due_to_nonexecution'] or not denominator else (
        table['disk_only_success']-table['convex_only_success'])/denominator
    return {'common_legal_denominator':denominator,'joint_outcomes':dict(table),
            'success_fraction_difference_disk_minus_convex':delta,'paired_units':units}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='runs/step1_boundary_estimator_protocol')
    parser.add_argument('--phase',choices=['smoke','future'],default='smoke')
    parser.add_argument('--output',default=str(Path(__file__).resolve().parent))
    args=parser.parse_args(); root=Path(args.root); out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    summary=read(root/(args.phase+'_summary.json')); lock=read(root/'protocol.json')
    rows=summary['rows']; write(out/'summary.json',summary)
    write(out/'paired_statistics.json',paired_table(rows))
    write(out/'future_manifest_120.json',read(root/'future_manifest_120.json'))
    write(out/'seed_audit.json',read(root/'seed_audit.json'))
    references=lock['references'] if args.phase=='smoke' else read(root/'future_inputs_frozen.json')['references']
    write(out/'input_references.json',references)
    columns=['task_id','scene','seed','estimator','count','input_sha256','input_origin','common_input_legal',
        'termination_status','failure_reason','component_count','occupied_holes','occupied_area_m2',
        'occupied_perimeter_m','extra_envelope_area_m2','controller_extra_area_m2','controller_filled_hole_area_m2',
        'covers_observed_disks_32_strict','uncovered_disk_area_m2','controller_covers_acceptance_disks_16',
        'curve_length_m','curve_area_m2','necessary_min_count','fixed_count','count_certificate',
        'target_geometry_ok','static_targets_ok','initial_paths_ok','domain_sha256','strict_illegal_length_m',
        'phase_triggered','phase_selected_m','simulation_time_s','actual_trajectory_length_m',
        'interval_safety_ok','unsafe_interval_count','terminal_arrival_ok','recovery_count',
        'curve_internal_cost_not_for_ranking','total_wall_s','offline_consistent','source_sha256']
    records=[]; timing=[]
    for row in rows:
        record={**row,**(row.get('boundary_description') or {}),**row.get('resource',{}),**(row['common_task'] or {})}
        record['strict_illegal_length_m']=row.get('curve_domain',{}).get('strict_illegal_length_m')
        record['curve_internal_cost_not_for_ranking']=(row['curve_internal'] or {}).get('constant_density_quadrature_cost')
        records.append(record)
        timing.append({'task_id':row['task_id'],**row['timing'],'total_wall_s':row['total_wall_s']})
    with (out/'tasks_16.csv' if args.phase=='smoke' else out/'tasks_120.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,extrasaction='ignore'); writer.writeheader(); writer.writerows(records)
    write(out/'timing.json',timing)
    invariants=[]
    for key,ref in references.items():
        pair=[r for r in rows if f"{r['scene']}__seed_{r['seed']}"==key]
        if not pair: continue
        invariants.append({'unit':key,'same_input_sha256':len({r['input_sha256'] for r in pair})==1,
            'same_fixed_count':len({r['count'] for r in pair})==1,
            'common_config_sha256':ref['config_sha256'],
            'reference_disk_hashes':[r['boundary_description']['reference_wkb_sha256'] for r in pair if r['boundary_description']],
            'paired_physical_reference_equal':len({r['boundary_description']['reference_wkb_sha256'] for r in pair if r['boundary_description']})<=1})
    write(out/'pairing_checks.json',invariants)
    baseline_root=Path(lock['spec']['smoke_input_root'])
    preservation=[]
    for row in rows:
        if row['estimator']!='disk_union_closed' or not row['boundary_description'] or not row['boundary_description']['curve_valid']: continue
        import numpy as np
        saved=baseline_root/'inputs'/row['scene']/f"seed_{row['seed']}"/'boundary.npz'
        current=root/args.phase/'plans'/row['task_id']/'boundary.npz'
        if saved.exists():
            with np.load(saved) as a,np.load(current) as b:
                preservation.append({'task_id':row['task_id'],'same_boundary_arrays':all(np.array_equal(a[k],b[k]) for k in a.files)})
    write(out/'baseline_boundary_preservation.json',preservation)
    write(out/'environment.json',{'python':sys.version,'python_executable':sys.executable,'udc_import_path':udc.__file__,
        'platform':platform.platform(),'logical_processors':psutil.cpu_count(), 'physical_cores':psutil.cpu_count(logical=False),
        'memory_total_bytes':psutil.virtual_memory().total,'workers':read(root/(args.phase+'_freeze.json'))['workers'],
        'numerical_threads_per_worker':1,'dependencies':{k:importlib.metadata.version(k) for k in
            ('numpy','scipy','shapely','jupedsim','PedPy','pytest')},
        'baseline_commit':lock['spec']['baseline_commit'],'provenance':lock['provenance'],
        'spec_sha256':lock['spec_sha256'],'generator_spec_sha256':lock['generator_spec_sha256'],
        'base_spec_sha256':lock['base_spec_sha256'],
        'future_inputs_exist':(root/'future_inputs').exists(),'future_execution_exists':(root/'future').exists()})
    write(out/'raw_checksums.json',{str(p):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob('*')) if p.is_file()})
    print(json.dumps({'planned_rows':summary['planned_rows'],'people_legal_units':summary['people_legal_common_units'],
        'executed_rows':summary['executed_rows'],'outcomes':summary['outcomes_all_planned_rows'],
        'baseline_boundary_arrays_unchanged':all(r['same_boundary_arrays'] for r in preservation),
        'future_inputs_generated':(root/'future_inputs').exists()}))


if __name__=='__main__': main()
