"""Read saved pilot evidence; independently rejudge every row without execution."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
import yaml

from udc.deployment.planners import demand_profile, coverage_statistics, quadrature_mass
from udc.evaluation.coverage import excess_coverage
from udc.experiments.demand_pilot import arrays, locked, read
from udc.experiments.nonconvex_rebuild import load_boundary, load_plan, sha, write
from udc.experiments.runner import reevaluate_result
from udc.experiments.target_domain import independent_geometry
from udc.navigation import navigation_domain


def main(root, report):
    root,report=Path(root).resolve(),Path(report).resolve()
    report.mkdir(parents=True,exist_ok=True)
    lock=locked(root)
    manifest=read(root/'manifest.json')
    spec=lock['spec']
    rows=[]
    failures=[]
    offline_checks=[]
    for item in manifest['tasks']:
        directory=root/'results'/item['task_id']
        row=read(directory/'protocol_metrics.json')
        metrics=read(directory/'metrics.json')
        cfg=yaml.safe_load((directory/'config.yaml').read_text(encoding='utf-8'))
        replay=reevaluate_result(directory)
        physical=root/'physical'/item['physical_key']
        b=load_boundary(physical)
        data=arrays(Path(lock['sources'][item['physical_key']]['source_cell'])/'input.npz')
        condition=root/'conditions'/item['condition_key']
        field=arrays(condition/'demand.npz')
        target_dir=root/'plans'/item['task_id']
        raw=load_plan(target_dir/'original')
        if not row['success']:
            gaps=np.diff(np.r_[raw.arc_positions,raw.arc_positions[0]+b.length])
            solver=read(target_dir/'original/solver.json')
            failures.append({'task_id':item['task_id'],'status':row['termination_status'],
                'constraint':'max consecutive arc gap <= 1.94 m (including closing seam)',
                'failed_legacy_phases':[a['legacy_phase'] for a in solver['attempts'] if not a['geometry_pass']],
                'selected_raw_attempt':solver['selected_attempt'],
                'violating_arc_pairs':[{'i':i,'j':(i+1)%len(gaps),'gap_m':float(g),
                    'first_arc_m':float(raw.arc_positions[i]),'second_arc_m':float(raw.arc_positions[(i+1)%len(gaps)]),
                    'first_position':raw.positions[i].tolist(),'second_position':raw.positions[(i+1)%len(gaps)].tolist()}
                    for i,g in enumerate(gaps) if g>1.94+1e-9],
                'selected_raw_domain_invalid_indices':row['original_domain']['invalid_target_indices'],
                'selected_raw_min_pair_m':float(np.min(arrays(target_dir/'original_geometry.npz')['all_pair_distances']+
                    np.eye(len(gaps))*1e9)),
                'not_executed':True,'simulation_time':None,'motion_safety':None})
        rho=demand_profile(b,data['people'],field['demand'],1.2)
        check={'task_id':item['task_id'],'criteria_consistent':replay['criteria_consistent'],
               'diagnostics_consistent':all(replay['diagnostics_consistent'].values()),
               'density_exact_match':bool(np.array_equal(rho,field['rho'])), 'score_match':True,
               'final_geometry_pass':None, 'runtime_pairing':None,'assignment_match':None}
        q,mass,length=quadrature_mass(b,rho,2048)
        for label in ('raw','final'):
            folder=target_dir/('original' if label=='raw' else 'final')
            if not folder.exists(): continue
            plan=load_plan(folder)
            # Independent evaluator reconstructs original total/excess/background
            # Gaussian masses from observed people and frozen demand, no planner rho.
            fresh=excess_coverage(b,data['people'],field['demand'],field['hotspot_mask'],1.2,2048,
                plan.positions,multiplier=5. if item['demand']=='hotspot5' else None)
            for metric in ('demand','excess','background'):
                key=f'planned_{metric}_weighted_cost'
                saved=row[label+'_score'][key]
                actual=fresh[key]
                check['score_match'] &= saved is actual if saved is None or actual is None else abs(saved-actual)<=1e-12
            if label=='final':
                domain=navigation_domain(data['room'],.55,Polygon(b.crowd),.55)
                check['final_geometry_pass']=independent_geometry(b,raw,plan,domain,lock['base'])['accepted']
        actual=arrays(directory/'trajectory.npz')
        executed=len(actual['positions'])>1
        if executed:
            final=load_plan(target_dir/'final')
            check['runtime_pairing']=all(np.array_equal(a,z) for a,z in [
                (actual['crowd_positions'],data['people']),(actual['crowd_radii'],data['radii']),
                (actual['crowd_demand'],field['demand']),(actual['crowd_hotspot_mask'],field['hotspot_mask']),
                (actual['positions'][0],data['guides']),(actual['room'],data['room']),
                (actual['targets'],final.positions),(actual['boundary_crowd'],b.crowd),
                (actual['boundary_deployment'],b.deployment)])
            check['assignment_match']=bool(np.array_equal(actual['assignment'],arrays(target_dir/'assignment.npz')['assignment']))
            assert row['motion'] is not None
        else:
            assert row['motion'] is None and row['replan_count'] is None
        check['executed']=executed
        check['termination_status']=row['termination_status']
        check['success_agrees']=bool(replay['success']==metrics['success']==row['success'])
        offline_checks.append(check)
        row['offline_rejudge']=check
        row['criteria']=metrics['criteria']
        row['recovery_summary']=[{k:e.get(k) for k in ('strategy','trigger_reason','frame','time_s',
            'accepted','budget_consumed','budget_remaining','classification')}
            for e in metrics.get('recovery_attempts',[])]
        coordination=metrics.get('coordination') or {}
        row['coordination_summary']={k:v for k,v in coordination.items()
            if k not in ('progress_decisions','progress_reference_changes')}
        row['raw_events_path']=(directory/'metrics.json').relative_to(root).as_posix()
        row['actual_max_gap']=metrics.get('actual_max_gap') if executed else None
        row['actual_mean_curve_distance']=metrics.get('mean_curve_distance') if executed else None
        row['actual_target_error']=metrics.get('max_target_error') if executed else None
        row['executed']=executed
        row['initial_navigation_s']=metrics.get('initial_navigation_wall_seconds') if executed else None
        row['original_arc_coordinates']=raw.arc_positions.tolist()
        if (target_dir/'final').exists():
            row['final_arc_coordinates']=load_plan(target_dir/'final').arc_positions.tolist()
        rows.append(row)
    # Do not silently omit an incomplete row.
    assert len(rows)==36
    assert all(all(c[k] for k in ('criteria_consistent','diagnostics_consistent','density_exact_match','score_match','success_agrees'))
               and c['runtime_pairing'] is not False and c['final_geometry_pass'] is not False
               and c['assignment_match'] is not False for c in offline_checks)
    pairs=[]
    for condition in lock['conditions']:
        group={r['method']:r for r in rows if r['condition_key']==condition}
        equal, mass, projected=[group[m] for m in spec['methods']]
        pair={'condition_key':condition,'demand':equal['demand'],
              'statuses':{m:r['termination_status'] for m,r in group.items()},'comparisons':{}}
        # Retention uses the rejected Mass-CVT raw witness only as a planning
        # diagnostic, never as observed execution benefit.
        e,p,m=[r['raw_score']['planned_demand_weighted_cost'] for r in (equal,projected,mass)]
        pair['raw_demand_gain_retention']=(e-p)/(e-m) if e-m>1e-12 else None
        ex,px,mx=[r['raw_score']['planned_excess_weighted_cost'] for r in (equal,projected,mass)]
        pair['raw_excess_gain_retention']=((ex-px)/(ex-mx)
            if ex is not None and ex-mx>1e-12 else None)
        ef=equal['final_score']['planned_demand_weighted_cost'] if equal['final_score'] else None
        pf=projected['final_score']['planned_demand_weighted_cost'] if projected['final_score'] else None
        pair['final_vs_final_gain_retention_reference_raw_mass']=(ef-pf)/(ef-m) if ef is not None and pf is not None and ef-m>1e-12 else None
        for method,r in group.items():
            comparison={}
            for label in ('raw','final'):
                for metric in ('demand','excess','background'):
                    key=f'planned_{metric}_weighted_cost'
                    baseline=(equal[label+'_score'] or {}).get(key)
                    value=(r[label+'_score'] or {}).get(key)
                    comparison[label+'_'+metric+'_gain_pct']=(100*(baseline-value)/baseline
                        if baseline is not None and value is not None and baseline>0 else None)
                baseline=(equal[label+'_score'] or {}).get('background_arc_cost')
                value=(r[label+'_score'] or {}).get('background_arc_cost')
                comparison[label+'_background_arc_increase_pct']=(100*(value-baseline)/baseline
                    if baseline is not None and value is not None and baseline>0 else None)
            if equal['motion'] is not None and r['motion'] is not None:
                comparison.update(time_difference_s=r['motion']['simulation_time_s']-equal['motion']['simulation_time_s'],
                    travel_difference_m=r['motion']['actual_trajectory_length_m']-equal['motion']['actual_trajectory_length_m'],
                    recovery_difference=r['replan_count']-equal['replan_count'])
            pair['comparisons'][method]=comparison
        pairs.append(pair)
    hotspots=[]
    for scene,seed in spec['units']:
        key=f'{scene}__seed_{seed}'
        condition=lock['conditions'][key+'__hotspot5']
        hotspots.append({'physical_key':key,'region':condition['region'],
            'people_indices':condition['hotspot_people_indices'],'density':condition['density']})
    flat=[]
    for r in rows:
        v={k:r[k] for k in ('task_id','physical_key','condition_key','demand','method','count','termination_status',
                            'stage','executed','success','phase_triggered','initial_paths_ok','offline_consistent')}
        v['phase_m']=(r.get('phase') or {}).get('selected_phase_m')
        for label in ('raw','final','actual'):
            s=r[label+'_score'] or {}
            for metric in ('demand','excess','background'):
                v[label+'_'+metric+'_cost_m2']=s.get('planned_'+metric+'_weighted_cost')
            v[label+'_background_arc_cost_m2']=s.get('background_arc_cost')
        geometry=r['original_geometry']
        for k in ('min_arc_gap_m','max_arc_gap_m','seam_arc_gap_m','min_euclidean_m','arc_box_ok','euclidean_ok'):
            v['raw_'+k]=geometry[k]
        v['raw_invalid_domain_targets']=json.dumps(r['original_domain']['invalid_target_indices'])
        for k in ('min_pair_m','min_domain_signed_m','min_arc_gap_m','max_arc_gap_m','seam_gap_m'):
            v['final_'+k]=(r.get('final_geometry') or {}).get(k)
        motion=r['motion'] or {}
        for k in ('simulation_time_s','actual_trajectory_length_m','interval_safety_ok','unsafe_interval_count',
                  'terminal_arrival_ok','final_max_target_error_m'):
            v[k]=motion.get(k)
        for k in ('crowd','guides','walls'):
            v['minimum_net_'+k+'_m']=motion.get('minimum_net_clearances_m',{}).get(k)
        v['recoveries']=r['replan_count']
        v.update({'planning_s':r['timing'].get('planning_s'),'phase_s':r['timing'].get('phase_s'),
                  'actual_max_gap_m':r['actual_max_gap'],'actual_mean_curve_distance_m':r['actual_mean_curve_distance']})
        flat.append(v)
    with (report/'tasks_36.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=list(flat[0]))
        writer.writeheader(); writer.writerows(flat)
    # Phase-free demand changes: same physical input and method, raw outputs only.
    changes=[]
    for scene,seed in spec['units']:
        key=f'{scene}__seed_{seed}'
        for method in spec['methods']:
            a=next(r for r in rows if r['physical_key']==key and r['method']==method and r['demand']=='ones')
            b=next(r for r in rows if r['physical_key']==key and r['method']==method and r['demand']=='hotspot5')
            sites=np.array(a['original_arc_coordinates']); other=np.array(b['original_arc_coordinates'])
            length=lock['conditions'][key+'__ones']['density']['curve_length_m']
            matching=np.min(np.abs((sites[:,None]-other[None,:]+length/2)%length-length/2),axis=1)
            changes.append({'physical_key':key,'method':method,'raw_symmetric_arc_hausdorff_m':float(max(matching.max(),
                np.min(np.abs((other[:,None]-sites[None,:]+length/2)%length-length/2),axis=1).max()))})
    summary={'planned_rows':36,'physical_inputs':6,'demand_units':12,
        'outcomes':dict(Counter(r['termination_status'] for r in rows)),
        'method_outcomes':{m:dict(Counter(r['termination_status'] for r in rows if r['method']==m)) for m in spec['methods']},
        'executed_rows':sum(r['executed'] for r in rows),'independent_rejudge_all_rows_passed':True,
        'phase_trigger_rows':[r['task_id'] for r in rows if r['phase_triggered']],
        'workers':sorted({r['worker']['pid'] for r in rows}),
        'counterfactual_values':sorted({r['worker']['priority_stall_counterfactual'] for r in rows}),
        'numerical_pool_threads':sorted({p['num_threads'] for r in rows for p in r['worker']['numerical_pools']}),
        'batch_wall_s':{s:read(root/(s+'_summary.json'))['wall_s'] for s in ('first','remaining')},
        'pairs':pairs,'demand_target_changes':changes,'rows':rows}
    write(report/'analysis.json',summary)
    write(report/'manifest_36.json',manifest)
    write(report/'inputs_6.json',lock['sources'])
    write(report/'demands_12.json',lock['conditions'])
    write(report/'offline_checks.json',offline_checks)
    write(report/'failure_evidence.json',failures)
    write(report/'geometry_guide_hashes.json',{'derivation':'computed from unchanged frozen NPZ arrays; file locks predate planning',
        'units':{k:{'guides_array_bytes_sha256':hashlib.sha256(np.ascontiguousarray(
            arrays(Path(v['source_cell'])/'input.npz')['guides']).tobytes()).hexdigest(),
            'boundary_npz_sha256':sha(root/'physical'/k/'boundary.npz'),
            'input_npz_sha256':sha(Path(v['source_cell'])/'input.npz'),
            'physical_array_sha256':v['physical_array_sha256']} for k,v in lock['sources'].items()}})
    source=Path('src/udc')
    write(report/'execution_source.json',{'provenance':lock['provenance'],
        'file_hashes':{p.as_posix():sha(p) for p in sorted(source.rglob('*.py'))},
        'protocol_sha256':sha(root/'protocol.json'),'config_sha256':sha('configs/step1/nonconvex_demand_pilot_v1.yaml')})
    write(report/'raw_checksums.json',{'run_files':{p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*')) if p.is_file()},
        'frozen_source_references':lock['files']})
    plot(root,report,lock,rows)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('pairs','rows','demand_target_changes')},indent=2))
    for p in pairs:
        print(p['condition_key'],json.dumps({'projected':p['comparisons']['projected_mass'],
            'raw_gain_retention':p['raw_demand_gain_retention']}))


def plot(root,report,lock,rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    fig,axes=plt.subplots(6,2,figsize=(11,18),layout='constrained')
    colors={'equal_arc':'#222222','mass_cvt':'#de7b18','projected_mass':'#1277b0'}
    markers={'equal_arc':'o','mass_cvt':'x','projected_mass':'+'}
    for i,(scene,seed) in enumerate(lock['spec']['units']):
        key=f'{scene}__seed_{seed}'
        b=load_boundary(root/'physical'/key)
        data=arrays(Path(lock['sources'][key]['source_cell'])/'input.npz')
        for j,demand in enumerate(('ones','hotspot5')):
            ax=axes[i,j]
            f=arrays(root/'conditions'/(key+'__'+demand)/'demand.npz')
            ax.plot(*np.vstack([b.deployment,b.deployment[0]]).T,color='#aaaaaa',lw=1)
            ax.scatter(*data['people'].T,c=f['demand'],cmap='Reds',vmin=0,vmax=5,s=17,edgecolor='#777777',linewidths=.3)
            if demand=='hotspot5':
                region=lock['spec']['hotspots'][scene]
                ax.add_patch(Circle(region['center'],region['radius'],fill=False,ec='#d33939',ls='--',lw=.8))
            for method in lock['spec']['methods']:
                row=next(r for r in rows if r['physical_key']==key and r['demand']==demand and r['method']==method)
                folder=root/'plans'/row['task_id']
                plan=load_plan(folder/('final' if (folder/'final').exists() else 'original'))
                label=method+(' [raw rejected]' if not row['success'] else '')
                ax.scatter(*plan.positions.T,marker=markers[method],c=colors[method],s=30,linewidths=.8,label=label)
            ax.set_title(f'{scene} seed{seed}, N={len(plan.positions)} | {demand}',fontsize=10)
            ax.set_aspect('equal'); ax.tick_params(labelsize=7)
            if i==0: ax.legend(fontsize=6,loc='upper right')
    fig.suptitle('Frozen demand pilot: final executable targets and rejected raw Mass CVT witnesses',fontsize=12)
    fig.savefig(report/'targets.png',dpi=130)
    fig.savefig(report/'targets.svg')
    svg=report/'targets.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default='runs/step1_nonconvex_demand_pilot_v1')
    p.add_argument('--report',default='reports/step1_nonconvex_demand_pilot_v1')
    args=p.parse_args()
    main(args.output,args.report)
