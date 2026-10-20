"""Resource and layout effects, retaining full denominators and failed motion."""
from __future__ import annotations
import argparse
from collections import Counter
from pathlib import Path
import numpy as np

from udc.analysis.demand_confirmation import csv_write
from udc.experiments.demand_pilot import read
from udc.experiments.nonconvex_rebuild import sha, write
from udc.experiments.resource_response import locked, OUTPUT

SCENES=['elongated','tight_u','wide_u']; METHODS=['equal_arc','projected_mass']
METRICS=['demand','excess','background_mass','background_arc']


def units(rows):
    result=[]
    for scene in SCENES:
        for seed in sorted({r['seed'] for r in rows if r['scene']==scene}):
            members=[r for r in rows if r['scene']==scene and r['seed']==seed]
            if len(members)!=6: raise ValueError('Keep all three N and both methods per physical unit')
            result.append({'scene':scene,'seed':seed,'physical_key':members[0]['physical_key'],
                'rows':{n:{m:next(r for r in members if r['increment']==n and r['method']==m) for m in METHODS} for n in (0,2,4)}})
    return result


def target_pair(unit,n):
    return all(unit['rows'][n][m].get('target_valid') for m in METHODS)


def common_target(unit):
    return all(target_pair(unit,n) for n in (0,2,4))


def pairs(all_units,n,metric='demand',common=False,source='planned_cost'):
    rows=[]
    for u in all_units:
        if not target_pair(u,n) or (common and not common_target(u)): continue
        e,p=[u['rows'][n][m] for m in METHODS]
        values=[(r.get(source) or {}).get(metric) for r in (e,p)]
        if any(v is None for v in values): continue
        x,y=values; delta=y-x
        rows.append({'physical_key':u['physical_key'],'scene':u['scene'],'seed':u['seed'],'increment':n,
            'count':e['count'],'metric':metric,'equal':x,'projected':y,'difference':delta,
            'relative_pct':100*delta/x,'equal_success':e['success'],'projected_success':p['success'],
            'direction':'improved' if delta< -1e-12 else 'worsened' if delta>1e-12 else 'tied'})
    return rows


def contrast(rows):
    def group(selected):
        return {'n':len(selected),'mean_difference':float(np.mean([r['difference'] for r in selected])) if selected else None,
            'mean_relative_pct':float(np.mean([r['relative_pct'] for r in selected])) if selected else None,
            'directions':dict(Counter(r.get('direction','') for r in selected)),
            'worst':max(selected,key=lambda r:r['difference']) if selected else None}
    by={s:group([r for r in rows if r['scene']==s]) for s in SCENES}
    return {'all':group(rows),'by_scene':by,'equal_scene_mean_difference':float(np.mean([v['mean_difference'] for v in by.values()]))
        if all(v['n'] for v in by.values()) else None,
        'equal_scene_mean_relative_pct':float(np.mean([v['mean_relative_pct'] for v in by.values()])) if all(v['n'] for v in by.values()) else None}


def interactions(all_units,n,common=False):
    result=[]
    for u in all_units:
        if not (target_pair(u,0) and target_pair(u,n)) or (common and not common_target(u)): continue
        d={k:u['rows'][k]['projected_mass']['planned_cost']['demand']-u['rows'][k]['equal_arc']['planned_cost']['demand'] for k in (0,n)}
        result.append({'physical_key':u['physical_key'],'scene':u['scene'],'seed':u['seed'],'increment':n,
            'baseline_delta':d[0],'new_delta':d[n],'difference':d[n]-d[0],
            'relative_pct':100*(d[n]-d[0])/abs(d[0]),'direction':'gap_shrinks' if d[n]>d[0] else 'gap_expands'})
    return result


def bootstrap(all_units,settings):
    """One sampled physical index carries every N, method and failure state."""
    rng=np.random.default_rng(settings['bootstrap_rng_seed']); reps=settings['bootstrap_repetitions']
    # Columns are own-N deltas and paired difference-in-differences, including NaN failures.
    distributions=[]
    for scene in SCENES:
        group=[u for u in all_units if u['scene']==scene]; values=[]
        for u in group:
            ds=[u['rows'][n]['projected_mass']['planned_cost']['demand']-u['rows'][n]['equal_arc']['planned_cost']['demand']
                if target_pair(u,n) else np.nan for n in (0,2,4)]
            values.append(ds+[ds[1]-ds[0],ds[2]-ds[0]])
        values=np.array(values); indices=rng.integers(0,len(group),size=(reps,len(group)))
        sample=values[indices]; count=np.isfinite(sample).sum(axis=1)
        distributions.append(np.divide(np.nansum(sample,axis=1),count,out=np.full((reps,5),np.nan),where=count>0))
    distribution=np.mean(distributions,axis=0); output={}
    for i,name in enumerate(('delta_N0','delta_N2','delta_N4','interaction_N2','interaction_N4')):
        finite=np.isfinite(distribution[:,i]); output[name]={'uncalculable':int((~finite).sum()),
            'interval':np.quantile(distribution[finite,i],[.025,.975]).tolist() if finite.mean()>=.99 else None}
    return {'rng_seed':settings['bootstrap_rng_seed'],'repetitions':reps,'whole_physical_unit':True,'estimates':output}


def summarize(output,report):
    root=Path(output).resolve(); report=Path(report); report.mkdir(parents=True,exist_ok=True)
    lock=locked(root,full=True); frozen=read(root/'inputs_frozen.json'); manifest=read(root/'manifest_360.json')
    rows=[read(root/'results'/r['task_id']/'protocol_metrics.json') for r in manifest['rows']]
    us=units(rows); contrasts={}; pair_rows=[]; interaction_rows=[]
    for n in (0,2,4):
        for common in (False,True):
            for metric in METRICS:
                ps=pairs(us,n,metric,common); key=f'{n}_{metric}_'+('common' if common else 'own')
                contrasts[key]=contrast(ps)
                if not common: pair_rows+=ps
    interaction={}
    for n in (2,4):
        for common in (False,True):
            ps=interactions(us,n,common); interaction[f'{n}_'+('common' if common else 'available')]=contrast(ps)
            if common: interaction_rows+=ps
    flat=[]
    for r in rows:
        init=frozen['starts'][r['initial_key']]; motion=r.get('motion') or {}; timing=r['timing']
        v={k:r.get(k) for k in ('task_id','physical_key','scene','seed','count','increment','method','mode','input_status',
            'status','stage','success','target_valid','initial_paths_ok','executed','recoveries','failure_reason','offline_consistent','input_sha256','density_sha256','curve_sha256','domain_sha256')}
        v.update({k:init['quantity'].get(k) for k in ('curve_length_m','average_gap_m','planning_average_slack_m','planning_lower','acceptance_lower','necessary_upper')})
        v['initialization_accepted']=init['initialization_accepted']
        for prefix in ('planned','fine','actual'):
            for metric in METRICS: v[prefix+'_'+metric]=(r.get(prefix+'_cost') or {}).get(metric)
        for k in ('simulation_time_s','actual_trajectory_length_m','unsafe_interval_count','interval_safety_ok'): v[k]=motion.get(k)
        v['successful_deployment_time_s']=motion.get('simulation_time_s') if r['success'] else None
        v['cutoff_margin_s']=40-motion['simulation_time_s'] if r['success'] and motion else None
        v['per_guide_travel_m']=motion['actual_trajectory_length_m']/r['count'] if motion else None
        for k in ('crowd','guides','walls'): v['minimum_net_'+k+'_m']=motion.get('minimum_net_clearances_m',{}).get(k)
        for k in ('original_layout_s','all_phase_s','fine_evaluation_s','initial_path_s','runner_s','independent_rejudge_s'): v[k]=timing.get(k)
        for k in ('min_pair_m','min_domain_signed_m','min_arc_gap_m','max_arc_gap_m'): v[k]=(r.get('target_geometry') or {}).get(k)
        flat.append(v)
    denominators=[]; motion_summary=[]; cost_response=[]; resource_changes=[]
    for scene in SCENES:
        su=[u for u in us if u['scene']==scene]
        for n in (0,2,4):
            selected=[r for r in flat if r['scene']==scene and r['increment']==n]
            es=[r for r in selected if r['method']=='equal_arc']; ps=[r for r in selected if r['method']=='projected_mass']
            states=Counter('both_success' if e['success'] and p['success'] else 'equal_only' if e['success'] else
                'projected_only' if p['success'] else 'both_fail' for e,p in zip(es,ps))
            denominators.append({'scene':scene,'increment':n,'N':selected[0]['count'],'physical':20,'task_rows':40,
                'people_legal':sum(frozen['sources'][u['physical_key']]['people_quality']['accepted'] for u in su),
                'curve_legal':sum(frozen['sources'][u['physical_key']]['status']=='ACCEPTED' for u in su),
                'initials_legal':sum(frozen['starts'][u['rows'][n]['equal_arc']['initial_key']]['initialization_accepted'] for u in su),
                'both_target_valid':sum(target_pair(u,n) for u in su),'all_N_both_target_valid':sum(common_target(u) for u in su),
                'success_rows':sum(r['success'] for r in selected),**dict(states),'failures':dict(Counter(r['status'] for r in selected))})
            for method in METHODS:
                selected_method=[r for r in selected if r['method']==method]
                success=[r for r in selected_method if r['success']]
                motion_summary.append({'scene':scene,'increment':n,'count':selected[0]['count'],'method':method,
                    'all_rows':20,'success':len(success),'executed':sum(r['executed'] for r in selected_method),
                    'failed_execution':[r['task_id'] for r in selected_method if r['executed'] and not r['success']],
                    **{k:{'mean':float(np.mean([r[k] for r in success])) if success else None,
                          'minimum':min((r[k] for r in success),default=None),'maximum':max((r[k] for r in success),default=None)}
                       for k in ('successful_deployment_time_s','cutoff_margin_s','actual_trajectory_length_m','per_guide_travel_m','recoveries')}})
                for metric in METRICS:
                    qualified=[r['planned_'+metric] for r in selected_method if r['target_valid']]
                    cost_response.append({'scene':scene,'increment':n,'count':selected[0]['count'],'method':method,'metric':metric,
                        'n':len(qualified),'mean':float(np.mean(qualified)) if qualified else None})
                for u in su:
                    old=u['rows'][0][method]; new=u['rows'][n][method]
                    if not(old['target_valid'] and new['target_valid']): continue
                    a,b=old['planned_cost']['demand'],new['planned_cost']['demand']
                    resource_changes.append({'scene':scene,'seed':u['seed'],'physical_key':u['physical_key'],
                        'increment':n,'method':method,'difference':b-a,'relative_pct':100*(b-a)/a})
    fine=[]
    for u in us:
        for n in (0,2,4):
            if not target_pair(u,n): continue
            e,p=[u['rows'][n][m] for m in METHODS]
            x=p['planned_cost']['demand']-e['planned_cost']['demand']; y=p['fine_cost']['demand']-e['fine_cost']['demand']
            fine.append({'physical_key':u['physical_key'],'scene':u['scene'],'increment':n,'main_delta':x,'fine_delta':y,
                'sign_changed':bool(np.sign(x)!=np.sign(y)),'absolute_delta_change':abs(y-x)})
    checks=read(root/'all_independent_checks.json'); executed=[r for r in flat if r['executed']]
    summary={'scope':'exposed inputs; resource response development, not new independent confirmation',
        'source':lock['provenance'],'baseline_source':lock['baseline_source'],'all_rows':len(rows),'physical_units':len(us),
        'outcomes':dict(Counter(r['status'] for r in rows)), 'modes':dict(Counter(r['mode'] for r in rows)),
        'executed_rows':len(executed),'new_executed_rows':sum(r['executed'] and r['mode']!='reference' for r in rows),
        'reused_executed_rows':sum(r['executed'] and r['mode']=='reference' for r in rows),
        'denominators':denominators,'contrasts':contrasts,'interactions':interaction,'motion':motion_summary,
        'cost_response':cost_response,'bootstrap':bootstrap(us,lock['spec']['statistics']),
        'common_target_units':sum(common_target(u) for u in us),'fine_sign_changes':sum(r['sign_changed'] for r in fine),
        'max_fine_delta_change':max(r['absolute_delta_change'] for r in fine),
        'max_fine_single_cost_change':max(abs(r['fine_demand']-r['planned_demand']) for r in flat if r['target_valid']),
        'independent_inconsistent':sum(any(c.get(k) is False for k in ('criteria_consistent','diagnostics_consistent','success_consistent')) for c in checks),
        'safety_minima':{k:min(r['minimum_net_'+k+'_m'] for r in executed) for k in ('crowd','guides','walls')},
        'slowest_success':max((r for r in flat if r['success']),key=lambda r:r['successful_deployment_time_s']),
        'execution_failures':[r for r in flat if r['executed'] and not r['success']],
        'threads':sorted({p['num_threads'] for r in rows if r['mode']!='reference' for p in r.get('worker',{}).get('numeric_pools',[])}),
        'counterfactual':sorted({r.get('worker',{}).get('counterfactual') for r in rows if r['mode']!='reference'}),
        'compute':[{ 'increment':n,'method':m,'cost_origin':'historical' if n==0 else 'new',
            **{k:{'n':len(v),'mean':float(np.mean(v)) if v else None,'maximum':max(v,default=None)}
               for k in ('original_layout_s','all_phase_s','initial_path_s','runner_s')
               for v in [[r['timing'][k] for r in rows if r['increment']==n and r['method']==m and k in r['timing']]]}}
            for n in (0,2,4) for m in METHODS]}
    csv_write(report/'tasks_360.csv',flat); csv_write(report/'pairs_all_metrics.csv',pair_rows)
    csv_write(report/'interactions_common.csv',interaction_rows); csv_write(report/'resource_changes.csv',resource_changes)
    csv_write(report/'fine_integration.csv',fine)
    diagnostics=read(root/'quantity_diagnostics_60.json'); csv_write(report/'quantity_diagnostics_60.csv',diagnostics)
    for name in ('manifest_360.json','inputs_frozen.json','reuse_decisions_120.json','all_independent_checks.json','environment.json','protocol.json'):
        write(report/name,read(root/name))
    write(report/'analysis.json',summary)
    plots(flat,cost_response,report)
    locked(root,full=True)
    write(report/'raw_checksums.json',{'run_files':{p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file()},
        'historical_verified_files':len(lock['historical_files']),'historical_unchanged':True,
        'frozen_input_files':len(frozen['files']),'manifest_sha256':sha(root/'manifest_360.json')})
    print({k:summary[k] for k in ('all_rows','outcomes','executed_rows','new_executed_rows','reused_executed_rows',
        'common_target_units','fine_sign_changes','independent_inconsistent','safety_minima')})


def plots(flat,costs,report):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(4,3,figsize=(13,13),constrained_layout=True)
    for i,metric in enumerate(METRICS):
        for j,scene in enumerate(SCENES):
            ax=axes[i,j]
            for method in METHODS:
                selected=[r for r in costs if r['scene']==scene and r['method']==method and r['metric']==metric]
                ax.plot([r['count'] for r in selected],[r['mean'] for r in selected],'-o',label=method)
                failed=[r for r in flat if r['scene']==scene and r['method']==method and r['target_valid'] and not r['success']]
                ax.scatter([r['count'] for r in failed],[r['planned_'+metric] for r in failed],marker='x',color='red')
            ax.set(title=scene+' / '+metric,xlabel='N',ylabel='planned cost (m²)'); ax.grid(alpha=.2)
            if i==0 and j==0: ax.legend()
    fig.savefig(report/'planning_response.png',dpi=160); plt.close(fig)
    fig,axes=plt.subplots(5,3,figsize=(13,15),constrained_layout=True)
    keys=['success','cutoff_margin_s','actual_trajectory_length_m','per_guide_travel_m','recoveries']
    for i,key in enumerate(keys):
        for j,scene in enumerate(SCENES):
            ax=axes[i,j]
            for method in METHODS:
                xs=[]; ys=[]
                for n in (0,2,4):
                    selected=[r for r in flat if r['scene']==scene and r['method']==method and r['increment']==n]
                    xs.append(selected[0]['count']); measured=selected if key=='success' else [r for r in selected if r['success']]
                    ys.append(np.mean([r[key] for r in measured]) if measured else np.nan)
                    if key!='success':
                        ax.scatter([xs[-1]]*len(measured),[r[key] for r in measured],s=7,alpha=.25)
                ax.plot(xs,ys,'-o',label=method)
            ax.set(title=scene+' / '+key,xlabel='N'); ax.grid(alpha=.2)
            if i==0 and j==0: ax.legend()
    fig.savefig(report/'execution_response.png',dpi=160); plt.close(fig)
    # Status figure explicitly distinguishes rejected/unexecuted from actual failures.
    fig,axes=plt.subplots(1,3,figsize=(13,4),constrained_layout=True)
    for ax,scene in zip(axes,SCENES):
        labels=[]; counts=[]
        for n in (0,2,4):
            for method in METHODS:
                selected=[r for r in flat if r['scene']==scene and r['increment']==n and r['method']==method]
                labels.append(str(selected[0]['count'])+' '+method[:4])
                counts.append([sum(r['success'] for r in selected),sum(r['executed'] and not r['success'] for r in selected),
                    sum(not r['executed'] for r in selected)])
        base=np.zeros(len(labels))
        for k,label in enumerate(('complete success','execution failure','not executed')):
            values=np.array(counts)[:,k]; ax.bar(labels,values,bottom=base,label=label); base+=values
        ax.set(title=scene,ylabel='tasks'); ax.tick_params(axis='x',rotation=60)
    axes[0].legend(); fig.savefig(report/'stage_accounting.png',dpi=160); plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',default=OUTPUT)
    p.add_argument('--report',default='reports/step1_nonconvex_resource_response_v1'); a=p.parse_args(); summarize(a.output,a.report)


if __name__=='__main__': main()
