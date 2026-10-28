"""Finalize the original frozen confirmation after execution and offline replay."""
import argparse
from collections import Counter, defaultdict
import json
from datetime import datetime
from pathlib import Path

from udc.experiments.static_multigroup import ROOT, digest, write_json, scientific_sources, environment_record
from .service_audit_report import read, records, run as supplementary_report


def finalize():
    output = ROOT / 'reports/static_service_audit_20261026'
    study = ROOT / 'runs/static_multigroup_20261026/confirmation_v2_frozen'
    summary, manifest = read(study / 'summary.json'), read(study / 'manifest.json')
    replay = read(output / 'confirmation_replay.json')
    if replay['mismatches']:
        raise ValueError('Confirmation replay has mismatches; inspect evidence')
    verified = supplementary_report(output / 'confirmation', include_confirmation=True, verify_trajectories=True)
    data = records(study)
    if len(data) != summary['recorded_tasks']:
        raise ValueError('Completed task ledger differs from frozen summary')
    primary = summary['primary_analysis']
    complete = len(data) == len(manifest['tasks']) and not summary['stopped_on_error_or_safety']
    ledger = read(output / 'confirmation/confirmation_full_ledger_bounds.json')
    stats = manifest['spec']['statistics']
    paired = defaultdict(dict)
    for row in data:
        if (row['layout'] == stats['primary_layout'] and row['resource'] == stats['primary_resource']
                and row['error_bound_m'] == stats['primary_error_bound_m']):
            paired[(row['group_count'], row['shape'], row['seed'])][row['allocator']] = row
    secondary = []
    strata = []
    for row in primary['strata']:
        deltas = []
        for seed in manifest['seeds']:
            pair = paired[(row['group_count'], row['shape'], seed)]
            if all(method in pair and pair[method].get('planned_common_service') for method in ('minimax', 'perimeter')):
                deltas.append(pair['minimax']['planned_common_service']['weighted_uncovered']
                              - pair['perimeter']['planned_common_service']['weighted_uncovered'])
        weighted = sum(deltas) / len(deltas) if deltas else None
        secondary.append({'group_count': row['group_count'], 'shape': row['shape'], 'pairs': len(deltas),
                          'weighted_mean_delta': weighted})
        strata.append(f"| {row['group_count']} | {row['shape']} | {row['valid_pairs']}/{row['draws']} | "
                      + (f"{row['mean_delta'] * 100:+.4f}" if row['mean_delta'] is not None else '缺失') + ' | '
                      + (f'{weighted * 100:+.4f}' if weighted is not None else '缺失') + ' |')
    weighted_overall = (sum(r['weighted_mean_delta'] for r in secondary) / len(secondary)
                        if all(r['weighted_mean_delta'] is not None for r in secondary) else None)
    write_json(output / 'confirmation/weighted_secondary.json', {'strata': secondary,
        'equal_stratum_mean_delta': weighted_overall, 'scope': 'secondary planned common-reference total unmet; conditional pairs'})
    weighted_text = f'{weighted_overall * 100:+.4f}' if weighted_overall is not None else '缺失'
    interval = primary['paired_stratified_95_interval']
    interval_text = f'[{interval[0] * 100:+.4f}, {interval[1] * 100:+.4f}]' if interval else '未能计算'
    point_text = f"{primary['equal_stratum_mean_delta'] * 100:+.4f}" if primary['equal_stratum_mean_delta'] is not None else '缺失'
    statuses = '\n'.join(f'| {key} | {value} |' for key, value in sorted(summary['statuses'].items()))
    grouping = defaultdict(list)
    for row in data:
        grouping[(row['group_count'], row['resource'])].append(row)
    admission = []
    for (k, resource), values in sorted(grouping.items()):
        admission.append({'group_count': k, 'resource': resource, 'recorded': len(values),
                          'statuses': dict(Counter(r['status'] for r in values)),
                          'safely_completed': sum(bool(r.get('deployment_completed')) for r in values),
                          'full_success': sum(bool(r.get('full_task_success')) for r in values)})
    write_json(output / 'confirmation/stage_accounting.json', admission)
    decision = ('原预先声明的平均方向得到支持' if primary['supports_preregistered_negative_direction'] and complete
                else '原预先声明的平均方向未得到完整确认支持')
    text = f'''# Step 1 下一轮实验：正式确认与机制评估

报告生成日期：{datetime.now().astimezone().date().isoformat()}。实验周期始于2026-10-26。
所有实验使用本项目独立CPython3.12.15环境。

## 正式确认结论

原v2确认记录 **{len(data):,}/{len(manifest['tasks']):,}** 行；完整执行状态：**{complete}**。
{decision}。原主效应为最差群体需求未覆盖比例的minimax减周长分配：
16个K/shape条件等权均值 **{point_text}个百分点**，分层配对bootstrap95%区间
**{interval_text}个百分点**。负值表示改善，K1的零效应条件保持在原估计量中。

同一主要条件下，总加权缺口的16层等权次要差异为 **{weighted_text}个百分点**，
用于明确公平性与总体服务的代价，不能替换主指标。
该主效应仍是双方存在计划时的条件规划效应，不自动等于实际交付效应。
所有方法按原协议实际运行；完整失败/未执行分母另行报告。
即使总体平均改善，也不宣称每种形状、K或输入均改善。

## 执行与失败账本

| 状态 | 行数 |
|---|---:|
{statuses}

安全部署完成 {summary['deployment_completed']:,}，完整任务成功 {summary['full_successes']:,}；
实际运动任务 {summary['motion_tasks']:,}，完整区间 {summary['executed_intervals']:,}，
记录违反区间 {summary['unsafe_intervals']:,}，尚未记录 {summary['not_yet_recorded']:,}。
程序/安全停止标志 {summary['stopped_on_error_or_safety']}。
这些方法、误差和资源条件共享物理输入；不能将行数或区间数当成独立样本量。
独立物理输入为320个(K,shape,seed)单元，观测960份，每主要条件20个seed。

| K | 形状 | 有效/预定配对 | 最差群缺口差异（百分点） | 加权缺口差异（百分点） |
|---|---|---:|---:|---:|
{chr(10).join(strata)}

分阶段失败与资源分层见[stage_accounting.json](confirmation/stage_accounting.json)。
完整原摘要见[confirmation.json](confirmation/confirmation.json)。

## 完整分母与实际完成的敏感性

原主统计量保留，另使用每个未确定服务比例属于[0,1]的范围给出补充均值界。
计划服务的16层等权界为 `{ledger['planned_equal_stratum_bounds']}`；
安全完成实际服务的对应界为 `{ledger['safely_completed_actual_equal_stratum_bounds']}`。
数值单位为比例，均值界不是置信区间，不是给失败任务虚构已实现的服务值。
范围跨零时，完整分母上的方向仍不能仅凭条件均值确定。
各层缺失数与界见[完整账本](confirmation/confirmation_full_ledger_bounds.json)。

## 新增机制证据与Step 1评价

本轮另完成172条固定动作分解、3,840条机制规划、320条静态扰动，以及保留初始失败的压力实验。
详细结果、全部零差异/退化条件和独立重复数见[开发报告](DEVELOPMENT.zh.md)。

- 弧距/欧氏距、估计/参考几何和采样积分差异会改变方法排名；不能以观察目录最优代替真实参考优势。
- 固定K的机制实验显示大小与群内需求形状影响收益，并存在公平性—总服务的代价。
- 一个1mm、分群与资源数量不变的扰动案例出现1.552m目标RMS跳变；分群稳定不能替代目标切换分析。
- 新初态下20个近墙压力案例均安全完成部分服务，近群20个中19个因分离条件拒绝。

当前Step 1可作为有明确假设、独立评价和完整失败账本的静态研究问题。
若正式平均效应得到支持，其结论限于冻结场景分布及该条件规划指标；
论文还应贡献可计算的误差—服务关系或公平性代价控制。
当前没有任意非凸真实边界的统一覆盖误差界、实际人群影响验证或Step2/3稳定性证明。
下一方法版本应优先改进服务目标与评价的一致性，并约束近等价方案间的目标切换。

## 可复核性

原清单SHA-256：`{digest(study / 'manifest.json')}`。
原封存源码文件、输入、目标、20seeds及科学停止触发条件保留；各运行后端修订另列，独立复判使用原验收函数。
离线复判 {replay['checked']:,} 条、差异 {len(replay['mismatches'])}；
本轮汇总核查 {verified['verified_task_receipts']:,} 个任务receipt、
{verified['trajectories_opened']:,} 份禁用pickle轨迹。
原始数据保留在runs目录，报告复制摘要与凭据，不替换历史原件或旧哈希锁。
开发阶段检查见[开发凭据](validation.json)；全核运行的调度、亲和性与测试检查见
[运行修订报告](../runtime_all_cores_20261027/REPORT.zh.md)。
实验批次存在并发，未以墙钟时间作算法效率对照；未执行预留种子5000–5029。
'''
    amendment_path = output / 'runtime_amendment.json'
    amendment = read(amendment_path) if amendment_path.exists() else None
    if amendment:
        text += ('\n## 用户授权的运行配置修订\n\n'
                 f"按用户全核请求，并行度由{amendment['old_workers']}改为{amendment['new_workers']}；"
                 '每进程数值线程仍为1。保留已完成任务、原科学源码快照、输入、评价与样本设计。'
                 '运行配置已修订，不能声称整个批次始终采用原2进程配置。'
                 '详见[runtime_amendment.json](runtime_amendment.json)。\n')
    backend_amendment_path = output / 'runtime_backend_amendment_20261028.json'
    backend_amendment = read(backend_amendment_path) if backend_amendment_path.exists() else None
    if backend_amendment:
        text += ('\n## 保存与等价计算后端修订\n\n'
                 '2026-10-28按用户要求暂停、核验并存档14,864条完整任务，随后对未完成工作使用独立封存的隐式可见图后端。'
                 '节点、坐标、几何谓词、边权、严格松弛及破同分规则保留；按需计算边并在目标出队后结束。'
                 '10个固定回归案例的完整轨迹/状态/验收逐位或逐值一致，新增记录的后端来源由单元凭据绑定。'
                 '该批次包含显式后端版本修订，不能声称所有行始终使用相同实现；独立复判继续调用原冻结验收函数。'
                 '详见[runtime_backend_amendment_20261028.json](runtime_backend_amendment_20261028.json)。\n')
    final_census = None
    if backend_amendment:
        census_output = output / 'final_path_failure_census'
        if not (census_output / 'summary.json').exists():
            if census_output.exists():
                raise ValueError('Incomplete final failure census requires artifact inspection')
            from udc.analysis.path_failure_census import run as failure_census
            final_census = failure_census(study, None, census_output, 8)
        else:
            final_census = read(census_output / 'summary.json')
        if final_census['cases'] != summary['statuses'].get('PATH_NOT_FOUND', 0):
            raise ValueError('Final failure census does not match the complete task ledger')
        text += ('\n## 完整路径失败的只读几何分类\n\n'
                 f"本批记录的{final_census['cases']}条PATH_NOT_FOUND均已分类，计数为`{final_census['categories']}`。"
                 '分类仅说明当前保守表示与固定串行政策中的几何原因，不是全局物理不可达证明。'
                 '原失败未重跑或删除，详细证据见[最终失败账本](final_path_failure_census/summary.json)。\n')
    target = output / 'REPORT.zh.md'
    if target.exists():
        raise FileExistsError(target)
    target.write_text(text, encoding='utf-8')
    write_json(output / 'completion_receipt.json', {
        'manifest_sha256': digest(study / 'manifest.json'), 'confirmation_complete': complete,
        'scheduled_rows': len(manifest['tasks']), 'recorded_rows': len(data), 'offline_replay': replay,
        'source_analysis_hashes': scientific_sources(), 'environment': environment_record(),
        'report_sha256': digest(target), 'verification': verified, 'runtime_amendment': amendment,
        'backend_amendment': backend_amendment, 'final_path_failure_census': final_census})
    return {'confirmation_complete': complete, 'recorded_rows': len(data), 'primary_delta_pp':
            primary['equal_stratum_mean_delta'] * 100 if primary['equal_stratum_mean_delta'] is not None else None,
            'primary_interval_pp': [v * 100 for v in interval] if interval else None,
            'report': str(target), 'verified': verified}


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(finalize(), indent=2))
