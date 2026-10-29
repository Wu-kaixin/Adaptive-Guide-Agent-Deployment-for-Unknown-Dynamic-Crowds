"""Full-denominator metrics for the single final static Step 1 experiment."""
from collections import Counter,defaultdict
import json
from pathlib import Path

import numpy as np

from udc.experiments.static_multigroup import digest,write_json


def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))


def paired_interval(values,expected_strata,repetitions,seed):
    grouped=defaultdict(list)
    for (k,shape,_),v in values.items():grouped[k,shape].append(v)
    if set(grouped)!=set(expected_strata):return dict(estimate=None,ci_95=None,reason='missing_prespecified_strata',paired_blocks=len(values))
    rng=np.random.default_rng(seed);draws=np.zeros(repetitions);estimate=0.
    for key in sorted(grouped):
        data=np.asarray(grouped[key],float);estimate+=float(data.mean())/len(grouped)
        draws+=rng.choice(data,(repetitions,len(data)),replace=True).mean(axis=1)/len(grouped)
    return dict(estimate=estimate,ci_95=np.quantile(draws,[.025,.975]).tolist(),paired_blocks=len(values),weighting='equal_K_shape_strata')


def assess(study,failure,wall_s):
    study=Path(study);m=read(study/'manifest.json');model=m['spec']['model']|m['design']['model'];settings=m['design']['final_assessment']
    statuses=Counter();cells={};count=0;intervals=0;unsafe=0;delivered=0;full=0;max_speed=0.;max_tracking=0.;max_duration=0.;deadline_violations=0
    minima={n:float('inf') for n in ('human','guide','wall')};by_resource={};max_observation_error=0.
    contract_failures=Counter();guard_frames=Counter();max_gaps=[];curve_errors=[]
    for path in sorted((study/'results').rglob('receipt.json')) if (study/'results').exists() else ():
        receipt=read(path)
        for n,h in receipt.items():
            if digest(path.parent/n)!=h:raise ValueError('Final metrics encountered changed artifact')
        payload=read(path.parent/'metrics.json');payload=payload if isinstance(payload,list) else [payload]
        for row in payload:
            count+=1;statuses[row['status']]+=1
            key='|'.join(str(row.get(k,'nominal')) for k in ('resource','pattern','method'))
            c=cells.setdefault(key,dict(records=0,delivered=0,full_success=0,partial_service=0,travel_m=0.,service_records=0,worst_uncovered_sum=0.,weighted_uncovered_sum=0.))
            valid_status=row['status'] in ('FULL_SUCCESS','PARTIAL_SERVICE')
            completed=bool(row.get('deployment_completed')) and valid_status
            success=row['status']=='FULL_SUCCESS' and bool(row.get('full_task_success'))
            c['records']+=1;c['delivered']+=completed;c['full_success']+=success;c['partial_service']+=row['status']=='PARTIAL_SERVICE'
            delivered+=completed;full+=success
            r=by_resource.setdefault(row['resource'],dict(records=0,delivered=0,full_success=0,partial_service=0,travel_m=0.,service_records=0,worst_uncovered_sum=0.,weighted_uncovered_sum=0.))
            r['records']+=1;r['delivered']+=completed;r['full_success']+=success;r['partial_service']+=row['status']=='PARTIAL_SERVICE'
            motion=row.get('motion',{});intervals+=motion.get('executed_intervals') or 0;unsafe+=motion.get('unsafe_interval_count') or 0
            for name in ('initial_safety_ok','interval_safety_ok','terminal_arrival_ok','speed_limit_ok','kinematics_ok'):
                if name in motion and motion[name] is False:contract_failures[name]+=1
            max_observation_error=max(max_observation_error,row.get('measurement_max_error_m') or 0.)
            max_gaps.extend(row.get('observed_final_max_gaps_m',[]));curve_errors.extend(v for v in row.get('observed_final_curve_errors_m',[]) if v is not None)
            if row.get('kind')=='episode':
                guard_frames[row.get('method','unknown')+'|updates']+=1
                guard_frames[row.get('resource','unknown')+'|'+row.get('method','unknown')+'|updates']+=1
                if row.get('accepted_certificate'):guard_frames[row['resource']+'|'+row['method']+'|accepted']+=1
                if row.get('decision')=='uncertified_fallback':guard_frames[row['resource']+'|'+row['method']+'|fallback']+=1
            max_speed=max(max_speed,motion.get('max_speed_m_s') or 0.);max_tracking=max(max_tracking,row.get('tracking_error_m') or 0.)
            duration=motion.get('simulation_time_s') or 0.;max_duration=max(max_duration,duration);deadline_violations+=duration>model['deadline_s']
            c['travel_m']+=motion.get('travel_m') or 0.
            r['travel_m']+=motion.get('travel_m') or 0.
            for n,v in motion.get('minimum_net_clearances_m',{}).items():
                if v is not None:minima[n]=min(minima[n],v)
            service=row.get('continuous_actual_service')
            if service is not None:
                for entry in (c,r):
                    entry['service_records']+=1;entry['worst_uncovered_sum']+=service['worst_group_uncovered'];entry['weighted_uncovered_sum']+=service['weighted_uncovered']
    for c in (*cells.values(),*by_resource.values()):
        n=c['service_records'];c['mean_worst_uncovered']=c.pop('worst_uncovered_sum')/n if n else None;c['mean_weighted_uncovered']=c.pop('weighted_uncovered_sum')/n if n else None
    audit_path=study/'readonly_audit/offline_audit.json';audit=read(audit_path) if audit_path.exists() else None
    episodes_path=study/'readonly_audit/episode_summaries.json';episodes=read(episodes_path) if episodes_path.exists() else []
    pairs=defaultdict(dict)
    for e in episodes:pairs[e['group_count'],e['shape'],e['seed'],e['resource'],e['pattern']][e['method']]=e
    strata={(u['group_count'],u['shape']) for u in m['units']};differences={}
    bound=m['design']['steps']*max(m['spec']['resources'].values())*max(m['design']['group_counts'])*model['max_speed_m_s']*model['deadline_s']
    for resource in m['design']['resources']:
        for pattern in m['design']['patterns']:
            values={};lower={};upper={}
            for u in m['units']:
                key=(u['group_count'],u['shape'],u['seed']);pair=pairs.get((*key,resource,pattern),{})
                a,b=pair.get('recompute_all'),pair.get('counts_phase_guard')
                if a and b and a['sequence_completed'] and b['sequence_completed']:
                    values[key]=b['actual_total_travel_m']-a['actual_total_travel_m'];lower[key]=upper[key]=values[key]
                else:lower[key],upper[key]=-bound,bound
            result=paired_interval(values,strata,settings['bootstrap_repetitions'],settings['bootstrap_seed'])
            result.update(scheduled_pairs=len(m['units']),missing_pairs=len(m['units'])-len(values),
                full_ledger_identification_range_m=[paired_interval(v,strata,1,0)['estimate'] for v in (lower,upper)])
            differences[resource+'|'+pattern]=result
    api=study/'api_contract_audit';api_completion=read(api/'completion.json') if (api/'completion.json').exists() else None
    api_rows=[read(p)['comparison'] for p in (api/'case_audits').glob('*.json')] if api.exists() else []
    latencies=[r for p in (api/'case_timings').glob('*.json') for r in read(p)] if api.exists() else []
    timings=[read(p) for p in (study/'unit_timings').glob('*.json')]
    metrics=dict(scheduled_records=m['scheduled_step_records'],recorded_records=count,missing_records=m['scheduled_step_records']-count,
        statuses=dict(statuses),deployment_completed=delivered,full_success=full,partial_service=statuses['PARTIAL_SERVICE'],
        full_success_fraction_of_scheduled=full/m['scheduled_step_records'],delivery_fraction_of_scheduled=delivered/m['scheduled_step_records'],
        executed_intervals=intervals,unsafe_intervals=unsafe,min_net_clearances_m={k:(v if np.isfinite(v) else None) for k,v in minima.items()},
        max_speed_m_s=max_speed,max_tracking_error_m=max_tracking,max_simulation_duration_s=max_duration,deadline_violations=deadline_violations,
        motion_contract_failure_records=dict(contract_failures),maximum_actual_observation_error_m=max_observation_error,
        maximum_observed_gap_m=max(max_gaps,default=None),maximum_observed_curve_error_m=max(curve_errors,default=None),
        program_error_records=statuses['EXPERIMENT_ERROR'],model_bound_violation_records=statuses['MODEL_BOUND_VIOLATION'],safety_violation_records=statuses['SAFETY_VIOLATION'],
        cells=cells,by_resource=by_resource,guard_frames=dict(guard_frames),paired_travel_differences= differences,
        scheduled_sequences=settings['expected_sequences'],completed_sequences=sum(bool(e['sequence_completed']) for e in episodes),
        independent_replay_all_match=audit['all_match'] if audit else False,
        replay_checked_records=audit['checked_step_records'] if audit else 0,
        update_methods=audit['by_method'] if audit else None,
        api=dict(all_match=api_completion['all_match'] if api_completion else False,expected_sessions=settings['expected_api_sessions'],
            recorded_sessions=api_completion['cases'] if api_completion else len(api_rows),expected_events=settings['expected_api_events'],
            recorded_events=api_completion['actual_events'] if api_completion else sum(r['compared_events'] for r in api_rows),
            completed_case_audits=len(api_rows),case_mismatches=sum(len(r['mismatches']) for r in api_rows),
            event_boundary_restores=(api_completion['cases']*len(settings['api_restart_after_updates'])) if api_completion else 0,
            scope='predeclared same-input API conformance and same-process event-boundary restore; no physical crash or dynamic backend proof'),
        computation=dict(total_pipeline_wall_s=wall_s,unit_cpu_s=sum(t['cpu_s'] for t in timings),
            maximum_process_lifetime_peak_working_set_bytes=max((t['process_lifetime_peak_working_set_bytes'] or 0 for t in timings),default=None),
            api_timed_events=len(latencies),api_event_wall_p50_s=float(np.median([x['wall_s'] for x in latencies])) if latencies else None,
            api_event_wall_p95_s=float(np.quantile([x['wall_s'] for x in latencies],.95)) if latencies else None),
        thresholds=model,source_manifest_sha256=digest(study/'manifest.json'),terminal_failure=failure,
        service_guard_quarantined=True,general_error_original_reference_certificate_complete=False,novelty_established=False,
        dynamic_step2_3_validation_complete=False,whole_goal_achieved=False,single_final_experiment=True,no_further_experiments=True)
    output=study/'final_assessment';output.mkdir(exist_ok=False);write_json(output/'metrics.json',metrics)
    lines=['# 最后一次 Step 1 综合实验评估','',
        '结论：本次实验已结束；整个长期目标未被宣告完成。低资源原参考证书与方法独特性仍缺必要证据。',
        '本次之后不启动新实验或定时器；长期目标已按用户停止要求置为paused。','',
        '| 指标 | 实测 |','|---|---:|',
        f'| 新物理输入 | {len(m["units"])} |',f'| 预定／保存记录 | {m["scheduled_step_records"]}／{count} |',
        f'| 完整成功／部分服务 | {full}／{statuses["PARTIAL_SERVICE"]} |',f'| 已交付 | {delivered} |',
        f'| 实际运动区间／不安全区间 | {intervals}／{unsafe} |',f'| 最大速度 m/s | {max_speed} |',
        f'| 最大跟踪误差 m | {max_tracking} |',f'| 截止违反 | {deadline_violations} |',
        f'| 决策及物理全量复判 | {metrics["independent_replay_all_match"]} |',
        f'| API会话／事件／事件边界恢复 | {metrics["api"]["recorded_sessions"]}／{metrics["api"]["recorded_events"]}／{metrics["api"]["event_boundary_restores"]} |','',
        '完整成功与部分服务分开，失败和未执行尾帧进入完整分母。阈值与安全／服务判据沿用冻结协议。',
        '比较差值和区间按独立物理输入分块，逐资源／扰动结果、计算开销及所有状态见metrics.json。',
        'Step 2/3仅有静态公共接口、阶段隔离及事件边界恢复证据，尚无动态人群、有限通信或新执行backend的稳定性证明。',
        '当前门控隔离造成的回退不能作为新服务保持方法的性能贡献；工程测试和本次有限实验不能替代方法独特性与普适服务证明。']
    (output/'REPORT.zh.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {k:metrics[k] for k in ('recorded_records','scheduled_records','statuses','independent_replay_all_match','api','whole_goal_achieved','terminal_failure')}
