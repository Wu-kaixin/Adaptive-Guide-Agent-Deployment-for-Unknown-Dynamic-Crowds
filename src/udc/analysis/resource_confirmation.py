"""Predeclared I2, whole-input uncertainty, and separate planning/motion accounting."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import numpy as np

from udc.analysis import resource_response as rr
from udc.analysis.demand_confirmation import csv_write
from udc.experiments.demand_pilot import read
from udc.experiments.nonconvex_rebuild import write, sha
from udc.experiments.resource_confirmation import locked, OUTPUT, REPORT, SCENES, METHODS


def inference(all_units,settings):
    """One stratified physical draw jointly carries all methods, N and missingness."""
    result=rr.bootstrap(all_units,settings)
    columns=[('delta_N0',lambda u:rr.pairs([u],0)),('delta_N2',lambda u:rr.pairs([u],2)),
        ('delta_N4',lambda u:rr.pairs([u],4)),('interaction_N2',lambda u:rr.interactions([u],2)),
        ('interaction_N4',lambda u:rr.interactions([u],4))]
    for name,select in columns:
        counts={s:sum(bool(select(u)) for u in all_units if u['scene']==s) for s in SCENES}
        result['estimates'][name]['valid_pairs_by_scene']=counts
        result['estimates'][name]['physical_denominator_by_scene']={s:sum(u['scene']==s for u in all_units) for s in SCENES}
        if min(counts.values())<settings['minimum_valid_pairs_per_scene_for_interval']:
            result['estimates'][name]['interval']=None
            result['estimates'][name]['interval_withheld_reason']='fewer than predeclared minimum pairs in a scene'
        if result['estimates'][name]['uncalculable']/settings['bootstrap_repetitions']>settings['maximum_uncalculable_bootstrap_share']:
            result['estimates'][name]['interval']=None
            result['estimates'][name]['interval_withheld_reason']='too many uncomputable resamples'
    # Scene intervals use the identical joint sampled indices as the aggregate.
    rng=np.random.default_rng(settings['bootstrap_rng_seed']); by={}
    for scene in SCENES:
        us=[u for u in all_units if u['scene']==scene]
        values=np.array([[(select(u)[0]['difference'] if select(u) else np.nan) for _,select in columns] for u in us])
        indices=rng.integers(0,len(us),size=(settings['bootstrap_repetitions'],len(us)))
        sample=values[indices]; counts=np.isfinite(sample).sum(axis=1)
        distribution=np.divide(np.nansum(sample,axis=1),counts,out=np.full(counts.shape,np.nan),where=counts>0)
        by[scene]={}
        for i,(name,_) in enumerate(columns):
            finite=np.isfinite(distribution[:,i]); n=int(np.isfinite(values[:,i]).sum())
            by[scene][name]={'n':n,'uncalculable':int((~finite).sum()),
                'interval':np.quantile(distribution[finite,i],[.025,.975]).tolist() if
                    n>=settings['minimum_valid_pairs_per_scene_for_interval'] and finite.mean()>=1-settings['maximum_uncalculable_bootstrap_share'] else None}
    result['by_scene']=by
    return result


def motion_subsets(all_units):
    output=[]
    for scene in SCENES:
        us=[u for u in all_units if u['scene']==scene]
        common=[u for u in us if all(u['rows'][n][m]['success'] for n in (0,2,4) for m in METHODS)]
        for n in (0,2,4):
            own=[u for u in us if all(u['rows'][n][m]['success'] for m in METHODS)]
            for label,subset in [('own_N_both_success',own),('all_six_common_success',common)]:
                for method in METHODS:
                    rows=[u['rows'][n][method] for u in subset]
                    def values(key):
                        if key=='time_s': return [r['motion']['simulation_time_s'] for r in rows]
                        if key=='slack_s': return [40-r['motion']['simulation_time_s'] for r in rows]
                        if key=='travel_m': return [r['motion']['actual_trajectory_length_m'] for r in rows]
                        if key=='per_guide_m': return [r['motion']['actual_trajectory_length_m']/r['count'] for r in rows]
                        return [r['recoveries'] for r in rows]
                    output.append({'scene':scene,'increment':n,'method':method,'subset':label,'n':len(rows),
                        **{key:float(np.mean(values(key))) if rows else None for key in ('time_s','slack_s','travel_m','per_guide_m','recoveries')}})
    return output


def summarize(output,report):
    root=Path(output).resolve(); report=Path(report); lock=locked(root,full=True)
    exports=['manifest_360.json','inputs_frozen.json','all_independent_checks.json','environment.json','protocol.json',
        'seed_audit.json','audit_at_input_freeze.json','first_gate.json','first_summary.json','remaining_summary.json']
    rr.summarize(root,report,lock_loader=lambda p:locked(p,full=True),
        scope='new local unexposed personnel inputs; fixed-template paired resource confirmation',export_files=exports)
    rows=[read(root/'results'/r['task_id']/'protocol_metrics.json') for r in read(root/'manifest_360.json')['rows']]
    us=rr.units(rows); summary=read(report/'analysis.json')
    stats=inference(us,lock['spec']['statistics']); primary=rr.contrast(rr.interactions(us,2))
    interval=stats['estimates']['interaction_N2']['interval']
    summary.update(primary={'name':'I2 = delta_N2 - delta_N0','unit':'m²','direction':'negative',
        'subset':'four qualified targets at N0/N2 regardless of execution','result':primary,
        'interval':interval,'negative_direction_clearly_confirmed':bool(interval is not None and interval[1]<0)},
        bootstrap=stats,motion_paired_subsets=motion_subsets(us),
        runner_outcomes=dict(Counter(r['runner_status'] for r in rows)),
        orchestration_outcomes=dict(Counter(r['orchestration_status'] for r in rows)),
        physical_pass=sum(r['independent_physical_success'] is True for r in rows),
        physical_fail=sum(r['independent_physical_success'] is False for r in rows),
        physical_not_executed=sum(r['independent_physical_success'] is None for r in rows),
        source_scientific_consistency=lock['scientific_consistency'],
        original_resource_ledger_unchanged=True)
    interactions=[]; resource=[]; actual=[]; queries=[]
    for n in (2,4):
        for common in (False,True):
            interactions += [{**r,'subset':'all_six_targets' if common else 'own_four_targets'} for r in rr.interactions(us,n,common)]
        for u in us:
            for method in METHODS:
                a,b=u['rows'][0][method],u['rows'][n][method]
                if not(a['target_valid'] and b['target_valid']): continue
                for metric in rr.METRICS:
                    x,y=a['planned_cost'][metric],b['planned_cost'][metric]
                    resource.append({'scene':u['scene'],'seed':u['seed'],'physical_key':u['physical_key'],'method':method,
                        'increment':n,'metric':metric,'baseline':x,'new':y,'difference':y-x,'relative_pct':100*(y-x)/x})
    for n in (0,2,4):
        actual += [{**r,'subset':'both_complete_success'} for u in us if all(u['rows'][n][m]['success'] for m in METHODS)
            for r in rr.pairs([u],n,source='actual_cost')]
    for r in rows:
        raw=read(root/'results'/r['task_id']/'metrics.json'); events=raw.get('recovery_attempts',[])
        queries.append({'task_id':r['task_id'],'runner_status':r['runner_status'],'orchestration_status':r['orchestration_status'],
            'physical_success':r['independent_physical_success'],'recovery_attempts':len(events) if r['executed'] else None,
            'failed_queries':sum(not e.get('accepted',False) for e in events) if r['executed'] else None,
            'assignment_changes':r.get('assignment_trace',{}).get('changes'),
            'trace_accepted':r.get('assignment_trace',{}).get('accepted'),
            'source_commit':r['source_commit'],'source_sha256':r['source_sha256']})
    csv_write(report/'primary_and_secondary_interactions.csv',interactions)
    csv_write(report/'own_method_resource_all_metrics.csv',resource)
    csv_write(report/'actual_success_pairs.csv',actual)
    csv_write(report/'motion_paired_subsets.csv',summary['motion_paired_subsets'])
    csv_write(report/'runner_orchestration_physical_audit.csv',queries)
    write(report/'analysis.json',summary)
    write(report/'all_independent_integrations.json',read(root/'all_independent_integrations.json'))
    print({'primary':summary['primary'],'runner':summary['runner_outcomes'],'orchestration':summary['orchestration_outcomes'],
        'physical_pass':summary['physical_pass'],'physical_fail':summary['physical_fail']})


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',default=OUTPUT); p.add_argument('--report',default=REPORT)
    a=p.parse_args(); summarize(a.output,a.report)


if __name__=='__main__': main()
