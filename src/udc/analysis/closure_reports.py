"""Complete-ledger reports for routing recovery and consecutive deployments."""
from collections import Counter,defaultdict
from datetime import datetime
import json
from pathlib import Path

import numpy as np

from udc.experiments.static_multigroup import digest,write_json


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def stratified(values,seed,repetitions=10000):
    groups=defaultdict(list)
    for (k,shape,_),value in values.items():groups[k,shape].append(value)
    rng=np.random.default_rng(seed);samples=np.zeros(repetitions)
    for numbers in groups.values():
        array=np.asarray(numbers,float);samples+=rng.choice(array,(repetitions,len(array)),replace=True).mean(axis=1)/len(groups)
    return {'estimate':float(np.mean([np.mean(v) for v in groups.values()])),
            'ci95':np.quantile(samples,[.025,.975]).tolist(),'independent_blocks':len(values),'strata':len(groups)}


def routing(study,output):
    study,output=Path(study),Path(output);manifest=read(study/'manifest.json');summary=read(study/'summary.json');audit=read(study/'offline_replay.json')
    if not audit['all_match'] or summary['stopped'] or summary['recorded']!=summary['scheduled']:raise ValueError('Full routing report gate failed')
    rows=[read(study/'results'/t['task_id']/'metrics.json') for t in manifest['tasks']]
    result={'manifest_sha256':digest(study/'manifest.json'),'recorded':len(rows),'scheduled':len(manifest['tasks']),
        'evidence':manifest['evidence'],'all_replay_match':True,'new_independent_samples':manifest['new_independent_samples'],
        'by_policy':{p:{'statuses':dict(Counter(r['status'] for r in rows if r['policy']==p)),
                        'safe_deployments_completed':sum(bool(r.get('deployment_completed')) for r in rows if r['policy']==p),
                        'full_task_success':sum(bool(r.get('full_task_success')) for r in rows if r['policy']==p)} for p in manifest['design']['policies']},
        'safety_violations':sum(r['status']=='SAFETY_VIOLATION' for r in rows),'program_errors':sum(r['status']=='EXPERIMENT_ERROR' for r in rows)}
    statistics=manifest['design'].get('statistics')
    if statistics:
        pairs=defaultdict(dict)
        for row in rows:pairs[(row['group_count'],row['shape'],row['seed'],row['resource'],row['allocator'])][row['policy']]=row
        contrasts={}
        for baseline in ['verified_q4_forward','adaptive_forward']:
            blocks=defaultdict(list)
            for key,pair in pairs.items():
                blocks[key[:3]].append(int(bool(pair['adaptive_reverse'].get('deployment_completed')))-int(bool(pair[baseline].get('deployment_completed'))))
            values={key:np.mean(v) for key,v in blocks.items()}
            contrasts['adaptive_reverse_minus_'+baseline]=stratified(values,statistics['bootstrap_seed'],statistics['bootstrap_repetitions'])
        result['predeclared_primary_and_secondary_contrasts']=contrasts
    output.mkdir(parents=True,exist_ok=False);write_json(output/'results.json',result)
    text=['# 安全替代路径实验结果','',f"完成：{len(rows)}/{len(manifest['tasks'])}，保存轨迹复判全部一致。",'',
          f"证据性质：{manifest['evidence']}。旧动作回归不增加独立样本，所有旧失败保留。",'',
          '| 策略 | 安全部署完成 | 完整任务成功 | 状态 |','|---|---:|---:|---|']
    for name,item in result['by_policy'].items():text.append(f"| {name} | {item['safe_deployments_completed']} | {item['full_task_success']} | {item['statuses']} |")
    text.extend(['','部署完成、完整覆盖和路径失败分开记账；失败短路程不算效率优势。', '',
        '三策略共享目标和初态，逐步改变圆盘表示与串行顺序；所有边和实际运动区间保留独立解析验证。', '',
        '```json',json.dumps(result,ensure_ascii=False,indent=2),'```'])
    (output/'REPORT.zh.md').write_text('\n'.join(text),encoding='utf-8');return result


def sequential(study,output,audit_output=None):
    study,output=Path(study),Path(output);audit_output=Path(audit_output) if audit_output is not None else study
    summary=read(study/'summary.json');audit=read(audit_output/'offline_audit.json');manifest=read(study/'manifest.json')
    if summary['stopped'] or not audit['all_match'] or audit['checked_step_records']!=audit['scheduled_step_records']:raise ValueError('Full sequence report gate failed')
    episodes=read(audit_output/'episode_summaries.json')
    cells=[]
    for resource,pattern,method in __import__('itertools').product(manifest['design']['resources'],manifest['design']['patterns'],manifest['design']['methods']):
        data=[e for e in episodes if (e['resource'],e['pattern'],e['method'])==(resource,pattern,method)]
        completed=[e for e in data if e['sequence_completed']]
        service=[e['event_worst_uncovered'] for e in completed if 'event_worst_uncovered' in e]
        cells.append({'resource':resource,'pattern':pattern,'method':method,'scheduled_sequences':len(data),'complete_sequences':len(completed),
            'conditional_mean_total_travel_m':float(np.mean([e['actual_total_travel_m'] for e in completed])) if completed else None,
            'conditional_mean_event_worst_uncovered':float(np.mean(service)) if service else None,
            'count_switch_events_all_recorded':sum(e['count_switch_events'] for e in data),
            'nontrivial_hold_frames':sum(e['nontrivial_hold_frames'] for e in data),'fallback_frames':sum(e['fallback_frames'] for e in data)})
    service_pairs=defaultdict(dict)
    for e in episodes:service_pairs[e['physical_id'],e['resource'],e['pattern']][e['method']]=e
    joint=[p for p in service_pairs.values() if p['recompute_all']['sequence_completed'] and p['counts_phase_guard']['sequence_completed']
           and 'event_worst_uncovered' in p['recompute_all']]
    service_loss=[np.asarray(p['counts_phase_guard']['event_worst_uncovered'])-p['recompute_all']['event_worst_uncovered'] for p in joint]
    result={'manifest_sha256':digest(study/'manifest.json'),'summary':summary,'audit':audit,'resource_pattern_method_cells':cells,
        'cross_method_original_service_comparison':{'joint_complete_sequences':len(joint),
            'mean_event_worst_gap_difference':float(np.mean(service_loss)) if service_loss else None,
            'maximum_event_worst_gap_difference':float(np.max(service_loss)) if service_loss else None,
            'scope':'empirical comparison with external recompute method; certificate compares own-state greedy, not a different counterfactual history'},
        'reference':'ORIGINAL_WORLD_REFERENCE','general_error_model':'arbitrary bounded independent or correlated person error; observation-certified group identity',
        'scope':str(manifest['design']['steps'])+' accepted observation indices; no reset within sequence; not asymptotic stability or dynamic-crowd proof'}
    output.mkdir(parents=True,exist_ok=False);write_json(output/'results.json',result)
    text=[f"# {manifest['design']['steps']}次静态重观测：数量与相位切换",'',f"记录：{summary['recorded_step_records']}/{summary['scheduled_step_records']}；全部保存轨迹和决策重建一致。",'',
        '每个方法沿自己的实际状态连续推进。普通部署失败结束受影响序列，其余帧明确未执行，不重置、不补抽。', '',
        '门控结果只属于本批冻结源码及声明前提；有限复判不证明支持域包含。当前活动版本和证明修订见docs/STEP1_BUFFER_ENCLOSURE_AUDIT.zh.md。', '',
        '主指标/完整分母界及按资源/扰动模式统计见下方；短路程必须与声明全部更新完成共同解释。', '',
        '```json',json.dumps(result,ensure_ascii=False,indent=2),'```', '',
        '有限序列结果不是任意时间的收敛或受限通信稳定性证明。Step2/3仍需独立动力学与信息年龄条件。']
    (output/'REPORT.zh.md').write_text('\n'.join(text),encoding='utf-8');return result
