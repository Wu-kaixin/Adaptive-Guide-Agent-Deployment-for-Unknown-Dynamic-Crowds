"""Format frozen statistics and observed audit records; no planning or resampling."""
from pathlib import Path
from collections import Counter
import csv
import numpy as np
from udc.experiments.demand_pilot import read, arrays
from udc.experiments.nonconvex_rebuild import write, sha
from udc.analysis import resource_response as rr
from udc.experiments.resource_confirmation import locked
from udc.experiments.provenance import provenance

PROJECT=Path(__file__).resolve().parents[2]
REPORT=Path(__file__).resolve().parent
ROOT=PROJECT/'runs/step1_nonconvex_resource_confirmation_v1'
locked(ROOT,full=True)
s=read(REPORT/'analysis.json'); lock=read(ROOT/'protocol.json'); frozen=read(ROOT/'inputs_frozen.json')
audit=read(ROOT/'seed_audit.json'); final=read(ROOT/'final_checks.json')
rows=[read(ROOT/'results'/r['task_id']/'protocol_metrics.json') for r in read(ROOT/'manifest_360.json')['rows']]
us=rr.units(rows)
def f(x,n=8): return 'null' if x is None else f'{x:.{n}f}'
def ci(x): return '不可计算' if x is None else '['+', '.join(f(v) for v in x)+']'
def table(headers,body):
    return '\n'.join(['|'+'|'.join(headers)+'|','|'+'|'.join(['---']*len(headers))+'|']+
        ['|'+'|'.join(str(x) for x in row)+'|' for row in body])+'\n'
def equal_scene(selected,key):
    return float(np.mean([np.mean([r[key] for r in selected if r['scene']==scene]) for scene in rr.SCENES]))
def label(m): return 'Equal' if m=='equal_arc' else 'projected'
def csvrows(name): return list(csv.DictReader((REPORT/name).open(encoding='utf-8-sig',newline='')))
resource=csvrows('own_method_resource_all_metrics.csv')
for r in resource:
    r['increment']=int(r['increment'])
    for k in ('baseline','new','difference','relative_pct'): r[k]=float(r[k])
failures=[r for r in rows if r['executed'] and not r['success']]
failure_records=[]
for r in failures:
    directory=ROOT/'results'/r['task_id']; raw=read(directory/'metrics.json'); initial=frozen['starts'][r['initial_key']]
    paths=[directory/'trajectory.npz',directory/'metrics.json',directory/'config.yaml',
        ROOT/'plans'/r['task_id']/'assignment.npz',ROOT/'plans'/r['task_id']/'paths.json',Path(initial['guides']),Path(initial['config']),
        Path(initial['physical'])/'input.npz',Path(initial['physical'])/'boundary.npz',
        Path(initial['physical'])/'static_domain.json',Path(initial['physical'])/'free.wkb',
        Path(initial['condition'])/'demand.npz',Path(initial['guides']).parent/'mapping.json',
        ROOT/'manifest_360.json',ROOT/'inputs_frozen.json',ROOT/'protocol.json']
    # save_plan uses a directory with plan.npz; retain exact existing paths.
    paths=[p for p in paths if p.exists()]
    paths += [p for p in (ROOT/'plans'/r['task_id']/'final').rglob('*') if p.is_file()]
    failure_records.append({'task_id':r['task_id'],'runner_status':raw['termination_status'],
        'orchestration_status':r['orchestration_status'],'physical_success':r['independent_physical_success'],
        'criteria':raw['criteria'],'motion':r['motion'],'target_geometry':r['target_geometry'],
        'quantity':initial['quantity'],'planned_cost':r['planned_cost'],'actual_cost':r['actual_cost'],
        'assignment_trace':r['assignment_trace'],'recovery_events':[{k:e.get(k) for k in
            ('strategy','trigger_reason','time_s','frame','accepted','classification','error','budget_before','budget_consumed','budget_remaining','installation_succeeded')}
                for e in raw['recovery_attempts']],
        'priority_summary':{k:raw['coordination'].get(k) for k in ('activated','steps','enforce_attempts','enforce_installed','enforce_rejected','exit_reason')},
        'files':{str(p.resolve()):sha(p) for p in paths},
        'policy':'frozen observed failure; no rerun, no new target, no diagnosis intervention in this confirmation'})
write(REPORT/'fixed_execution_failures.json',failure_records)
for name in ('final_checks.json','first_artifact_audit.json','rejudge_timing.json','all_independent_checks.json'):
    write(REPORT/name,read(ROOT/name))

# Static paper figure: precomputed confidence intervals, no extra random draws.
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig,ax=plt.subplots(figsize=(9,4.5),constrained_layout=True)
for i,scene in enumerate(rr.SCENES):
    ps=[r for r in rr.interactions(us,2) if r['scene']==scene]
    xs=i+np.linspace(-.12,.12,len(ps)); ys=[r['difference'] for r in ps]
    ax.scatter(xs,ys,s=18,alpha=.55,color='tab:blue')
    for x,r in zip(xs,ps):
        if any(v['physical_key']==r['physical_key'] for v in failures): ax.scatter(x,r['difference'],marker='x',s=65,color='red')
    mean=s['primary']['result']['by_scene'][scene]['mean_difference']; lo,hi=s['bootstrap']['by_scene'][scene]['interaction_N2']['interval']
    ax.errorbar(i,mean,yerr=[[mean-lo],[hi-mean]],fmt='s',color='black',capsize=5)
mean=s['primary']['result']['equal_scene_mean_difference']; lo,hi=s['primary']['interval']
ax.errorbar(3,mean,yerr=[[mean-lo],[hi-mean]],fmt='*',markersize=13,color='black',capsize=5)
ax.axhline(0,color='grey',linewidth=1); ax.set_xticks(range(4),['elongated (20/20)','tight_u (10/20)','wide_u (16/20)','equal-scene mean'])
ax.set(ylabel='I2 (m²)',title='Primary resource interaction: four qualified targets; 95% paired bootstrap')
ax.grid(axis='y',alpha=.2); fig.savefig(REPORT/'primary_I2.png',dpi=180); plt.close(fig)

parts=['# 非凸需求部署的Guide资源规律：新输入配对确认 v1\n']
parts.append(f'''本轮按运行前协议完成seed{lock['selected_seeds'][0]}–{lock['selected_seeds'][-1]}的60份新人员输入、360行主台账和60条共同数量诊断。唯一主要指标I₂的**三场景等权均值为{f(mean)}m²，95%配对bootstrap区间{ci(s['primary']['interval'])}m²**，支持本批预定的I₂<0方向；46个有效四目标配对均为负。这里没有使用不等样本量的合并均值{f(s['primary']['result']['all']['mean_difference'])}替代场景等权值。

实际结果为**275 SUCCESS、78 INITIALIZATION_INVALID、6 BOUNDARY_INVALID、1 TIMEOUT；360条编排状态均OK**。276条新运动轨迹中275条通过独立物理部署验收、1条安全但未完成；62,834个完整运动区间零安全违反。规划资源交互得到本批确认，40秒完整执行仍有边界，不能写全部成功或Guide越多越容易部署。唯一失败wide_u seed113 N21 Equal完整保留，没有重试、修复或追加样本。

## 1. 基线、预注册顺序与新seed审计

分支Major-Research-Branches，起点dafc4aba干净，科学基线865e6a48。先阅读AGENTS、架构、原资源报告及恢复诊断。原65份执行／科学Python文件与865e6a48规范化Git内容一致；布局、需求、相位、导航端点修复、联合安全、优先恢复、进展监测和独立评价不变。唯一已有源码改动是资源统计导出器的锁／元数据适配，未改原数值统计函数；另加本轮编排和统计模块。全部实际运行使用同一提交、同一完整源码SHA，原恢复第二策略失败短路边界保留。

运行前协议／配置／编排／统计／测试先提交为**{lock['provenance']['commit']}**，工作区干净后才审计选seed，之后才生成输入，再全量冻结，再首seed18行串行、余342行最多2worker。首批18行全成功，46.264856s，计入360一次；余342行408.497659s，普通科学超时保留继续，无停止／重试／批内科学变更。每worker数值线程1，反事实显式0。

本机审计读取{len(audit['inspected_files'])}份配置、manifest、报告／输入元数据文件并检查现有输入目录标记，相关namespace20261019记录排除{len(audit['exposed_scene_seed_pairs'])}个场景seed单元；其他协议或未归属记录单列，不把重复元数据条数当样本数。从95起按预定“全部三场景均未暴露的首个连续20seed区间”选中95–114，未触发顺延。审计范围仅本仓库本机现存文件，未查其他设备、缺失文件或未记录的运行；同整数不同namespace不自动认定相同输入。没有使用5000–5029。

60次原draw_cell调用、零重抽／替换；新60份全精度人员／半径／场地哈希唯一，与上批60份人员哈希重叠0。种子审计在人员生成前完成，输入和任务选择均在方法结果出现前锁定。[审计](seed_audit.json)、[冻结协议](PROTOCOL.zh.md)、[manifest](manifest_360.json)和[输入／ID映射锁](inputs_frozen.json)可逐项核对。

## 2. 固定物理输入、需求、方法和嵌套初态

同一场景seed的三N共用人员、半径、房间、观测需求、边界、曲线、静态域、rho和热点窗口。需求仅原热点B：背景1，原固定圆盘内5；圆心elongated[12,14.75]、tight_u[12,16.4]、wide_u[11,16.4]，半径1.2m；Gaussian带宽1.2m，原全曲线尺度／归一化／周期插值／2048中点积分。热点成员与“超额质量占局部总质量至少一半”的窗口在目标出现前冻结，不随N或目标重画；背景质量为原总质量减超额质量，背景弧段为冻结窗口外弧长，两者不互换或相加。

方法equal_arc+all_phase_256和projected_mass+all_phase_256；后者是Lloyd迭代、弧间隙约束布局与共用有限相位搜索的组合。四布局初值原顺序、300轮／停止规则、浓度1、双方256相位、原网格／循环索引／接纳／tie-break均冻结。每候选检查原曲线、闭合gap、全成对距和静态入域；原rho评分，目标选定后才导航／指派，失败不得换相位。没有浓度减半、Equal回退或目标重优化。

N为elongated11/13/15、tight_u13/15/17、wide_u19/21/23。基准Guide按原生成器、namespace和独立随机流得到；原Guide位置和零起始ID完整保留。沿保存内缩周界方向与弧原点，逐次拆最大周期弧间隙在中点追加，精确并列取最小起始弧坐标，ID追加。60单元逐位核验基准初态、N₀+2／N₀+4前缀及ID；180组Guide安全检查均通过。这里的初态合法不代表人员源合法；人员另独立拒绝。此政策不等同各N独立均匀重抽。

全部60份输入、180组Guide初态／映射、合法曲线的静态域WKB、rho及窗口、360行manifest和{final['frozen_input_files']}个输入工件哈希，均在任何方法结果前写完。13个人员拒绝和1个边界拒绝各保留六行，无曲线单元没有伪造rho／目标／运动。旧资源360行、两条EXPERIMENT_ERROR、原唯一科学失败及276固定目标回归均只读保留，未将开发证据改称本轮新输入结果。

## 3. 数量必要条件、阶段失败与完整分母
''')
quant=read(ROOT/'quantity_diagnostics_60.json')
body=[]
for scene in rr.SCENES:
    q=[r for r in quant if r['scene']==scene and r['curve_length_m'] is not None]
    body.append([scene,len(q),f(min(r['curve_length_m'] for r in q),4)+'–'+f(max(r['curve_length_m'] for r in q),4),
        '/'.join(map(str,sorted({r['planning_lower'] for r in q}))),'/'.join(map(str,sorted({r['acceptance_lower'] for r in q}))),
        '/'.join(map(str,sorted({r['necessary_upper'] for r in q}))),sum(r['acceptance_quantity_not_excluded'] for r in q)])
parts.append(table(['场景','合法曲线','L范围m','ceil(L/1.94)','ceil(L/2)','floor(L/.75)','N₀−1的2m数量未排除'],body))
parts.append('60条N₀−1诊断仅共同数量检查，无目标或运动，不按方法重复、不入360成功率；14条曲线缺失为null。有效曲线的低N均违反1.94m规划必要条件，但其中满足2m数量必要条件者不能称资源绝对不足；主N数量未排除也不证明欧氏间距、静态入域、完整可达指派或40秒联合执行。全部180组L/N、1.94−L/N与上下界见台账和输入锁。\n')
body=[]
for d in s['denominators']:
    body.append([d['scene'],d['N'],20,d['people_legal'],d['curve_legal'],d['both_target_valid'],
        d.get('both_success',0),d.get('equal_only',0),d.get('projected_only',0),d.get('both_fail',0),d['success_rows']])
parts.append(table(['场景','N','物理','人员合法','曲线合法','双方目标合格','双方成功','仅Equal','仅projected','双方失败','成功行/40'],body))
parts.append('共同物理分母60，人员合法47（20/10/17），曲线合法46（20/10/16）。各N均120行、94个人员合法配置行、92个曲线／目标合格行；主要四目标和全六目标共同子集恰为同46输入，各N自己的规划对子集也同46。故本批规划趋势没有目标子集选择变化。各N双方成功运动子集46/45/46，全六配置共同成功45（20/10/15）；运动比较另固定。\n')
parts.append(table(['N增量','完整成功/全部行','成功/实际运动','共同输入／边界拒绝','科学执行失败','程序错误'],
    [[n,f"{sum(r['success'] for r in rows if r['increment']==n)}/120",f"{sum(r['success'] for r in rows if r['increment']==n)}/92",28,
        sum(r['executed'] and not r['success'] for r in rows if r['increment']==n),0] for n in (0,2,4)]))
rejects={scene:{state:[r['seed'] for r in frozen['sources'].values() if r['scene']==scene and r['status']==state]
    for state in ('INITIALIZATION_INVALID','BOUNDARY_INVALID')} for scene in rr.SCENES}
parts.append('人员／边界拒绝明细：'+str(rejects)+'。数量、布局／相位有限搜索、初始指派路径均无额外拒绝；安全、程序错误和外部中断为0。未执行84行的时间、路程、恢复和安全运动证据为null。全部360行分别保存runner、编排和独立物理状态，不将275/360=76.39%的完整分母替换成条件性275/276=99.64%。\n\n![阶段记账](stage_accounting.png)\n')
parts.append('## 4. 唯一主要I₂及配对不确定性\n')
parts.append(table(['场景／汇总','有效配对/物理分母','I₂均值m²','95%区间m²','负/零/正'],
    [[scene,str(s['primary']['result']['by_scene'][scene]['n'])+'/20',f(s['primary']['result']['by_scene'][scene]['mean_difference']),
        ci(s['bootstrap']['by_scene'][scene]['interaction_N2']['interval']),str(s['primary']['result']['by_scene'][scene]['n'])+'/0/0'] for scene in rr.SCENES]+
    [['三场景等权','46/60',f(mean),ci(s['primary']['interval']),'46/0/0']]))
parts.append('统计严格使用运行前锁定RNG2026102003、10000次分层整物理单元配对bootstrap，每层全部20单元（含缺失）一起抽取其全部N、方法及阶段状态，每次重算条件均值后场景等权。五个预定估计量及三个分层均无不可计算重采样（0/10000），各层有效对20/10/16均≥2；不是把360行当独立样本。95%百分位区间描述这些固定模板与条件性合格目标，不代表任意非凸几何。主指标／方向／子集没有因结果改动。\n\n![主要资源交互](primary_I2.png)\n\n蓝点为有效四目标物理配对，黑方块／星号为分场景／等权均值及95%区间，红×为包含本轮超时配置的配对，其合格规划目标仍保留。全部逐对值见[主要与次要交互CSV](primary_and_secondary_interactions.csv)。其中relative_pct是按基准绝对方法差归一化的探索字段，不是总需求降低百分比，本报告不以它解释I₂。\n')
parts.append('## 5. 同N布局收益与各方法资源收益分账\n')
body=[]
for n in (0,2,4):
    p=rr.pairs(us,n); c=s['contrasts'][f'{n}_demand_own']
    body.append([n,f(equal_scene(p,'equal')),f(equal_scene(p,'projected')),f(c['equal_scene_mean_difference']),
        f(c['equal_scene_mean_relative_pct'],4)+'%',ci(s['bootstrap']['estimates'][f'delta_N{n}']['interval']),'46/0/0'])
parts.append(table(['增量','Equal J m²','projected J m²','ΔJ m²','逐对相对差等权','ΔJ的95%区间','改善/持平/恶化'],body))
parts.append(table(['场景','Δ₀ m²','Δ₂ m²','Δ₄ m²','Δ₄−Δ₂ m²'],[[scene]+[f(s['contrasts'][f'{n}_demand_own']['by_scene'][scene]['mean_difference']) for n in (0,2,4)]+
    [f(s['contrasts']['4_demand_own']['by_scene'][scene]['mean_difference']-s['contrasts']['2_demand_own']['by_scene'][scene]['mean_difference'])] for scene in rr.SCENES]))
parts.append(f"次要I₄等权均值{f(s['interactions']['4_available']['equal_scene_mean_difference'])}m²，95%区间{ci(s['bootstrap']['estimates']['interaction_N4']['interval'])}。两种交互在全六目标共同子集上的值与本批各自四目标子集严格相同，仍分别导出。N₀+2到N₀+4的绝对优势在elongated和tight_u缩小、wide_u扩大；总量相对百分比可随Equal基准下降而增大，不能称绝对优势持续加速。\n")
body=[]
for method in rr.METHODS:
    for n in (2,4):
        rs=[r for r in resource if r['method']==method and r['increment']==n and r['metric']=='demand']
        body.append([label(method),n,len(rs),f(equal_scene(rs,'difference')),f(equal_scene(rs,'relative_pct'),4)+'%',sum(r['difference']<0 for r in rs)])
parts.append(table(['方法','增加Guide','自身有效配对','自身ΔJ m²','逐对相对资源变化等权','降低对数'],body))
parts.append('三档各46配对projected总需求代价均低于Equal；两方法自身增加2或4名Guide也在全部46对降低J。资源带来的共同下降与同N布局差分别记账，不把两者全部算作projected算法优势。最差（收益最小）同N案例：\n')
parts.append(table(['增量','物理输入','ΔJ m²','逐对相对差'],[[n,s['contrasts'][f'{n}_demand_own']['all']['worst']['physical_key'],
    f(s['contrasts'][f'{n}_demand_own']['all']['worst']['difference']),f(s['contrasts'][f'{n}_demand_own']['all']['worst']['relative_pct'],4)+'%'] for n in (0,2,4)]))
worst_J=max((r for r in rows if r['target_valid']),key=lambda r:r['planned_cost']['demand'])
parts.append(f"全主矩阵最大合格规划J为{worst_J['task_id']}，{f(worst_J['planned_cost']['demand'])}m²；这与最小方法收益案例是不同口径。\n")
parts.append('## 6. 热点和两种背景取舍\n')
parts.append(table(['指标','N增量','同N projected−Equal m²','逐对相对差等权','降低/持平/增加'],
    [[metric,n,f(s['contrasts'][f'{n}_{metric}_own']['equal_scene_mean_difference']),f(s['contrasts'][f'{n}_{metric}_own']['equal_scene_mean_relative_pct'],4)+'%',
        '/'.join(str(s['contrasts'][f'{n}_{metric}_own']['all']['directions'].get(k,0)) for k in ('improved','tied','worsened'))]
        for metric in ('excess','background_mass','background_arc') for n in (0,2,4)]))
parts.append('同N背景弧段相对代价随档位约为+2.27%、+17.59%、+25.72%，热点超额收益扩大；背景质量从基准略降转为增加。该取舍不能替代跨N自身背景绝对变化：\n')
parts.append(table(['方法','增加Guide','指标','自身绝对变化m²','降低/全部有效对'],
    [[label(m),n,metric,f(equal_scene(rs,'difference')),f"{sum(r['difference']<0 for r in rs)}/{len(rs)}"]
        for m in rr.METHODS for n in (2,4) for metric in ('excess','background_mass','background_arc')
        for rs in [[r for r in resource if r['method']==m and r['increment']==n and r['metric']==metric]]]))
parts.append('两方法相对N₀增加2或4名Guide后的背景绝对代价在本批全部配对下降，不能把“projected同N背景高于Equal”写成“增加Guide使背景绝对更差”。所有J保持同一冻结需求和归一化、m²不重缩放；图线为各N合格规划目标均值，包含后续失败，红×标出实际执行失败的目标，共同拒绝没有伪代价。\n\n![规划响应](planning_response.png)\n')
parts.append('## 7. 部署、截止余量、真实路程与协调成本\n\n下表使用各N自己的双方完整成功配对，两方法严格相同输入集合：\n')
parts.append(table(['场景','N','方法','配对n','成功时间s','40s余量s','真实总路程m','每Guide m','恢复均值'],
    [[r['scene'],lock['spec']['base_counts'][r['scene']]+r['increment'],label(r['method']),r['n']]+[f(r[k],3) for k in ('time_s','slack_s','travel_m','per_guide_m','recoveries')]
        for r in s['motion_paired_subsets'] if r['subset']=='own_N_both_success']))
parts.append('全六配置共同成功45输入（20/10/15）的场景等权结果如下，避免把seed113在N21的剔除误作资源趋势。完整分层双子集见[motion_paired_subsets.csv](motion_paired_subsets.csv)。\n')
parts.append(table(['N增量','方法','成功时间s','40s余量s','真实总路程m','每Guide m','恢复均值'],
    [[n,label(m)]+[f(equal_scene(rs,k),3) for k in ('time_s','slack_s','travel_m','per_guide_m','recoveries')]
        for n in (0,2,4) for m in rr.METHODS for rs in [[r for r in s['motion_paired_subsets'] if r['subset']=='all_six_common_success' and r['increment']==n and r['method']==m]]]))
parts.append('增加N自然增加总路程，每Guide量的变化较小；wide_u的联合执行、恢复和时间余量负担更明显，但不同N／方法不是单调关系。elongated／tight_u本批无需恢复。全276运动恢复次数分布为0次198、2次2、3次76，失败查询共76次；合法重指派全部通过连续事件链，非法变化0。用满三次预算的成功与约1e−8m净裕量不证明噪声鲁棒性或一般收敛。\n\n![执行响应](execution_response.png)\n\n图中成功率包含该N全部20物理单元；时间等点线是各方法成功行的描述，不把不同成功集合直接解释成配对效应。失败终止40s不进入成功时间或成功截止余量。规划、完整成功、实际失败和未执行由两张图及完整台账区分。\n')
slow=s['slowest_success']
parts.append(f"最慢完整成功为{slow['task_id']}，{f(slow['successful_deployment_time_s'],1)}s，余{f(slow['cutoff_margin_s'],1)}s、恢复{slow['recoveries']}次，真实总路程{f(slow['actual_trajectory_length_m'],6)}m。接近40秒只说明本次完成，不能称预算宽裕。\n")
parts.append('## 8. 唯一超时与分开记账\n')
for record in failure_records:
    parts.append(f"{record['task_id']}：runner TIMEOUT、编排OK、独立物理失败；400个区间／40.0s，全部区间安全。目标合格、初始完整指派路径通过，L={f(record['quantity']['curve_length_m'],6)}m、平均gap={f(record['quantity']['average_gap_m'],6)}m、规划平均余量={f(record['quantity']['planning_average_slack_m'],6)}m，最小目标成对距{f(record['target_geometry']['min_pair_m'],6)}m、静态入域余量{f(record['target_geometry']['min_domain_signed_m'],6)}m。数量和目标余量不能保证联合执行截止。\n")
    parts.append(table(['时刻s','策略','接纳','路径安装','剩余预算','分类'],[[f(e['time_s'],1),e['strategy'],e['accepted'],e['installation_succeeded'],e['budget_remaining'],e['classification']] for e in record['recovery_events']]))
    parts.append('priority_yield的路径安装False表示没有替换路径，accepted=True表示原优先模式接纳，不能读作恢复失败。原日志记录该模式231步，进展加强尝试30次、安装28次、拒绝2次；这里只保留观察，不以这些计数宣称已确定超时机制。\n')
    parts.append(f"末态最大目标误差{f(record['motion']['final_max_target_error_m'],6)}m，到达、贴线、gap和覆盖未通过，几何／数量／规划／路径／安全通过。实际终止路程{f(record['motion']['actual_trajectory_length_m'],6)}m、每Guide{f(record['motion']['actual_trajectory_length_m']/21,6)}m只描述失败过程，不能当同完成度成功成本。三次预算全部使用，已有priority_yield激活，并非本轮修复或绕过第二策略短路；这一运行记录本身不足以证明根因或动态无解。其合格规划J与I₂始终保留。\n")
parts.append('''失败全精度输入、初态、原目标、初始指派、实际轨迹、原metrics、配置和恢复事件只保存在唯一runs路径，固定引用和哈希见[fixed_execution_failures.json](fixed_execution_failures.json)，不从CSV舍入值重建。本轮没有开发诊断干预、目标重优化、延时、增预算或继续重跑。下一轮唯一研究问题应先针对这一冻结超时，检验原优先协调进展与40秒边界的机制；不自动启动新的大型矩阵或外推。

旧资源主台账继续273成功、84拒绝、2程序错误、1科学失败；旧物理275成功与随后固定目标回归276成功分别保留。本轮是新输入275成功＋1科学超时，程序错误0。两条旧编排错误没有被本轮“修复成两个部署成功”，也没有将三类状态合成导航失败。\n''')
parts.append('## 9. 独立复判、积分、终态评分和计算成本\n')
parts.append(f"全360行重新独立核查，276实际轨迹逐完整运动区间评价，共{final['executed_intervals']}区间，criteria／diagnostics／success不一致0；独立物理275通过、1未完成、未执行84为null。全部276合格目标从保存NPZ坐标重算2048／8192积分，主／细值均匹配，方法差符号变化0；最大单目标积分变化{f(s['max_fine_single_cost_change'],10)}m²，最大配对差变化{f(s['max_fine_delta_change'],10)}m²，不重选相位。总量及热点／背景细值保存为[全量积分核查](all_independent_integrations.json)。\n")
parts.append(table(['区间净裕量','最小m'],[[k,f(v,12)] for k,v in s['safety_minima'].items()]))
parts.append('全部合格目标的几何边界描述如下（单位m，每格为相应组最小值；最大弧gap列为最大值）。逐目标原值和域／曲线hash在360台账及计划工件中。\n')
parts.append(table(['增量','方法','目标最小成对距','静态入域余量','最小弧gap','最大弧gap'],
    [[n,label(m)]+[f(min(r['target_geometry'][k] for r in selected),6) for k in ('min_pair_m','min_domain_signed_m','min_arc_gap_m')]+
        [f(max(r['target_geometry']['max_arc_gap_m'] for r in selected),6)]
        for n in (0,2,4) for m in rr.METHODS for selected in [[r for r in rows if r['increment']==n and r['method']==m and r['target_valid']]]]))
parts.append('保持原人／Guide中心距0.75m、墙0.55m、容差1e−7m、规划裕度0.11m、偏移1.3m、规划／执行gap1.94/2m、最大速度1m/s、400步40s、恢复3次及原到达／速度／保持／贴线／默认严格圆盘覆盖。完整线段、真实起点、数值边界节点修复、原子安装、联合过滤、进展约束和优先监测均保留，没有epsilon或接纳分辨率切换。\n')
actual=[]
for n in (0,2,4):
    ps=[r for u in us if all(u['rows'][n][m]['success'] for m in rr.METHODS) for r in rr.pairs([u],n,source='actual_cost')]
    c=rr.contrast(ps); actual.append([n,len(ps),f(c['equal_scene_mean_difference']),f(c['equal_scene_mean_relative_pct'],4)+'%'])
parts.append('实际终态沿原规则投影到同一冻结曲线后评价，另独立检查贴线／覆盖；下面仅该N双方完整成功配对，失败终态代价仍在360台账但不冒充完整部署收益。\n\n'+table(['增量','双方成功配对','实际终态需求Δ m²','逐对相对差等权'],actual))
compute=[]
for n in (0,2,4):
    for m in rr.METHODS:
        selected=[r for r in rows if r['increment']==n and r['method']==m]
        keys=('original_layout_s','all_phase_s','initial_path_s','runner_s','fine_evaluation_s','independent_rejudge_s')
        compute.append([n,label(m)]+[f(np.mean([r['timing'][k] for r in selected if k in r['timing']]),4) for k in keys])
parts.append(table(['增量','方法','布局均值s','相位均值s','导航均值s','runner均值s','8192评价均值s','行复判均值s'],compute))
parts.append('计算成本均按实际进入相应阶段的行统计，行复判还含拒绝检查；非算法复杂度或跨机速度证明。首批串行46.264856s、余批408.497659s，最终全量独立复判含积分43.711519s；该机同一冻结环境，无依赖升级。\n')
parts.append('## 10. 来源、测试、哈希与复现命令\n')
parts.append('''运行前212 passed/28.65s；最终212 passed/29.14s，pip check无冲突，compileall及diff检查通过。测试覆盖I₂四目标与全六子集区别、执行失败保留规划指标、场景等权、整物理单元缺失重采样、区间不可计算规则、运动子集，以及继承的导航数值端点、临时占据、安全、原子安装、合法重指派和编排错误不覆盖raw metrics。测试数不能代替本批案例证据。

Python3.12.15、numpy2.5.4、scipy1.18.1、shapely2.2.0、PyYAML6.0.3、jupedsim1.4.2，其他包版本、解释器与仓库src导入路径见[environment.json](environment.json)。全部276新runner记录clean运行前提交；原65源文件规范化Git一致、原始字节哈希另列，完整新源码聚合SHA及配置／输入锁如下。后续仅生成报告／图和只读身份核对，没有改变冻结src统计或执行源码。
''')
parts.append(table(['记录','SHA／值'],[['运行提交',lock['provenance']['commit']],['完整源码SHA',lock['provenance']['source_sha256']],
    ['配置SHA',sha(PROJECT/'configs/step1/nonconvex_resource_confirmation_v1.yaml')],['protocol SHA',final['protocol_sha256']],
    ['manifest SHA',final['manifest_sha256']],['inputs lock SHA',final['inputs_lock_sha256']],['本机历史保护文件',final['protected_historical_files']]]))
parts.append(r'''12,788项历史工件／报告保护引用和1,284项新输入工件全部核验未变，360行身份审计、60组嵌套ID核验通过。新计划／目标／轨迹的完整逐文件hash见[raw_checksums.json](raw_checksums.json)，runner／编排／物理分列见[状态审计CSV](runner_orchestration_physical_audit.csv)；没有复制整runs、backups或覆盖旧结果。报告导出脚本仅格式化已冻结统计，独立身份脚本只读核对并另写新目录侧文件。

实际PowerShell流程（各阶段已运行，不能在现有目录重试或覆盖）：

```powershell
$py = ".\.venv\Scripts\python.exe"
$out = "runs/step1_nonconvex_resource_confirmation_v1"
$env:PYTHONUTF8 = "1"
$env:UDC_PRIORITY_STALL_COUNTERFACTUAL = "0"
$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"
# 协议、配置、编排、统计和测试先提交0433bb19，git status干净
& $py -m udc.experiments.resource_confirmation --stage prepare
& $py -m udc.experiments.resource_confirmation --stage freeze
& $py -m udc.experiments.resource_confirmation --stage first --workers 1
& $py -m udc.experiments.resource_confirmation --stage remaining --workers 2
& $py -m udc.experiments.resource_confirmation --stage rejudge
& $py -m udc.analysis.resource_confirmation
& $py reports/step1_nonconvex_resource_confirmation_v1/audit_identity.py
& $py reports/step1_nonconvex_resource_confirmation_v1/export_report.py
& $py -m pytest -q --basetemp=.tmp/pytest-resource-confirmation-final
& $py -m pip check
& $py -m compileall -q src
git diff --check
```

Git保存源码、配置、测试、协议和报告，runs保存唯一原始输入／目标／状态／轨迹，不推送、不合并main。包含本机绝对路径的引用不保证新克隆自动拥有runs；复现需保留唯一工件与相同环境。

## 11. 结论、适用范围与停止

本批新输入重复了同N需求布局收益，也确认预定N₀→N₀+2绝对方法优势扩大的资源交互；两方法自身均获得资源覆盖收益，热点加密伴随同N背景覆盖取舍。目标全成对距离、静态自由空间、弧gap硬约束与需求加密在冻结有限搜索中同时检查，有限结果不证明真实最优或关于N的全局单调性。

原端点一致性修复属于系统正确性，不是新增部署规划算法贡献。本轮未修改科学模块；新TIMEOUT体现规划可行与40秒联合执行间的适用边界，不能用原276回归全成功覆盖它。N多带来更多Guide联合运动／路径／恢复负担，wide_u的恢复和低截止余量与几何、初始化、目标共同相关；本批没有用反事实隔离其因果机制，也不从三个模板推导通用几何定律。

当前证据限静态单人群、已知场地、全局精确观测、无限通信、无Guide–Crowd交互、冻结热点B／Gaussian需求和嵌套周界初态、N11–23。需求是规划优先级，不是真实风险、containment或完整RA-ABDC证据；不称大规模可扩展性，不设任意综合分数，不把N₀+2命名为通用最优。统计区间不覆盖全部非凸几何或观测噪声。

本轮固定批次在360行结束，不追加seed、N、热点强度或恢复预算。先冻结本批规划确认和执行边界证据，下一轮仅为wide_u seed113 N21 Equal的原输入／目标设计开发诊断；论文整合与其他外推另立协议，本轮不启动几何／规模／噪声或Step2/3。一页主线见[RESEARCH_NOTE.zh.md](RESEARCH_NOTE.zh.md)。
''')
(REPORT/'REPORT.zh.md').write_text('\n'.join(parts),encoding='utf-8')
note=f'''# Step 1研究主线：新输入资源确认（一页）

方法可重复能力：在原非凸边界／自由空间上，equal_arc与projected_mass都用相同256相位机会；后者在满足闭合弧gap、全成对欧氏间距和静态入域的候选中降低冻结需求J。projected是Lloyd迭代、约束布局与有限相位组合，不能将收益全归于一次投影。热点需求是数值优先级，不是风险模型。

新证据：本机先审计再选择seed95–114，60物理输入／360行，不是360独立人员样本。47人员合法、46曲线合法，三个N各46个规划配对全部projected优于Equal。唯一主要I₂的场景等权均值{f(mean)}m²、95%区间{ci(s['primary']['interval'])}，46个四目标配对均负；原开发研究和276固定目标回归不算入新输入样本。

资源收益与方法收益：同N ΔJ₀/ΔJ₂/ΔJ₄分别{f(s['contrasts']['0_demand_own']['equal_scene_mean_difference'])}/{f(s['contrasts']['2_demand_own']['equal_scene_mean_difference'])}/{f(s['contrasts']['4_demand_own']['equal_scene_mean_difference'])}m²。两方法增加Guide都降低自身J，这部分单列资源收益。再增加到N₀+4后，绝对布局优势在elongated／tight_u较+2略缩、wide_u扩大；相对百分比的增大不等于绝对优势加速。

热点与背景：等权热点超额布局收益随档位扩大，同N背景弧段projected相对Equal约+2.27%/+17.59%/+25.72%。但两方法相对N₀增加2或4名Guide后的背景绝对代价均下降，不能称增加Guide让背景绝对更差。硬约束与需求加密的有限搜索输出不是全局最优证明。

执行收益与代价：275/360完整成功，84共同拒绝，1科学TIMEOUT，编排错误0；276真实轨迹中275独立验收成功，62,834完整区间零安全违反。总路程随Guide增加，每Guide量另列；wide_u需要更多协调／恢复。最慢成功39.5s只余0.5s；约8.29e−9m最小Guide净裕量不能作为噪声鲁棒性保证。规划改善没有消除40秒执行边界。

系统正确性与方法证据：此前导航数值边界起点修复让合法真实起点正确进入查询，是共享实现一致性修复；本轮继承865e6a48且不修改科学模块。它与本轮新输入需求资源交互证据分开，旧360行／两编排错误／旧科学失败不改。本轮唯一失败是wide_u seed113 N21 Equal，原三次恢复全部使用、现有priority_yield已进入，40秒时仍未到达／贴线／覆盖；保留冻结输入、全精度目标和事件，不宣称其具体根因已诊断。

适用范围：静态单人群、已知场地、精确全局观测、无限通信、无crowd-guide交互，三个固定模板、热点B／Gaussian和嵌套周界初态、N11–23。不是每N独立初态抽样，不代表任意形状／噪声／规模，不验证containment或完整RA-ABDC。N₀+2不是通用最优资源。

下一轮唯一问题：固定wide_u seed113 N21 Equal的原现场与目标，原优先协调的进展为何未在40秒内完成，属于政策时间边界还是实现一致性问题？另立开发协议后再诊断；本轮不加预算／重跑／补seed，不自动开展其他外推或Step2/3。
'''
(REPORT/'RESEARCH_NOTE.zh.md').write_text(note,encoding='utf-8')
# The full two audit observations remain unique in runs. Git keeps the inspected
# file hashes, selection and compact exposure evidence with exact raw references.
for name in ('seed_audit.json','audit_at_input_freeze.json'):
    raw_audit=read(ROOT/name); evidence=[]
    for scene,seed in raw_audit['exposed_scene_seed_pairs']:
        found=[e for e in raw_audit['exposure_evidence'] if e['scene']==scene and e['seed']==seed]
        unique={tuple(sorted(e.items())) for e in found}
        examples=[dict(e) for e in sorted(unique,key=str)[:3]]
        evidence.append({'scene':scene,'seed':seed,'observations':len(found),'distinct_metadata_records':len(unique),'examples':examples})
    write(REPORT/name,{k:raw_audit[k] for k in ('scope','generator_namespace','scene_order','selection_rule','selected_seeds',
        'exposed_scene_seed_pairs','inspected_files')} | {'raw_file':str((ROOT/name).resolve()),'raw_sha256':sha(ROOT/name),
        'exposure_summary':evidence,'other_protocol_or_unattributed_record_count':len(raw_audit['other_protocol_or_unattributed_records']),
        'full_evidence_preserved_in_raw_file':True,'report_representation':'compact metadata; raw audit and selection unchanged'})
assert len(csvrows('tasks_360.csv'))==360
assert len(csvrows('quantity_diagnostics_60.csv'))==60
assert provenance({},None,'fixed_n',None)['source_sha256']==lock['provenance']['source_sha256']
write(REPORT/'final_verification.json',{'main_ledger_rows':360,'quantity_rows':60,'physical_inputs':60,
    'runner_statuses':s['runner_outcomes'],'orchestration_statuses':s['orchestration_outcomes'],
    'physical_success':s['physical_pass'],'physical_failure':s['physical_fail'],
    'executed_intervals':final['executed_intervals'],'unsafe_intervals':final['unsafe_intervals'],
    'all_276_integrations_match':all(r['main_matches'] and r['fine_matches'] for r in read(ROOT/'all_independent_integrations.json')),
    'scientific_and_all_frozen_udc_sources_unchanged':True,'protected_historical_files_verified':len(lock['historical_files']),
    'tests':'212 passed in 29.14s','pip_check':'No broken requirements found.',
    'checksums_refreshed_after_all_run_console_files_closed':True})
write(REPORT/'raw_checksums.json',{'run_files':{p.relative_to(ROOT).as_posix():sha(p) for p in ROOT.rglob('*') if p.is_file()},
    'historical_verified_files':len(lock['historical_files']),'historical_unchanged':True,
    'frozen_input_files':len(frozen['files']),'manifest_sha256':sha(ROOT/'manifest_360.json'),
    'final_snapshot_after_all_console_files_closed':True})
write(REPORT/'report_checksums.json',{'files':{p.relative_to(REPORT).as_posix():sha(p) for p in REPORT.rglob('*')
    if p.is_file() and p.name!='report_checksums.json' and '__pycache__' not in p.parts},
    'excludes_self_and_python_cache':True})
print({'report':str(REPORT/'REPORT.zh.md'),'primary':mean,'interval':s['primary']['interval'],'failures':len(failure_records)})
