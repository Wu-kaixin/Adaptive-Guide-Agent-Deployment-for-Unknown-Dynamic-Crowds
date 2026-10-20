"""Persisted-frame priority, queue and stall-window evidence; no new solver."""
from __future__ import annotations
import argparse
from collections import Counter
from pathlib import Path
import numpy as np
import shapely
from shapely.geometry import Polygon

from udc.analysis.demand_confirmation import csv_write
from udc.controller.progress import priority_path_stall, ordinary_stall
from udc.experiments.deadline_diagnostics import OUTPUT, REPORT, lock
from udc.experiments.demand_pilot import read, arrays
from udc.experiments.nonconvex_rebuild import write, sha


def analyze_attempt(root,attempt,report):
    directory=root/attempt; summary=read(directory/'summary.json'); frames=read(directory/'frames.json')
    monitors=read(directory/'monitor.json'); calls=read(directory/'joint_calls.json')
    trajectory=arrays(directory/'trajectory.npz'); cfg=read(root/'protocol.json')['original_config']; dt=cfg['simulation']['dt']
    control=cfg['controller']; tol=control['position_tolerance']; window=control['stall_window']
    mm={m['frame']:m for m in monitors}; timeline=[]; changes=[]; handoffs=[]; windows=[]
    for r in frames:
        g=r['priority_guide']; before=r['before']; after=r['after_step']; t0=r['time_start_s']; t1=r['time_end_s']
        settled=(max(r['errors_after'])<=tol and np.max(np.linalg.norm(r['filtered'],axis=1))<=control['speed_tolerance'])
        limited=(g is not None and r['filtered_along_m_s'] < .9*r['nominal_along_m_s'])
        near=(g is not None and r['aim_distance_before_m']<=control['waypoint_tolerance'])
        # This partition is exclusive. Limited/near flags are also exported as
        # overlapping descriptions and must never be added to the partition.
        category=('final_hold' if settled else
            'ordinary_return' if g is None and before['priority_order'] else
            'ordinary_before_priority' if g is None else
            'priority_filter_limited' if limited else
            'priority_short_aim_step' if near else 'priority_effective_advance')
        current_active=[c for c in r['active_constraints'] if g is not None and (c['guide']==g or c['other_guide']==g)]
        m=mm.get(r['frame']); maturity=None; decrease=None; ordinary_context=None
        if m is not None:
            ordinary_context=ordinary_stall(m['ordinary_progress'],window,tol,m['max_error'],
                float(np.linalg.norm(r['filtered'],axis=1).mean()),control['speed_tolerance'])
        if m is not None and m['priority_mode'] and m['reference'] is not None:
            samples=m['after']['samples']; maturity=len(samples)>=window+1
            decrease=samples[-window-1]-samples[-1] if maturity else None
            expected=priority_path_stall(samples,window,tol,m['max_error'])
            if expected!=m['stalled']: raise RuntimeError('Priority monitor decision mismatch')
            ref=m['reference']; token=[ref['guide'],ref['waypoint'],ref['path_sha256']]; b=m['before']
            continuous=(token==b['token'] and len(b['samples'])>=1 and abs(ref['remaining_before_m']-b['samples'][-1])<=1e-6)
            after_remaining=r['remaining_after'][g]
            expected_samples=b['samples']+[after_remaining] if continuous else [ref['remaining_before_m'],after_remaining]
            if not np.array_equal(expected_samples,samples): raise RuntimeError('Priority sample evolution mismatch')
            if not continuous:
                reasons=[]
                if b['token'] is None: reasons=['initial']
                else:
                    for i,key in enumerate(('guide','waypoint','path')):
                        if token[i]!=b['token'][i]: reasons.append(key+'_changed')
                    if abs(ref['remaining_before_m']-b['samples'][-1])>1e-6: reasons.append('remaining_definition_discontinuity')
                changes.append({'frame':r['frame'],'time_start_s':t0,'guide':g,'waypoint':ref['waypoint'],
                    'reasons':','.join(reasons),'previous_guide':None if b['token'] is None else b['token'][0],
                    'previous_remaining':b['samples'][-1] if b['samples'] else None,'new_remaining':ref['remaining_before_m'],
                    'jump_not_counted_as_progress':True,'new_sample_count':len(samples)})
        if after['priority_index']>before['priority_index']:
            for index in range(before['priority_index'],after['priority_index']):
                guide=after['priority_order'][index]
                handoffs.append({'frame':r['frame'],'time_s':t0,'queue_index':index,'guide':guide,
                    'position':before['positions'][guide],'error_m':r['errors_before'][guide],
                    'within_original_tolerance':r['errors_before'][guide]<=tol,
                    'queue_exit':not after['priority_mode']})
        timeline.append({'frame':r['frame'],'start_s':t0,'end_s':t1,'mode':category,'priority_guide':g,
            'priority_index':after['priority_index'],'target_error_before':None if g is None else r['errors_before'][g],
            'target_error_after':None if g is None else r['errors_after'][g],
            'remaining_before':None if g is None else r['priority_reference']['remaining_before_m'],
            'remaining_after':None if g is None else r['remaining_after'][g],
            'waypoint':None if g is None else r['priority_reference']['waypoint'],
            'nominal_along':r.get('nominal_along_m_s'),'projection_along':r.get('projection_along_m_s'),
            'filtered_along':r.get('filtered_along_m_s'),'same_reference_progress':r.get('same_reference_progress_m'),
            'filter_limited_flag':limited,'near_aim_flag':near,'mature_priority_window':maturity,'window_decrease_m':decrease,
            'priority_stalled':None if m is None else m['stalled'],'active_priority_constraint_kinds':','.join(sorted({c['kind'] for c in current_active})),
            'ordinary_mean_stall_formula_context_only':ordinary_context,
            'settled_for_hold':settled,'budget_consumed':after['replans'],'aim_segment_legal':r.get('aim_segment_legal')})
    # Sum actual within-reference reductions, excluding all representation jumps,
    # across waypoint changes of the SAME priority guide. No extra control solve.
    for i,r in enumerate(frames):
        selected=frames[max(0,i-window+1):i+1]; g=r['priority_guide']
        if len(selected)!=window or g is None or any(x['priority_guide']!=g for x in selected): continue
        value=sum(x['same_reference_progress_m'] for x in selected)
        windows.append({'frame':r['frame'],'guide':g,'start_s':selected[0]['time_start_s'],'end_s':r['time_end_s'],
            'actual_path_reduction_excluding_reference_jumps_m':value,'below_original_0_03m_threshold':value<tol,
            'max_target_error':max(r['errors_after'])})
    segments=[]
    for r in timeline:
        key=(r['mode'],r['priority_guide'])
        if not segments or (segments[-1]['mode'],segments[-1]['guide'])!=key:
            segments.append({'mode':r['mode'],'guide':r['priority_guide'],'start_s':r['start_s'],'end_s':r['end_s'],'frames':1})
        else: segments[-1]['end_s']=r['end_s']; segments[-1]['frames']+=1
    service=[]
    for g in dict.fromkeys(r['priority_guide'] for r in frames if r['priority_guide'] is not None):
        selected=[r for r in frames if r['priority_guide']==g]
        service.append({'guide':g,'start_s':selected[0]['time_start_s'],'end_s':selected[-1]['time_end_s'],'steps':len(selected),
            'duration_s':len(selected)*dt,'remaining_at_start_m':selected[0]['priority_reference']['remaining_before_m'],
            'remaining_at_end_m':selected[-1]['remaining_after'][g],'error_at_end_m':selected[-1]['errors_after'][g],
            'actual_same_reference_reduction_m':sum(r['same_reference_progress_m'] for r in selected),
            'filter_limited_steps':sum(r['filtered_along_m_s']<.9*r['nominal_along_m_s'] for r in selected),
            'near_aim_steps':sum(r['aim_distance_before_m']<=control['waypoint_tolerance'] for r in selected)})
    def at_frame(frame):
        r=frames[frame-1]; st=r['after_step']; order=st['priority_order']; index=st['priority_index']
        current=r['priority_guide']; served=order[:index]; pending=order[index+1:] if current is not None else order[index:]
        return {'frame':frame,'time_s':frame*dt,'priority_mode':st['priority_mode'],'priority_index':index,'current':current,
            'served':served,'unserved_after_current':pending,
            'errors':r['errors_after'],'maximum_error_guide':int(np.argmax(r['errors_after'])),
            'outside':[{ 'guide':i,'error_m':e,'role':'current' if i==current else 'served' if i in served else 'pending'}
                for i,e in enumerate(r['errors_after']) if e>tol]}
    post_service=[]
    for h in handoffs:
        guide=h['guide']; later=[r for r in frames if r['time_end_s']>h['time_s']]
        out=[r for r in later if r['errors_after'][guide]>tol]
        post_service.append({'guide':guide,'handoff_s':h['time_s'],'handoff_error_m':h['error_m'],
            'max_error_after_handoff_m':max((r['errors_after'][guide] for r in later),default=h['error_m']),
            'first_pushed_outside_s':out[0]['time_end_s'] if out else None,
            'final_error_m':frames[-1]['errors_after'][guide]})
    # Post-cutoff terminal windows use the original thresholds. Arrival is the
    # same ten-position/velocity condition as evaluate; final original evaluator
    # independently checks all criteria and every physical motion interval.
    data=trajectory; line=Polygon(data['boundary_deployment']).exterior
    points=shapely.points(data['positions'].reshape(-1,2)); shape=data['positions'].shape[:2]
    curve=shapely.distance(points,line).reshape(shape)
    arcs=shapely.line_locate_point(line,points).reshape(shape)
    errors=np.linalg.norm(data['positions']-data['targets'][data['assignment']][None,:,:],axis=2)
    gaps=np.max(np.diff(np.c_[np.sort(arcs,axis=1),np.sort(arcs,axis=1)[:,0]+line.length],axis=1),axis=1)
    speeds=np.linalg.norm(data['safe_velocity'],axis=2); h=control['hold_steps']; conditions=[]
    for k in range(max(400,h),len(data['positions'])):
        arrival=bool(np.all(errors[k-h+1:k+1]<=tol) and np.all(speeds[k-h:k]<=control['speed_tolerance']))
        curve_ok=bool(np.all(curve[k-h+1:k+1]<=control['curve_tolerance']))
        gap_ok=bool(np.all(gaps[k-h+1:k+1]<=control['max_gap_limit']))
        conditions.append({'frame':k,'time_s':k*dt,'arrival_hold':arrival,'curve_hold':curve_ok,'gap_hold':gap_ok,
            'coverage_hold':curve_ok and gap_ok,'all_terminal_conditions':arrival and curve_ok and gap_ok,
            'max_error_m':float(errors[k].max()),'max_curve_distance_m':float(curve[k].max()),'max_gap_m':float(gaps[k])})
    first={key:next((r['time_s'] for r in conditions if r[key]),None) for key in
        ('arrival_hold','curve_hold','gap_hold','coverage_hold','all_terminal_conditions')}
    output={'attempt':attempt,'summary':summary,'priority_service':service,'queue_handoffs':handoffs,
        'post_service_displacement':post_service,'at_40s':at_frame(400),'terminal':at_frame(len(frames)),
        'partition_s':{k:v*dt for k,v in Counter(r['mode'] for r in timeline).items()},
        'partition_sum_s':len(timeline)*dt,'overlapping_flags_s':{'limited':sum(r['filter_limited_flag'] for r in timeline)*dt,
            'near_aim':sum(r['near_aim_flag'] for r in timeline)*dt},
        'reference_change_count':len(changes),'reference_changes':changes,
        'mature_priority_windows':sum(r['mature_priority_window'] is True for r in timeline),
        'minimum_original_mature_window_reduction_m':min((r['window_decrease_m'] for r in timeline if r['window_decrease_m'] is not None),default=None),
        'priority_stall_frames':[m['frame'] for m in monitors if m['priority_mode'] and m['stalled']],
        'minimum_actual_same_guide_2s_reduction_m':min((r['actual_path_reduction_excluding_reference_jumps_m'] for r in windows),default=None),
        'actual_same_guide_2s_low_progress_windows':[r for r in windows if r['below_original_0_03m_threshold'] and r['max_target_error']>tol],
        'priority_illegal_aim_frames':[r['frame'] for r in frames if r['priority_guide'] is not None and not r['aim_segment_legal']],
        'actual_segment_checker_rejections':[{'frame':r['frame'],'guides':[i for i,v in enumerate(r['command_segments_legal']) if not v]}
            for r in frames if not all(r['command_segments_legal'])],
        'joint_calls':len(calls),'joint_accepted':sum(c['accepted'] for c in calls),'joint_rejected':sum(not c['accepted'] for c in calls),
        'joint_solver_wall_s':sum(c['solver_wall_s'] for c in calls),'first_post_cutoff_terminal_conditions_s':first,
        'baseline_original_40s_success_remains_false':True}
    csv_write(report/(attempt+'_timeline.csv'),timeline); csv_write(report/(attempt+'_segments.csv'),segments)
    csv_write(report/(attempt+'_service.csv'),service)
    csv_write(report/(attempt+'_window_audit.csv'),windows); csv_write(report/(attempt+'_terminal_conditions.csv'),conditions)
    write(report/(attempt+'_analysis.json'),output)
    return output


def summarize(output=OUTPUT,report=REPORT):
    root=Path(output).resolve(); report=Path(report); report.mkdir(parents=True,exist_ok=True); frozen=lock(root)
    results={name:analyze_attempt(root,name,report) for name in frozen['spec']['attempts']}
    write(report/'analysis.json',results)
    for name in frozen['spec']['attempts']:
        for filename in ('summary.json','independent_rejudge.json'):
            write(report/(name+'_'+filename),read(root/name/filename))
    write(report/'continuation_audit.json',read(root/'diagnostic_continuation_60s/continuation_audit.json'))
    write(report/'environment.json',read(root/'environment.json'))
    write(report/'freeze_summary.json',{k:frozen[k] for k in ('source','old_source','row','initial','original_config','deadline_transform','production_sources')}
        | {'protected_files':len(frozen['protected']),'raw_protocol':str(root/'protocol.json'),'raw_protocol_sha256':sha(root/'protocol.json')})
    plots(root,results,report); lock(root)
    write(report/'raw_checksums.json',{'new_run_files':{p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file()},
        'historical_files_verified':len(frozen['protected']),'original_confirmation_unchanged':True})
    print({name:{k:r[k] for k in ('first_post_cutoff_terminal_conditions_s','priority_stall_frames','minimum_actual_same_guide_2s_reduction_m',
        'priority_illegal_aim_frames','joint_calls')} for name,r in results.items()},flush=True)


def plots(root,results,report):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    frames=read(root/'diagnostic_continuation_60s/frames.json')
    fig,axes=plt.subplots(4,1,figsize=(12,11),sharex=True,constrained_layout=True)
    times=np.array([r['time_end_s'] for r in frames])
    axes[0].step(times,[np.nan if r['priority_guide'] is None else r['priority_guide'] for r in frames],where='post',label='priority Guide ID')
    axes[0].set(ylabel='Guide ID'); axes[0].legend()
    for g in sorted({r['priority_guide'] for r in frames if r['priority_guide'] is not None}):
        axes[1].plot(times,[r['errors_after'][g] for r in frames],label=str(g))
    axes[1].set(ylabel='target error (m)',yscale='log'); axes[1].axhline(.03,color='grey',ls=':'); axes[1].legend(ncol=7,title='served Guide IDs')
    for field,label in (('nominal_along_m_s','nominal along path'),('projection_along_m_s','initial projection'),('filtered_along_m_s','installed velocity')):
        axes[2].plot(times,[r.get(field,np.nan) for r in frames],label=label,alpha=.8)
    axes[2].set(ylabel='priority speed (m/s)'); axes[2].legend()
    for name,result in results.items():
        ms=read(root/name/'monitor.json')
        axes[3].plot([m['frame']*.1 for m in ms],[len(m['after']['samples']) if m['priority_mode'] else np.nan for m in ms],label=name,alpha=.7)
    axes[3].axhline(21,color='grey',ls=':'); axes[3].set(ylabel='priority window samples',xlabel='simulation time (s)'); axes[3].legend()
    for ax in axes:
        ax.axvline(40,color='red',ls='--'); ax.axvline(16.9,color='grey',ls=':'); ax.grid(alpha=.2)
    fig.savefig(report/'priority_timeline.png',dpi=160); plt.close(fig)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',default=OUTPUT); p.add_argument('--report',default=REPORT)
    a=p.parse_args(); summarize(a.output,a.report)


if __name__=='__main__': main()
