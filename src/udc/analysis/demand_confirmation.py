"""Prespecified paired estimands and stratified whole-physical-unit bootstrap."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from pathlib import Path
import platform
import sys

import numpy as np

from udc.experiments.demand_pilot import read, arrays
from udc.experiments.demand_confirmation import locked, SCENES, METHODS
from udc.experiments.nonconvex_rebuild import write, sha


def paired_units(rows,scenes=SCENES):
    """Keep A/B and both methods in each physical unit, including failures."""
    units=[]
    for scene in scenes:
        seeds=sorted({r['seed'] for r in rows if r['scene']==scene})
        for seed in seeds:
            members=[r for r in rows if r['scene']==scene and r['seed']==seed]
            if len(members)!=4: raise ValueError('A physical unit must retain two demands times two methods')
            units.append({'scene':scene,'seed':seed,'physical_key':members[0]['physical_key'],
                          'rows':{d:{m:next(r for r in members if r['demand']==d and r['method']==m)
                                   for m in METHODS} for d in ('ones','hotspot5')}})
    return units


def pair_values(unit,demand,metric='demand',source='planned_cost'):
    pair=unit['rows'][demand]
    if source=='planned_cost' and not all(pair[m].get('target_valid') for m in METHODS): return None
    values=[(pair[m].get(source) or {}).get(metric) for m in METHODS]
    if any(v is None for v in values): return None
    equal,projected=values
    return {'equal':equal,'projected':projected,'difference':projected-equal,
            'relative_pct':100*(projected-equal)/equal}


def contrast(units,demand,metric='demand',source='planned_cost'):
    by_scene={}; pairs=[]
    for scene in SCENES:
        selected=[]
        for u in units:
            if u['scene']!=scene: continue
            value=pair_values(u,demand,metric,source)
            if value is not None: selected.append({**value,'scene':scene,'seed':u['seed'],'physical_key':u['physical_key']})
        pairs+=selected
        deltas=[v['difference'] for v in selected]
        by_scene[scene]={'n_pairs':len(selected),'mean_difference':float(np.mean(deltas)) if deltas else None,
            'mean_relative_pct':float(np.mean([v['relative_pct'] for v in selected])) if selected else None,
            'projected_lower_fraction':float(np.mean(np.array(deltas)<0)) if deltas else None,
            'worst_pair':max(selected,key=lambda v:v['difference']) if selected else None}
    computable=all(v['mean_difference'] is not None for v in by_scene.values())
    return {'demand':demand,'metric':metric,'source':source,'n_pairs':len(pairs),'by_scene':by_scene,'pairs':pairs,
        'equal_scene_mean_difference':float(np.mean([v['mean_difference'] for v in by_scene.values()])) if computable else None,
        'equal_scene_mean_relative_pct':float(np.mean([v['mean_relative_pct'] for v in by_scene.values()])) if computable else None,
        'worst_pair':max(pairs,key=lambda p:p['difference']) if pairs else None,
        'conditional_on':'both qualified final targets; no requirement for path or execution success' if source=='planned_cost' else
            'both have actual motion evidence; failed final deployments remain labelled'}


def stratified_bootstrap(units,settings,complete=True):
    """Draw whole physical-unit indices per scene; missing pairs stay missing."""
    result={'repetitions':settings['bootstrap_repetitions'],'rng_seed':settings['bootstrap_rng_seed'],
        'interval':None,'reason':None,'uncalculable':0,'physical_units_resampled':True,
        'A_B_methods_states_share_each_sampled_index':True}
    if not complete: result['reason']='incomplete or interrupted confirmation'; return result
    groups=[[u for u in units if u['scene']==s] for s in SCENES]
    values=[np.array([pair_values(u,'hotspot5')['difference'] if pair_values(u,'hotspot5') is not None else np.nan for u in group])
            for group in groups]
    if any(np.isfinite(v).sum()<settings['minimum_valid_pairs_per_scene_for_interval'] for v in values):
        result['reason']='too few qualified physical-unit pairs in at least one scene'; return result
    rng=np.random.default_rng(settings['bootstrap_rng_seed']); scene_means=[]
    for group,v in zip(groups,values):
        indices=rng.integers(0,len(group),size=(settings['bootstrap_repetitions'],len(group)))
        samples=v[indices]  # The indices address complete unit dictionaries, not task rows.
        counts=np.isfinite(samples).sum(axis=1)
        means=np.divide(np.nansum(samples,axis=1),counts,out=np.full(len(counts),np.nan),where=counts>0)
        scene_means.append(means)
    distribution=np.mean(np.array(scene_means),axis=0)
    valid=np.isfinite(distribution); result['uncalculable']=int((~valid).sum())
    if (~valid).mean()>settings['maximum_uncalculable_bootstrap_share']:
        result['reason']='too many resamples lack a computable stratum'; return result
    alpha=(1-settings['confidence'])/2
    result['interval']=[float(v) for v in np.quantile(distribution[valid],[alpha,1-alpha])]
    result['valid_repetitions']=int(valid.sum())
    return result


def success_pairs(units,demand):
    def table(selected):
        counts={'both_success':0,'equal_only':0,'projected_only':0,'both_fail':0}
        for u in selected:
            pair=u['rows'][demand]; e,p=[bool(pair[m]['success']) for m in METHODS]
            counts['both_success' if e and p else 'equal_only' if e else 'projected_only' if p else 'both_fail']+=1
        return {'n_physical_units':len(selected),**counts}
    return {'all':table(units),'by_scene':{s:table([u for u in units if u['scene']==s]) for s in SCENES}}


def motion_pairs(units,demand):
    pairs=[]
    for u in units:
        rows=u['rows'][demand]
        if not all(rows[m]['success'] and rows[m].get('motion') is not None for m in METHODS): continue
        e,p=[rows[m] for m in METHODS]
        pairs.append({'physical_key':u['physical_key'],'scene':u['scene'],'seed':u['seed'],
            'time_difference_s':p['motion']['simulation_time_s']-e['motion']['simulation_time_s'],
            'travel_difference_m':p['motion']['actual_trajectory_length_m']-e['motion']['actual_trajectory_length_m'],
            'recovery_difference':p['recoveries']-e['recoveries']})
    return {'n_both_success':len(pairs),'pairs':pairs,'by_scene':{s:{'n':len([p for p in pairs if p['scene']==s]),
        **{k:float(np.mean([p[k] for p in pairs if p['scene']==s])) if any(p['scene']==s for p in pairs) else None
           for k in ('time_difference_s','travel_difference_m','recovery_difference')}} for s in SCENES}}


def csv_write(path,rows):
    if not rows: return
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def summarize(output,report):
    root=Path(output).resolve(); report=Path(report).resolve(); lock=locked(root,inputs=True)
    report.mkdir(parents=True,exist_ok=True)
    frozen=read(root/'inputs_frozen.json'); rows=[]
    for item in read(root/'manifest_240.json')['rows']:
        path=root/'results'/item['task_id']/'protocol_metrics.json'
        row=read(path) if path.exists() else {**item,'status':'NOT_STARTED','success':False,'stage':'not_started',
            'target_valid':False,'planned_cost':None,'fine_cost':None,'actual_cost':None,'motion':None,
            'executed':False,'recoveries':None,'timing':{},'phase':None}
        rows.append(row)
    units=paired_units(rows); complete=all(r['status']!='NOT_STARTED' for r in rows)
    primary=contrast(units,'hotspot5')
    primary['bootstrap']=stratified_bootstrap(units,lock['spec']['statistics'],complete=complete)
    secondary={d:{metric:contrast(units,d,metric) for metric in ('demand','excess','background_mass','background_arc')}
               for d in ('ones','hotspot5')}
    actual={d:{metric:contrast(units,d,metric,'actual_cost') for metric in ('demand','excess','background_mass','background_arc')}
            for d in ('ones','hotspot5')}
    sensitivity=[]
    for u in units:
        for d in ('ones','hotspot5'):
            pair=u['rows'][d]; main=pair_values(u,d)
            if main is None: continue
            fine=[(pair[m].get('fine_cost') or {}).get('demand') for m in METHODS]
            if any(v is None for v in fine): continue
            diff=fine[1]-fine[0]
            sensitivity.append({'physical_key':u['physical_key'],'demand':d,'delta_2048':main['difference'],
                'delta_8192':diff,'sign_changed':bool(np.sign(diff)!=np.sign(main['difference'])),
                'absolute_change':abs(diff-main['difference'])})
    flat=[]
    for r in rows:
        v={k:r.get(k) for k in ('task_id','physical_key','condition_key','scene','seed','demand','method','method_label',
            'count','input_status','input_sha256','density_sha256','status','stage','success','target_valid','initial_paths_ok','executed',
            'curve_sha256','domain_sha256','offline_consistent','recoveries','wall_s','actual_off_curve','failure_reason')}
        for prefix in ('planned','fine','actual'):
            for metric in ('demand','excess','background_mass','background_arc'):
                v[prefix+'_'+metric]=(r.get(prefix+'_cost') or {}).get(metric)
        motion=r.get('motion') or {}
        for key in ('simulation_time_s','actual_trajectory_length_m','interval_safety_ok','unsafe_interval_count','terminal_arrival_ok'):
            v[key]=motion.get(key)
        for key in ('crowd','guides','walls'): v['minimum_net_'+key+'_m']=motion.get('minimum_net_clearances_m',{}).get(key)
        for key in ('original_layout_s','all_phase_s','initial_path_s','runner_s','fine_evaluation_s','independent_rejudge_s'):
            v[key]=r['timing'].get(key)
        v['phase_m']=(r.get('phase') or {}).get('relative_phase_m')
        v['phase_accepted']=(r.get('phase') or {}).get('accepted')
        flat.append(v)
    legal=sum(s['people_quality']['accepted'] for s in frozen['sources'].values())
    executed=[r for r in rows if r.get('motion') is not None]
    summary={'protocol':lock['spec'],'source':lock['provenance'],'all_rows':len(rows),'physical_units':len(units),
        'common_people_room_legal_units':legal,'input_statuses':dict(Counter(s['status'] for s in frozen['sources'].values())),
        'outcomes':dict(Counter(r['status'] for r in rows)),
        'method_outcomes':{m:dict(Counter(r['status'] for r in rows if r['method']==m)) for m in METHODS},
        'stage_outcomes':dict(Counter(r['stage']+'::'+r['status'] for r in rows)),
        'complete':complete,'executed_rows':len(executed),'success_pairs':{d:success_pairs(units,d) for d in ('ones','hotspot5')},
        'primary':primary,'secondary':secondary,'actual':actual,
        'motion_pairs':{d:motion_pairs(units,d) for d in ('ones','hotspot5')},
        'fine_sign_changes':sum(r['sign_changed'] for r in sensitivity),
        'offline_inconsistent':sum(r.get('offline_consistent') is False for r in rows),
        'executed_criteria_failures':[{k:r[k] for k in ('task_id','status','failure_reason')} for r in executed if not r['success']],
        'safety_minima':{k:min(r['motion']['minimum_net_clearances_m'][k] for r in executed) if executed else None
                         for k in ('crowd','guides','walls')},
        'numerical_threads':sorted({p['num_threads'] for r in rows for p in r.get('worker',{}).get('numeric_pools',[])}),
        'counterfactual_values':sorted({r.get('worker',{}).get('counterfactual') for r in rows if 'worker' in r}),
        'compute_by_method':{m:{key:{'n':len(values),'sum':float(np.sum(values)),'mean':float(np.mean(values)) if values else None,
                'maximum':float(np.max(values)) if values else None} for key in ('original_layout_s','all_phase_s','runner_s')
                for values in [[r['timing'][key] for r in rows if r['method']==m and key in r['timing']]]} for m in METHODS}}
    csv_write(report/'tasks_240.csv',flat)
    csv_write(report/'primary_hotspot_pairs.csv',primary['pairs'])
    csv_write(report/'fine_integration_pairs.csv',sensitivity)
    write(report/'analysis.json',summary)
    write(report/'manifest_240.json',read(root/'manifest_240.json'))
    write(report/'input_map_60.json',frozen['sources'])
    write(report/'demand_map_120.json',frozen['conditions'])
    write(report/'seed_audit.json',read(root/'seed_audit.json'))
    if (root/'all_independent_checks.json').exists():
        final_checks=read(root/'all_independent_checks.json')
        summary['offline_final_inconsistent']=sum(c.get('criteria_consistent') is False or c.get('diagnostics_consistent') is False
            or c.get('success_consistent') is False for c in final_checks)
        write(report/'independent_checks_240.json',final_checks)
        write(report/'analysis.json',summary)
    write(report/'execution_source.json',{'provenance':lock['provenance'],'python':sys.version,'executable':sys.executable,
        'platform':platform.platform(),'protocol_sha256':sha(root/'protocol.json'),'inputs_frozen_sha256':sha(root/'inputs_frozen.json'),
        'source_files':{p.as_posix():sha(p) for p in sorted(Path('src/udc').rglob('*.py'))}})
    locked(root,inputs=True)
    write(report/'raw_checksums.json',{'run_files':{p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*')) if p.is_file()},
        'protected_baseline':lock['protected_files']})
    print({k:summary[k] for k in ('all_rows','physical_units','common_people_room_legal_units','outcomes','executed_rows','fine_sign_changes','offline_inconsistent')})
    print({'B_primary_delta_m2':primary['equal_scene_mean_difference'],'bootstrap':primary['bootstrap']})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default='runs/step1_nonconvex_demand_confirmation_v1')
    p.add_argument('--report',default='reports/step1_nonconvex_demand_confirmation_v1')
    a=p.parse_args(); summarize(a.output,a.report)


if __name__=='__main__': main()
