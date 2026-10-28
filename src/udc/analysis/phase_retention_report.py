"""Render measured phase-retention results and declared limitations."""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

import numpy as np

from udc.experiments.static_multigroup import ROOT, digest, write_json
from .phase_retention_analysis import read


def figures(data, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    cells = data['planning_cells']
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for ax, k in zip(axes, (2, 5)):
        for method, label, color in [('continuous_euclidean_greedy', 'Euclidean greedy', '#a65c00'),
                                      ('euclidean_retain_0.002', 'Retain, U budget 0.002', '#006b8f'),
                                      ('euclidean_retain_0.005', 'Retain, U budget 0.005', '#4f7d29')]:
            ys = [np.mean([r['mean_rms_m'] for r in cells if r['group_count'] == k and r['method'] == method
                           and r['perturbation_m'] == eta and r['mean_rms_m'] is not None]) for eta in (.001, .01, .03)]
            ax.plot([1, 10, 30], ys, 'o-', label=label, color=color)
        ax.set_xscale('log')
        ax.set_xlabel('Additional observation perturbation bound (mm)')
        ax.set_ylabel('Mean target matching RMS (m)')
        ax.set_title(f'K = {k}; concave / ellipse; paired base models')
        ax.grid(alpha=.2)
    axes[0].legend(fontsize=8)
    fig.suptitle('Fixed-count static re-observation; shared candidates')
    for extension in ('png', 'svg'):
        fig.savefig(output / ('layout_response.' + extension), dpi=180)
    plt.close(fig)
    pairs = data['motion']['redeployment_pairs']
    complete = [r for r in pairs if r['both_safely_completed']]
    fig, ax = plt.subplots(figsize=(6, 5), constrained_layout=True)
    for k, color in ((2, '#a65c00'), (5, '#006b8f')):
        selected = [r for r in complete if r['group_count'] == k]
        ax.scatter([r['greedy_travel_m'] for r in selected], [r['retained_travel_m'] for r in selected],
                   color=color, label=f'K = {k}', alpha=.7, s=30)
    high = max([r['greedy_travel_m'] for r in complete] + [r['retained_travel_m'] for r in complete] + [1.]) * 1.05
    ax.plot([0, high], [0, high], '--', color='gray', lw=1)
    ax.set_xlim(-.02 * high, high); ax.set_ylim(-.02 * high, high)
    ax.set_xlabel('Greedy reconfiguration travel (m)')
    ax.set_ylabel('Retention reconfiguration travel (m)')
    ax.set_title(f'1 mm re-observation; safely completed pairs {len(complete)}/{len(pairs)}')
    ax.legend(); ax.grid(alpha=.2)
    for extension in ('png', 'svg'):
        fig.savefig(output / ('actual_redeployment.' + extension), dpi=180)
    plt.close(fig)


def render(analysis, study, output):
    analysis, study, output = Path(analysis).resolve(), Path(study).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    data = read(analysis)
    primary = data['primary']
    contrasts = data['contrasts']
    service = []
    for treatment, control in [('continuous_arc_greedy', 'sampled_arc_greedy'),
                               ('continuous_euclidean_greedy', 'continuous_arc_greedy'),
                               ('euclidean_retain_0.002', 'continuous_euclidean_greedy')]:
        chosen = [r for r in contrasts if r['perturbation_m'] == .001 and r['treatment'] == treatment and r['control'] == control]
        valid = [r for r in chosen if r['mean_true_worst_delta'] is not None]
        service.append({'treatment': treatment, 'control': control, 'valid_pairs': sum(r['valid_pairs'] for r in chosen),
                        'mean_true_worst_delta': float(np.mean([r['mean_true_worst_delta'] for r in valid])) if valid else None,
                        'mean_true_weighted_delta': float(np.mean([r['mean_true_weighted_delta'] for r in valid])) if valid else None,
                        'true_worst_loss_above_proxy_budget_count': sum(r['true_worst_loss_above_proxy_budget_count'] or 0 for r in valid),
                        'maximum_true_worst_loss': max((r['maximum_true_worst_loss'] for r in valid), default=None)})
    complete_motion = [r for r in data['motion']['redeployment_pairs'] if r['both_safely_completed']]
    mean_greedy = float(np.mean([r['greedy_travel_m'] for r in complete_motion])) if complete_motion else None
    mean_retained = float(np.mean([r['retained_travel_m'] for r in complete_motion])) if complete_motion else None
    interval = primary['paired_physical_block_bootstrap_95_interval_m']
    cost_by_noise = []
    for eta in (.001, .01, .03):
        selected = [r for r in contrasts if r['perturbation_m'] == eta and r['treatment'] == 'euclidean_retain_0.002'
                    and r['mean_true_worst_delta'] is not None]
        cost_by_noise.append({'perturbation_m': eta, 'valid_pairs': sum(r['valid_pairs'] for r in selected),
            'mean_true_worst_delta': float(np.mean([r['mean_true_worst_delta'] for r in selected])) if selected else None,
            'mean_true_weighted_delta': float(np.mean([r['mean_true_weighted_delta'] for r in selected])) if selected else None,
            'maximum_true_worst_loss': max((r['maximum_true_worst_loss'] for r in selected), default=None),
            'true_loss_above_proxy_budget_count': sum(r['true_worst_loss_above_proxy_budget_count'] for r in selected)})
    raw = [read(path) for path in sorted((study / 'results').glob('*/metrics.json'))]
    tails = {}
    for method in ('continuous_euclidean_greedy', 'euclidean_retain_0.002'):
        values = [r['matched_target_rms_m'] for r in raw if r['kind'] == 'planning' and r['layout'] == method
                  and r['perturbation_m'] == .001 and 'matched_target_rms_m' in r]
        tails[method] = {'views': len(values), 'median_m': float(np.median(values)),
                         'p95_m': float(np.quantile(values, .95)), 'maximum_m': max(values)} if values else None
    def number(value, digits=6):
        return f'{value:.{digits}f}' if value is not None else '缺失'
    stratum_rows = '\n'.join(f"| {r['group_count']} | {r['shape']} | {r['valid_blocks']}/{r['scheduled_blocks']} | "
                    f"{number(r['mean_greedy_rms_m'])} | {number(r['mean_retained_rms_m'])} | {number(r['mean_rms_delta_m'])} |"
                    for r in primary['strata'])
    service_rows = '\n'.join(f"| {r['treatment']} − {r['control']} | {r['valid_pairs']}/160 | "
                   f"{number(r['mean_true_worst_delta'] * 100 if r['mean_true_worst_delta'] is not None else None, 4)} | "
                   f"{number(r['mean_true_weighted_delta'] * 100 if r['mean_true_weighted_delta'] is not None else None, 4)} |"
                   for r in service)
    noise_rows = '\n'.join(f"| {r['perturbation_m'] * 1000:g} | {r['valid_pairs']}/160 | "
                  f"{number(r['mean_true_worst_delta'] * 100 if r['mean_true_worst_delta'] is not None else None, 4)} | "
                  f"{number(r['maximum_true_worst_loss'] * 100 if r['maximum_true_worst_loss'] is not None else None, 4)} | "
                  f"{r['true_loss_above_proxy_budget_count']} |" for r in cost_by_noise)
    status = {kind: value for kind, value in data['motion']['by_kind'].items()}
    replay = read(study / 'offline_replay.json')
    witness_study = ROOT / 'runs/static_phase_retention_20261027/witness_execution_v1'
    witness_execution = read(witness_study / 'summary.json') if (witness_study / 'summary.json').exists() else None
    witness_replay = read(witness_study / 'offline_replay.json') if (witness_study / 'offline_replay.json').exists() else None
    witness_summary_text = (f"{witness_execution['recorded']}/{witness_execution['scheduled']}次记录，"
        f"安全完成标志{witness_execution['both_safely_completed']}，{witness_execution['executed_intervals']}完整区间，"
        f"违反{witness_execution['unsafe_intervals']}，实际真实最差缺口差值{witness_execution['actual_true_worst_delta']}"
        if witness_execution else '尚未运行')
    witness_replay_text = (f"{witness_replay['recomputed_rows']}条，差异{len(witness_replay['mismatches'])}，"
        f"初态逐位相同标志{witness_replay['identical_initial_robot_states']}" if witness_replay else '尚未复判')
    original = read(output / 'original_v2_interim.json') if (output / 'original_v2_interim.json').exists() else None
    original_text = (f"截至{original['captured_at']}的记账快照：{original['recorded_rows']}/{original['scheduled_rows']}条，"
        f"状态{original['statuses']}。路径失败原因{original['path_failure_reasons']}。" if original else '尚无本轮快照。')
    complete = (data['summary']['recorded_rows'] == 3440 and not data['summary']['stopped_on_program_or_safety'])
    manifest_hash = digest(study / 'manifest.json')
    report = f'''# 连续评分与布局保留：静态Step 1实验

日期：{datetime.now(timezone(timedelta(hours=9))).date().isoformat()}（Asia/Tokyo）。
本轮完成状态：**{complete}**。证据类型：独立新输入上的开发方法实验，不是原v2正式确认。

## 主要结果

在预先指定的1mm重观测条件，.002保留策略减连续欧氏贪心的目标匹配RMS，
四层等权平均差为 **{number(primary['equal_stratum_mean_delta_m'])}m**；
按完整物理种子块重采样的95%开发区间为 **{interval}m**。
完整分母敏感性范围为`{primary['full_denominator_mean_bounds_m']}`m，该范围不是置信区间。

| K | 形状 | 有效/预定物理块 | 贪心RMS均值(m) | 保留RMS均值(m) | 差值(m) |
|---|---|---:|---:|---:|---:|
{stratum_rows}

每层20个种子块，两种基线误差先在块内平均；80个独立物理块，160个基线观测。
四档附加扰动/五种方法不作为额外独立样本。所有条件和.005敏感性结果保存在CSV。
统计是开发结果；没有依据结果修改容差、补种子或筛除失败。
160个观测视图的描述性尾部：贪心目标RMS中位数/P95/最大值分别为
`{tails['continuous_euclidean_greedy']}`，保留策略为`{tails['euclidean_retain_0.002']}`。
视图不是160个独立物理输入，尾部分位数不是置信区间。

![布局响应](layout_response.png)

## 连续评分与真实参考服务

下表限定1mm，单位为未覆盖比例的百分点；负值表示改善。
这是在相同数量、同一候选池上的指标比较，不能解释为整个原控制器的比较。
次要指标按声明条件等权；缺失与配对数分别保留，完整详情见[contrasts.csv](analysis/contrasts.csv)。

| 比较 | 有效/预定观测配对 | 最差群缺口差异(pp) | 总加权缺口差异(pp) |
|---|---:|---:|---:|
{service_rows}

保留策略的.002界只适用于当前观察曲线、本群指派服务与有限共享候选。
真实参考曲线、需求映射、跨群欧氏服务均可能使真实差异超过这个观察预算。
在1mm主比较中，真实最差缺口损失超过观察.002预算的配对数为
{service[-1]['true_worst_loss_above_proxy_budget_count']}/160；最大单对真实损失为
{number(service[-1]['maximum_true_worst_loss'] * 100 if service[-1]['maximum_true_worst_loss'] is not None else None, 4)}个百分点。
连续服务也是声明折线/线性密度的float64积分，不是真实人群影响模型或浮点认证。

较大扰动下，保留策略减贪心的规划目标在真实参考边界上的代价如下；
观察预算始终是0.2个百分点。10/30mm只评价规划目标，未执行对应重部署轨迹。

| 附加扰动(mm) | 有效配对 | 最差群缺口平均变化(pp) | 最大单对损失(pp) | 真实损失超过观察预算的配对数 |
|---|---:|---:|---:|---:|
{noise_rows}

这些超出观察预算的真实损失不违反本方法声明的观察模型界，但直接表明该界不足以
充当真实服务保证。保留策略不能独立解决边界误差到真实覆盖的联系。

另对事后选出的最大损失案例K5/凹形/seed2210进行固定动作执行诊断：
完整精度数据见[true_service_witness.json](true_service_witness.json)。
观察最差群损失为0.0018544135533689055，真实参考损失为0.011346670485246846，
实际最大人员定位误差0.10121614242567314m，小于声明0.12m。
两种固定动作从相同名义机器人终态各执行一次，结果：{witness_summary_text}。
这2次动作不加入主试验的独立样本数或成功率；选择是描述性极值，未重新优化动作。
离线见证复判：{witness_replay_text}。

## 实际重部署

名义部署记录：`{status['nominal_deployment']}`。
重部署记录：`{status['redeployment']}`。
完整任务成功数：{sum(v['statuses'].get('FULL_SUCCESS', 0) for v in status.values())}；
安全完成部分服务不能替代完整覆盖成功。
两策略初始机器人状态逐位不一致数：{len(data['motion']['paired_initial_state_mismatches'])}。
实际行驶距离配对有效 **{len(complete_motion)}/80**；安全完成配对子集的贪心均值
**{number(mean_greedy)}m**、保留均值 **{number(mean_retained)}m**。
失败计入完整完成率分母，以上距离均值不代替失败惩罚后的全体效应。

![实际重部署距离](actual_redeployment.png)

这是真实静态人体快照下的一次重观测/一次重部署，未模拟人群运动、行为反馈、
连续观测漂移、分裂合并或通信延迟。人群物理真值在两阶段保持相同。
本轮固定资源数量，不能据此声称已解决资源分配的整数切换。

## 可核查性与边界

原计划3,200条规划、80次共同名义部署、160次实际重部署；记录{data['summary']['recorded_rows']}条。
保留服务界违反{len(data['guard_failures'])}；共享候选/数量不一致{len(data['shared_pool_failures'])}；
连续评分在其声明观察指标上的非劣性质违反{len(data['observed_scoring_dominance_failures'])}；
分群错配{data['group_partition_mismatches']}；最大实际观测误差{number(data['max_actual_observation_error_m'])}m。
核查{data['verification']['verified_receipts']}个任务receipt与禁用pickle轨迹；
离线重构{replay['reconstructed_planning_rows']}条规划、复判{replay['recomputed_motion_rows']}次运动，
不一致{len(replay['mismatches'])}。

清单SHA-256：`{manifest_hash}`。全部输入、协议、源码和环境在运行前封存。
每个方法共用原安全阈值、相位机会及候选，真值不进入控制选择。
协议：[static_phase_retention_v1.yaml](../../configs/step1/static_phase_retention_v1.yaml)；
数学契约：[STEP1_PHASE_RETENTION.zh.md](../../docs/STEP1_PHASE_RETENTION.zh.md)。
数据和源码快照保存在`runs/static_phase_retention_20261027/main_v1`。

该结果用于判断连续评分与有服务预算的布局保留是否值得进入下一版本。
真实参考服务的统一误差界、任意扰动下的目标连续性和动态稳定性仍需单独建立。
原17,280行v2确认继续使用其封存源码，不混入本轮新方法或新数据。

## 原v2确认的过程记账

{original_text}
详见[original_v2_interim.json](original_v2_interim.json)。K5未完成，任务顺序也不是随机抽样，
该快照不作为总体完成率或主效应结论。已经记录的路径失败不能解释成物理不可达证明；
它们说明原串行固定指派/有限图策略的适用限制，不能用本轮低预算成功覆盖掉。
'''
    figures(data, output)
    target = output / 'REPORT.zh.md'
    if target.exists():
        raise FileExistsError(target)
    target.write_text(report, encoding='utf-8')
    result = {'evidence': 'development_new_method', 'complete': complete,
              'primary': primary, 'service_at_1mm': service,
              'service_cost_by_noise': cost_by_noise, 'rms_view_quantiles_at_1mm': tails,
              'actual_redeployment_valid_pairs': len(complete_motion),
              'mean_actual_greedy_travel_m': mean_greedy, 'mean_actual_retained_travel_m': mean_retained,
              'motion': status, 'replay': replay, 'report_sha256': digest(target), 'manifest_sha256': manifest_hash}
    result['selected_witness_execution'] = witness_execution
    result['selected_witness_replay'] = witness_replay
    write_json(output / 'results.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', required=True); parser.add_argument('--study', required=True); parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = render(args.analysis, args.study, args.output)
    print(json.dumps({k: v for k, v in result.items() if k not in ('primary', 'service_at_1mm')}, indent=2))
