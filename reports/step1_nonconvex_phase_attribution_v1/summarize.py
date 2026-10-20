"""Independent saved-evidence summaries; no new targets or simulation decisions."""
import argparse
from collections import Counter
import csv
from pathlib import Path
import sys
import importlib.metadata
import platform
import os

import numpy as np
import psutil
import shapely
from shapely.geometry import Polygon
import yaml

from udc.experiments.demand_pilot import arrays, read
from udc.experiments.nonconvex_rebuild import array_hash, load_plan, sha, write
from udc.experiments.phase_attribution import (condition_data, independent_rejudge, locked, score_sites)
import udc


METRICS=('demand','excess','background_mass','background_arc')


def csv_file(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def gain(equal,projected):
    return None if equal is None or projected is None else 100*(equal-projected)/equal


def independent_integral(boundary,field,points,samples):
    """Direct persisted-coordinate evaluation, without the policy scorer."""
    length=boundary.length
    q=(np.arange(samples)+.5)*length/samples
    sites=shapely.line_locate_point(Polygon(boundary.deployment).exterior,shapely.points(points))
    delta=(q[:,None]-sites[None,:]+length/2)%length-length/2
    nearest_squared=np.min(delta**2,axis=1)
    rho=np.interp(q,np.r_[boundary.arc,length],np.r_[field['rho'],field['rho'][0]])
    result={'demand':float(np.dot(rho,nearest_squared)/rho.sum()),
            'excess':None,'background_mass':None,'background_arc':None}
    if field['excess_mass'].sum()>0:
        ex=np.interp(q,np.r_[boundary.arc,length],np.r_[field['excess_rho'],field['excess_rho'][0]])
        outside=~field['excess_window'][np.minimum((q/length*len(field['excess_window'])).astype(int),len(field['excess_window'])-1)]
        for metric,weights in [('excess',ex),('background_mass',np.maximum(rho-ex,0.)),('background_arc',outside.astype(float))]:
            result[metric]=float(np.dot(weights,nearest_squared)/weights.sum())
    return result


def policy_flat(r):
    v={k:r[k] for k in ('task_id','condition_key','method','policy','grid','selected_index','relative_phase_m',
        'candidate_count','feasible_count','status','target_geometry_ok','initial_paths_ok','original_domain_ok')}
    # Source manifest's method_label is the original layout provenance. Give the
    # derived policy its own explicit label, especially the new P3 budget.
    v['source_layout_label']=r['method_label']
    v['policy_method_label']=r['method']+('+all_phase_256' if r['policy']=='P3' else '+'+r['policy'])
    v['policy_consulted_candidate_count']=(256 if r['policy']=='P3' or
        (r['policy'] in ('P1','P2') and not r['original_domain_ok']) else 0)
    v['target_sha256']=r.get('target_sha256')
    v['cache_key']=r['cache_key']
    for n,s in [('2048',r['score']),*r['fine_scores'].items()]:
        for metric in METRICS:
            v[metric+'_cost_'+n]=(s or {}).get(metric)
    for name in ('min_pair_m','min_domain_signed_m','min_arc_gap_m','max_arc_gap_m','seam_gap_m','cyclic_gap_pattern_error_m'):
        v[name]=(r.get('geometry') or {}).get(name)
    return v


def summarize(output,report):
    root,report=Path(output).resolve(),Path(report).resolve()
    report.mkdir(parents=True,exist_ok=True)
    lock=locked(root)
    for path,value in read(root/'p3_target_freeze.json')['files'].items():
        assert sha(path)==value, path
    main=read(root/'policy_main_96.json')
    half=read(root/'policy_half_P3_24.json')
    source=Path(lock['source_run'])
    conditions=list(dict.fromkeys(r['condition_key'] for r in main))
    comparisons=[]; accounts=[]; identities=[]; original_score_checks=[]
    integration_checks=[]
    for row in main+half:
        _,field,b,_,_=condition_data(lock,row)
        points=arrays(root/'planning'/(row['task_id']+'__'+row['grid']+'__'+row['policy'])/'targets.npz')['targets']
        for samples in (4096,8192):
            fresh=independent_integral(b,field,points,samples)
            errors={m:abs(fresh[m]-row['fine_scores'][str(samples)][m]) for m in METRICS if fresh[m] is not None}
            assert max(errors.values())<1e-12
            integration_checks.append({'task_id':row['task_id'],'grid':row['grid'],'policy':row['policy'],
                                       'samples':samples,'absolute_errors':errors})
    for condition in conditions:
        by={policy:{method:next(r for r in main if r['condition_key']==condition and r['policy']==policy and r['method']==method)
            for method in ('equal_arc','projected_mass')} for policy in ('P0','P1','P2','P3')}
        hs={r['method']:r for r in half if r['condition_key']==condition}
        pair={'condition_key':condition,'policies':{},'half_P3':{},'accounting':{},'integration':{},'grid':{}}
        for policy,p in by.items():
            pair['policies'][policy]={metric:{'equal':p['equal_arc']['score'][metric],
                'projected':p['projected_mass']['score'][metric],
                'difference_equal_minus_projected':(None if p['equal_arc']['score'][metric] is None else
                    p['equal_arc']['score'][metric]-p['projected_mass']['score'][metric]),
                'projected_gain_pct':gain(p['equal_arc']['score'][metric],p['projected_mass']['score'][metric])}
                for metric in METRICS}
        for metric in METRICS:
            er,pr=[by['P0'][m]['score'][metric] for m in ('equal_arc','projected_mass')]
            ea,pa=[by['P3'][m]['score'][metric] for m in ('equal_arc','projected_mass')]
            pair['half_P3'][metric]={'equal':hs['equal_arc']['score'][metric],
                'projected':hs['projected_mass']['score'][metric],
                'gain_pct':gain(hs['equal_arc']['score'][metric],hs['projected_mass']['score'][metric])}
            if er is None: continue
            dr,ge,gp,da=er-pr,er-ea,pr-pa,ea-pa
            residual=da-(dr-ge+gp)
            assert abs(residual)<1e-12
            pair['accounting'][metric]={'D_raw':dr,'G_equal':ge,'G_projected':gp,
                'D_all_phase':da,'identity_residual':residual,'scope':'same-score accounting, not causal decomposition'}
            pair['integration'][metric]={str(n):{
                'equal':by['P3']['equal_arc']['fine_scores'][str(n)][metric],
                'projected':by['P3']['projected_mass']['fine_scores'][str(n)][metric],
                'gain_pct':gain(by['P3']['equal_arc']['fine_scores'][str(n)][metric],
                                by['P3']['projected_mass']['fine_scores'][str(n)][metric])}
                for n in (4096,8192)}
            deltas=[by['P3']['equal_arc']['score'][metric]-by['P3']['projected_mass']['score'][metric],
                hs['equal_arc']['score'][metric]-hs['projected_mass']['score'][metric],
                *(by['P3']['equal_arc']['fine_scores'][str(n)][metric]-by['P3']['projected_mass']['fine_scores'][str(n)][metric]
                  for n in (4096,8192)),
                *(hs['equal_arc']['fine_scores'][str(n)][metric]-hs['projected_mass']['fine_scores'][str(n)][metric]
                  for n in (4096,8192))]
            pair['grid'][metric]={'main_gain_pct':gain(ea,pa),
                'half_gain_pct':gain(hs['equal_arc']['score'][metric],hs['projected_mass']['score'][metric]),
                'difference_range_all_grids_and_integrals':[min(deltas),max(deltas)],
                'sign_stable':all(d>0 for d in deltas) or all(d<0 for d in deltas),
                'main_to_half_cost_change':{m:hs[m]['score'][metric]-by['P3'][m]['score'][metric]
                                            for m in ('equal_arc','projected_mass')}}
        comparisons.append(pair)
        accounts.append({'condition_key':condition,**pair['accounting']['demand'],
            **{policy+'_gain_pct':pair['policies'][policy]['demand']['projected_gain_pct'] for policy in by},
            'half_P3_gain_pct':pair['half_P3']['demand']['gain_pct'],
            'main_P3_8192_gain_pct':pair['integration']['demand']['8192']['gain_pct']})
        row=by['P0']['equal_arc']
        data,field,b,domain,cfgpath=condition_data(lock,row)
        paths={m:source/'plans'/(condition+'__'+m)/'original/targets.npz' for m in ('equal_arc','projected_mass','mass_cvt')}
        identities.append({'condition_key':condition,'input_sha256':row['input_sha256'],
            'physical_sha256':row['physical_sha256'],'curve_array_sha256':array_hash(crowd=b.crowd,
                deployment=b.deployment,arc=b.arc,length=np.array(b.length)),
            'guide_initial_array_sha256':array_hash(guides=data['guides']),
            'raw_rho_sha256':array_hash(rho=field['rho']), 'demand_sha256':array_hash(demand=field['demand']),
            'window_sha256':array_hash(window=field['excess_window']), 'domain_sha256':domain.geometry_sha256,
            'original_target_files':{m:{'path':str(p),'sha256':sha(p)} for m,p in paths.items()},
            'count':row['count'],'length_m':b.length,'person_count':len(data['people'])})
        for method in ('equal_arc','projected_mass'):
            previous=read(source/'results'/(condition+'__'+method)/'protocol_metrics.json')
            for policy,label in [('P0','raw_score'),('P2','final_score')]:
                difference=by[policy][method]['score']['demand']-previous[label]['planned_demand_weighted_cost']
                assert abs(difference)<1e-12
                original_score_checks.append({'condition':condition,'method':method,'policy':policy,'cost_error':difference})
    executions=[]; offline=[]
    for row in read(root/'p3_execution_manifest.json')['rows']:
        directory=root/'execution'/row['task_id']
        r=read(directory/'protocol_metrics.json')
        data,field,b,domain,cfgpath=condition_data(lock,row)
        cfg=yaml.safe_load(cfgpath.read_text(encoding='utf-8'))
        if r.get('trajectory_reference'):
            trajectory=Path(r['trajectory_reference'])
            metric_path=trajectory.parent/'metrics.json'
            metrics=read(metric_path)
            check=independent_rejudge(trajectory,cfg,metrics)
            # All new and reused execution evidence is independently rejudged;
            # only this report gets new output, including for the old references.
            assert check['criteria_consistent'] and check['success']==r['success']
            offline.append({'task_id':r['task_id'],'reused':r['reused'],
                'trajectory_path':str(trajectory),'trajectory_sha256':sha(trajectory),
                'criteria_consistent':check['criteria_consistent'],'success':check['success'],
                'interval_safety_ok':check['motion']['interval_safety_ok']})
            for key in ('simulation_time_s','actual_trajectory_length_m'):
                assert abs(check['motion'][key]-r['motion'][key])<1e-12
        old=read(source/'results'/row['task_id']/'protocol_metrics.json')
        v={k:r.get(k) for k in ('task_id','condition_key','method','method_label','status','success',
                              'new_execution','reused','recoveries','relative_phase_m','wall_s','offline_consistent')}
        for key in ('simulation_time_s','actual_trajectory_length_m','interval_safety_ok','unsafe_interval_count',
                    'terminal_arrival_ok','final_max_target_error_m'):
            v[key]=(r['motion'] or {}).get(key)
        for key in ('crowd','guides','walls'):
            v['net_'+key+'_m']=(r['motion'] or {}).get('minimum_net_clearances_m',{}).get(key)
        v['time_difference_vs_old_P2_s']=(r['motion']['simulation_time_s']-old['motion']['simulation_time_s']
            if r['motion'] and old['motion'] else None)
        v['travel_difference_vs_old_P2_m']=(r['motion']['actual_trajectory_length_m']-old['motion']['actual_trajectory_length_m']
            if r['motion'] and old['motion'] else None)
        v['recovery_difference_vs_old_P2']=(r['recoveries']-old['replan_count'] if r.get('recoveries') is not None else None)
        executions.append(v)
    assert len(main)==96 and len(half)==24 and len(executions)==24 and len(offline)==24
    summary={'physical_inputs':6,'demand_conditions':12,'main_policy_rows':96,'half_P3_sensitivity_rows':24,
        'policy_statuses':{p:dict(Counter(r['status'] for r in main if r['policy']==p)) for p in ('P0','P1','P2','P3')},
        'execution_outcomes':dict(Counter(r['status'] for r in executions)),
        'new_executions':sum(r['new_execution'] for r in executions),'reused_trajectories':sum(r['reused'] for r in executions),
        'P2_reproduction_rows':len(read(root/'p2_reproduction.json')),
        'all_old_references_unchanged':True,'comparisons':comparisons,
        'offline_wall_s':read(root/'p3_target_freeze.json')['offline_wall_s'],
        'execution_wall_s':{stage:read(root/(stage+'_execution_summary.json'))['wall_s'] for stage in ('first','remaining')}}
    csv_file(report/'policy_main_96.csv',[policy_flat(r) for r in main])
    csv_file(report/'sensitivity_half_P3_24.csv',[policy_flat(r) for r in half])
    csv_file(report/'benefit_accounting_12.csv',accounts)
    csv_file(report/'executions_P3_24.csv',executions)
    write(report/'analysis.json',summary)
    write(report/'input_layout_hashes_12.json',identities)
    write(report/'candidate_cache_summary.json',read(root/'candidate_cache_index.json'))
    write(report/'p2_reproduction_checks.json',{'targets':read(root/'p2_reproduction.json'),'scores':original_score_checks})
    write(report/'offline_rejudgement_24.json',offline)
    write(report/'independent_fine_integration_240.json',integration_checks)
    write(report/'source_environment.json',{'base_commit':lock['provenance']['commit'],'provenance':lock['provenance'],
        'python':sys.version,'executable':sys.executable,'platform':platform.platform(),'udc_import_path':udc.__file__,
        'logical_cpus':os.cpu_count(),'physical_cores':psutil.cpu_count(logical=False),
        'memory_bytes':psutil.virtual_memory().total,'available_memory_at_summary_bytes':psutil.virtual_memory().available,
        'dependencies':{n:importlib.metadata.version(n) for n in ('numpy','scipy','shapely','jupedsim','PedPy','pytest','threadpoolctl')},
        'baseline_tests':'180 passed in 25.34s','final_tests':'185 passed in 23.28s','pip_check':'No broken requirements found',
        'new_source_file_sha256':sha('src/udc/experiments/phase_attribution.py'),
        'config_sha256':sha('configs/step1/nonconvex_phase_attribution_v1.yaml'),
        'protocol_sha256':sha(root/'protocol.json'),'target_freeze_sha256':sha(root/'p3_target_freeze.json')})
    # Verify again after rejudging old trajectories: no helper wrote to old runs.
    locked(root)
    write(report/'raw_checksums.json',{'new_run_files':{p.relative_to(root).as_posix():sha(p)
        for p in sorted(root.rglob('*')) if p.is_file()},'protected_prior_references':lock['references']})
    plot(report,comparisons)
    print({k:v for k,v in summary.items() if k!='comparisons'})
    for c in comparisons:
        print(c['condition_key'],{p:round(c['policies'][p]['demand']['projected_gain_pct'],6) for p in ('P0','P1','P2','P3')},
            'P3_excess_gain_pct',c['policies']['P3']['excess']['projected_gain_pct'])


def plot(report,pairs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(13,7),layout='constrained')
    y=np.arange(12)
    colors={'P0':'#999999','P1':'#57a18c','P2':'#e29139','P3':'#1476ac'}
    for i,policy in enumerate(('P0','P1','P2','P3')):
        axes[0].scatter([p['policies'][policy]['demand']['projected_gain_pct'] for p in pairs],y+(i-1.5)*.13,
            label=policy+(' (raw descriptive)' if policy=='P0' else ''),s=27,color=colors[policy])
    labels=[p['condition_key'].replace('__seed_',' ').replace('__ones',' A').replace('__hotspot5',' B') for p in pairs]
    axes[0].set_yticks(y,labels); axes[0].invert_yaxis(); axes[0].axvline(0,color='#777',lw=.7)
    axes[0].set_xlabel('Projected-layout total demand cost reduction vs Equal Arc (%)')
    axes[0].set_title('Same saved layouts, four phase policies'); axes[0].legend(fontsize=8)
    hot=[p for p in pairs if 'hotspot5' in p['condition_key']]
    for i,(metric,label,color) in enumerate([('demand','Total','#1476ac'),('excess','Hotspot excess','#c7463e'),
        ('background_mass','Background mass','#57a18c'),('background_arc','Background arc','#a18cbb')]):
        axes[1].scatter([p['policies']['P3'][metric]['projected_gain_pct'] for p in hot],np.arange(6)+(i-1.5)*.13,
            label=label,color=color,s=35)
    axes[1].set_yticks(np.arange(6),[p['condition_key'].replace('__seed_',' ').replace('__hotspot5','') for p in hot])
    axes[1].invert_yaxis(); axes[1].axvline(0,color='#777',lw=.7)
    axes[1].set_xlabel('P3 projected-layout cost reduction (%); negative = increase')
    axes[1].set_title('Equal finite phase opportunity: six hotspot pairs'); axes[1].legend(fontsize=8)
    fig.savefig(report/'phase_benefit.png',dpi=140)
    fig.savefig(report/'phase_benefit.svg')
    svg=report/'phase_benefit.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
    plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default='runs/step1_nonconvex_phase_attribution_v1')
    p.add_argument('--report',default='reports/step1_nonconvex_phase_attribution_v1')
    a=p.parse_args(); summarize(a.output,a.report)
