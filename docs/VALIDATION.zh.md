# 验证记录

当前投影消融写在第一节。其后各节是更早的热点试点、重构和迁移记录，不能当作这一轮的结果。

## 2026-10-16 投影与浓度消融

起始提交是上一轮成果 `b6a37b6`（父提交 `4fbc5a0`）。本轮检查时分支为 `Major-Research-Branches`，工作树在消融修改之后为 dirty。没有推送，没有改 `main`。`runs/` 被忽略，实验数据留在磁盘。

环境沿用已有 `.venv`，没有重装依赖：Windows 11、Python 3.13.13（Anaconda）、NumPy 2.5.4、SciPy 1.18.1、Shapely 2.2.0、JuPedSim 1.4.2、PedPy 1.5.1、pytest 9.1.1。验收阈值没有改动。validation seeds 5000–5029 没有运行。

### 上一轮轨迹复核

`runs/pilot` 下 191 份保存结果全部独立复判：157 份有执行轨迹，34 份在规划失败时停止，没有伪造执行轨迹。在线与离线的九类验收和覆盖诊断一致，差异数为 0。30 个试点配对的输入哈希一致。规划失败的尝试都未被标成接受，间隙都超过 1.94 m，阶段都是 `no_plan_found`，没有 `certified_infeasible`。

均匀人员需求（每人权重 1）经过 Gaussian 映射后，seed 0 的曲线密度最大／最小约为 1.58。这是人群相对曲线的占据，不是需求抽样。Mass CVT 在“均匀”单元格失败，原因是这条曲线密度，不是人员权重的方差。评价密度对所有方法都是这份原始 Mass CVT 密度；浓度指数只进入规划。

`certified_infeasible` 只在 `ceil(L/H_p)` 或 `floor(L/d_min)` 被违反时出现。它假设目标在这一条闭合曲线上，并且弦长不超过弧长。它不包含曲率造成的额外弦缩短、障碍和可达性。必要条件成立仍只记 `no_plan_found`。单独诊断：圆形 N=13（下界 14）和椭圆 seed 0 的 N=14（下界 15），可用 Guide 都多于下界，终止状态仍是 `PLAN_SEARCH_EXHAUSTED`，阶段是 `certified_infeasible`，证书是 `GAP_BOUND`。这两条不进入 360 任务表。

### 360 任务消融

配置 `experiments/step1_projection_ablation.yaml`。先做 seed 0 的 72 任务规划扫描，再规划全部 360 个，然后只对找到方案的任务执行。输出目录 `runs/ablation/projection_matrix`。90／90 配对输入哈希一致。360／360 复判与在线结果一致。没有指派失败，没有执行失败。

| 方法 | 成功 | 规划未找到 | 浓度 | Equal Arc 回退 |
|---|---|---|---|---|
| Equal Arc | 90/90 | 0 | — | — |
| Mass CVT | 62/90 | 28 | — | — |
| projected_mass | 90/90 | 0 | 固定 1 | 无此机制 |
| RA-ABDC | 90/90 | 0 | 始终 1.0 | 0／90 |

28 次 Mass CVT 失败全部在数量下界附近（圆形 N=14 的三种需求各 5／5；椭圆 N=16 的强热点 5／5，均匀和弱热点各 4／5）。`projected_mass` 把这 28 次都变成可执行并被验收的方案，没有把任何一次已成功的 Mass CVT 变成失败。余量档和较多余量档上，四种方法都是 5／5。该模式在 seed 0–4、两种形状和三种需求强度上重复出现，但只出现在贴近下界的那一档。

在 90 对已保存目标里，`projected_mass` 与 RA-ABDC 的目标坐标全部一致，调整记录长度都是 1。浓度减半和回退没有发生。因此本轮完整 RA-ABDC 没有超出间隙投影的行为；那部分机制尚未得到实验检验。原因是投影已经把弧间隙放进 `[0.75, 1.94]`，这些凸曲线上的弦长没有再跌破 0.75 m。

Mass CVT 被拒绝的方案，重新由保存的人群算出的规划间隙均值 2.15 m（2.01–2.52）。投影后的实际间隙均值 1.74 m，最大 1.940 m。成功任务的最小净距裕量是 0.192 m。两者都已执行时，仿真时间差的均值是 0 s；规划失败的仿真时间是 0，分析时视为缺失，不把它当成更快。规划耗时均值 0.036 s，最大 0.145 s。

需求加权代价（原始密度，28 个被投影挽救的任务）：无约束 Mass CVT 均值 0.277，投影后实际 0.283，Equal Arc 实际 0.288。相对 Equal Arc，投影留下约 44% 的代价改进。圆形上最近人员窗口能定义热点浓度：无约束均值 1.23，投影后 1.04，Equal Arc 1.00，超出等弧长的集中约留下 16%。椭圆上该窗口弧长比例为 0，因为热点人员不在曲线的最近点上；椭圆不报告这个浓度，需求曲线上的峰仍在图里。

图在 `runs/ablation/projection_matrix/figures/`。

### 本轮没有做的事

4860 任务因子表没有运行。development smoke 没有按新的六方法清单重跑。不为了触发浓度调节而修改验收阈值。

本轮复现：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m udc.experiments.comparison --spec experiments/step1_projection_ablation.yaml --output runs/ablation/projection_smoke --seeds 0 --plan-only --workers 8
.\.venv\Scripts\python.exe -m udc.experiments.comparison --spec experiments/step1_projection_ablation.yaml --output runs/ablation/projection_matrix --plan-only --workers 8
.\.venv\Scripts\python.exe -m udc.experiments.comparison --spec experiments/step1_projection_ablation.yaml --output runs/ablation/projection_matrix --resume --workers 8
```

## 2026-10-16 受控热点试点（上一轮）

不能当作上面消融的结果。

分支 `Major-Research-Branches`，父提交 `4fbc5a0`，检查时工作树为 dirty。本机 PATH 上没有 `uv`；使用已有 `.venv`。环境：Windows 11、Python 3.13.13（Anaconda）、NumPy 2.5.4、SciPy 1.18.1、Shapely 2.2.0、JuPedSim 1.4.2、PedPy 1.5.1、pytest 9.1.1。验收阈值没有改动。validation seeds 5000–5029 没有运行。

| 检查 | 结果 |
|---|---|
| `python -m pytest -q` | 53 passed |
| `python -m pip check` | No broken requirements found |
| 50 人网格，Equal Arc，8 Guide | SUCCESS，仿真时间 23.0 s，实际最大间隙 3.741 m，路径总长 134.33 m |
| 50 人轨迹独立复判 | 与在线九类验收一致；需求加权覆盖也一致 |
| `high_demand_side` 的 20／24 Guide，五方法，seed 0 | 10/10 轨迹复判与在线结果一致。见下表 |
| 热点试点 smoke，seed 0，30 任务 | 30 completed，25 SUCCESS，5 `no_plan_found`，配对哈希一致 |
| 热点试点 150 任务，seed 0–4 | 150 completed，0 experiment error，125 SUCCESS，25 `no_plan_found`，30/30 配对一致 |
| 抽查复判（热点 15 个保存轨迹） | 15/15 在线与离线一致 |

50 人运行的最小净裕量：人 0.200 m，Guide 1.743 m，墙 0.750 m。复判跟踪 RMSE 0.00416 m。

### 需求场

`demand_kind` 区分为 `uniform`、`independent`、`hotspot`、`hotspot_on_independent`。空间热点是场景坐标中的固定圆盘：背景需求恒为 1，圆内乘 3 后再按 `[min,max]` 截断，生成后冻结。试点里 seed 0–4 的热点人数为 10–12／50。`high_demand_side` 仍是逐人独立抽样。人员需求只通过 Gaussian 曲线密度进入规划。

三档 Guide 数量相对生成多边形必要下界 15：tight 14、moderate 17、ample 19。实现凸包更短，因此实现下界 `ceil(L/1.94)` 对 seed 0–4 都是 14。它们是几何预算，不是充足／临界／不足的证明。

### 20／24 Guide 诊断

配置是 `configs/step1/scenarios/high_demand_side.yaml`。demand 按人独立抽样。曲线长 37.321 m，规划间隙上限 1.94 m，必要下界 20，可用 24。Guide 中心间距阈值 0.75 m。所有 10 个任务都满足必要数量条件；失败都是 `no_plan_found`，不是不可行证书，也没有进入执行。

| 指定数量 | 方法 | 阶段 | 终止状态 |
|---|---|---|---|
| 20 | Equal Arc | accepted | SUCCESS，7.8 s，实际最大间隙 1.867 m |
| 20 | RA-ABDC | accepted | SUCCESS，7.8 s，实际最大间隙 1.940 m，浓度 1.0，无 Equal Arc 回退 |
| 20 | Average CVT | no_plan_found | PLAN_SEARCH_EXHAUSTED |
| 20 | Mass CVT | no_plan_found | PLAN_SEARCH_EXHAUSTED |
| 20 | DistMesh-inspired | no_plan_found | PLAN_SEARCH_EXHAUSTED。预测最大间隙 2.770 m，观测约 2.437 m |
| 24 | Equal Arc | accepted | SUCCESS，8.6 s，实际最大间隙 1.556 m |
| 24 | Average CVT | accepted | SUCCESS，8.6 s，实际最大间隙 1.626 m |
| 24 | Mass CVT | accepted | SUCCESS，8.6 s，实际最大间隙 1.836 m |
| 24 | RA-ABDC | accepted | SUCCESS，8.6 s，实际最大间隙 1.836 m，与 Mass CVT 相同 |
| 24 | DistMesh-inspired | no_plan_found | PLAN_SEARCH_EXHAUSTED。预测 2.308 m，观测约 2.08–2.12 m |

20 Guide 的 Equal Arc／RA-ABDC 成功只使用 20/24，不能解释成资源不足已经排除。RA-ABDC 在 N=24 与 Mass CVT 重合，因为无约束方案已经满足间隙上限。

### 150 任务热点试点

场景：square、circle、50 人。需求：均匀与空间热点。数量：14／17／19。方法：五种。seed：0–4。失败全部计入分母。规划指标与轨迹指标分开保存。五个开发 seed 只用于诊断。

| 方法 | 成功 | 说明 |
|---|---|---|
| Equal Arc | 30/30 | 所有单元格都找到方案并被独立验收 |
| RA-ABDC | 30/30 | 浓度始终为 1.0，没有 Equal Arc 回退 |
| Average CVT | 25/30 | 只在热点 × N=14 上 `no_plan_found` |
| Mass CVT | 20/30 | 热点 × N=14 与均匀 × N=14 上都未找到方案 |
| DistMesh-inspired | 20/30 | 与 Mass CVT 同一组失败；N=14 时预测最大间隙 2.14–2.60 m，观测 2.02–2.35 m，都超过 1.94 m |

N=17／19 时 RA-ABDC 的轨迹间隙、需求加权代价和热点浓度与 Mass CVT 相同：约束未激活时该候选就是带记录的 Mass CVT。N=14 热点 seed 0：Equal Arc 实际间隙 1.902 m、热点浓度 1.000；RA-ABDC 把间隙投影到 1.940 m，热点浓度 1.059，需求加权代价从 0.299 降到 0.284。这不能外推成一般优势。

DistMesh-inspired 没有调参。间隙过大由其 $\sqrt{\rho}$ 不动点解释，不是阻尼过小。若要把最宽间隙压到 $H_p$，需要的数量下界是 `count_for_limit`（本试点约 16–19），对相关方法没有另设规则。

人员分布、边界需求曲线和部署目标图在 `runs/pilot/hotspot/figures/`。热点圆盘在人群 +x 侧，曲线密度在对应弧段升高。

### 本轮没有完成的运行

完整因子表 4050 个任务没有运行。development smoke（现为 110 任务）没有重跑。validation seeds 5000–5029 没有运行。

本轮复现（现有 `.venv`，仓库根目录）：

```bash
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe run.py --config configs/step1/static_50.yaml --output runs/pilot/static50 --no-plots
.\.venv\Scripts\python.exe run.py --reevaluate runs/pilot/static50
.\.venv\Scripts\python.exe -m udc.experiments.comparison --hotspot --output runs/pilot/high_demand_20_24 --workers 8
.\.venv\Scripts\python.exe -m udc.experiments.comparison --spec experiments/step1_hotspot_pilot.yaml --output runs/pilot/hotspot_smoke --seeds 0 --workers 8
.\.venv\Scripts\python.exe -m udc.experiments.comparison --spec experiments/step1_hotspot_pilot.yaml --output runs/pilot/hotspot --workers 8
.\.venv\Scripts\python.exe visualization.py --result runs/pilot/hotspot/square_circle_medium_hotspot_moderate/ra_abdc/seed_0 --demand-profile
```

## 2026-10-16 重构检查（历史）

分支 `refactor/step1-modular`，父提交 `56f0197`，检查时工作树为 dirty。
本机没有 Python 3.12，也没有已存在的 uv 虚拟环境。检查使用由 Python 3.13.13 新建的 `.venv`：Windows 11、NumPy 2.5.4、SciPy 1.18.1、Shapely 2.2.0、JuPedSim 1.4.2、PedPy 1.5.1。
验收阈值没有改动。

| 检查 | 结果 |
|---|---|
| `python -m pytest -q` | 40 passed |
| `python -m pip check` | No broken requirements found |
| 50 人网格，Equal Arc，8 Guide | SUCCESS，仿真时间 23.0 s，实际最大间隙 3.741 m，路径总长 134.33 m，资源利用率 1 |
| 50 人轨迹独立复判 | 与在线九类验收一致 |
| `high_demand_side` 的 20／24 Guide，四方法，seed 0 | 8/8 轨迹复判与在线结果一致。见下表 |
| 公平比较一个单元：square／circle／small／uniform／sufficient／seed 0 | 四种方法都 SUCCESS，人员快照哈希相同，fixed N = 15。这不是方法排序 |
| development smoke，88 任务 | 88 completed，0 experiment error，88 expectation matched，56 SUCCESS |

50 人运行的最小净裕量：人 0.200 m，Guide 1.743 m，墙 0.750 m。复判跟踪 RMSE 0.00416 m。

### 20／24 Guide 诊断

配置是 `configs/step1/scenarios/high_demand_side.yaml`。demand 按人独立抽样，不是空间热点。可用 Guide 为 24。规划间隙上限 2.0 m，弧长容差 0.03 m，因此可接受的规划间隙上限是 1.94 m。Guide 中心间距阈值是 0.75 m。

| 指定数量 | 方法 | 类别 | 终止状态 |
|---|---|---|---|
| 20 | Equal Arc | success | SUCCESS，7.8 s，实际最大间隙 1.867 m，利用率 20/24 |
| 20 | Average CVT | planning_failure | PLAN_SEARCH_EXHAUSTED。四相位都收敛，规划间隙 1.969–1.983 m，超过 1.94 m |
| 20 | Mass CVT | planning_failure | PLAN_SEARCH_EXHAUSTED。规划间隙 2.183–2.413 m |
| 20 | DistMesh-inspired | planning_failure | PLAN_SEARCH_EXHAUSTED。规划间隙 2.437 m |
| 24 | Equal Arc | success | SUCCESS，8.6 s，实际最大间隙 1.556 m |
| 24 | Average CVT | success | SUCCESS，8.6 s，实际最大间隙 1.626 m |
| 24 | Mass CVT | success | SUCCESS，8.6 s，实际最大间隙 1.836 m |
| 24 | DistMesh-inspired | planning_failure | PLAN_SEARCH_EXHAUSTED。规划间隙约 2.08–2.12 m，超过 1.94 m |

被拒绝的尝试里，Guide 间距都大于 0.75 m。因此这些失败不是 `CAPACITY_SHORTFALL`，也没有进入执行。没有观察到执行失败。
20 Guide 的 Equal Arc 成功只使用 20/24，不能解释成资源不足已经被排除于所有方法之外。

### 本轮没有完成的运行

公平比较因子表共 3240 个任务，只实际跑了上述 1 个单元的 4 个任务。
validation seeds 5000–5029 没有运行。RA-ABDC 没有实现。

## 2026-10-14 历史记录（提交 56f0197）

验证日期：2026-10-14。环境：Linux、Python 3.12.14、JuPedSim 1.4.2、PedPy 1.5.1。
这是开发工作树上的迁移检查与 smoke；不是新盲测，也不继承来源仓库的 CLOSED 声明。
机器记录见 [verification.json](../reports/integration/verification.json)。原始开发输出由命令在 `runs/` 重建。
机器记录保存发布前 Git 父提交、dirty=true 和被执行源码的 SHA-256，避免伪称在发布后的提交上运行。

## 已完成检查

| 检查 | 结果 |
|---|---|
| 原有与新增单元／集成／反例测试 | 34/34 PASS |
| pip 依赖健康 | No broken requirements found |
| 默认 square／rectangle | SUCCESS，包含在测试中 |
| 50 人冻结案例 | SUCCESS，8 Guides，23.0 s 仿真时间 |
| 原实现迁移等价性 | 2 环境 × Equal Arc／Average CVT，4/4 保存数组完全一致，状态与 criteria 相同 |
| 88 任务 development smoke | 88 completed，0 experiment error，88 expectation matched |
| 实际部署 SUCCESS | 56/88；失败保留，不能把 88 completed 解释为全部成功 |
| 保存轨迹离线复判 | 88/88 与在线结果一致；50 人案例也一致 |
| PedPy | 50 人、1 帧冻结快照，密度与邻域产物成功输出 |
| headless 多案例可视化 | square／rectangle 快照成功生成 |
| wheel 打包 | 包含控制器、evaluator、运行器、人群源与分析模块 |
| 从解压 wheel 运行 | 50 人案例 SUCCESS，未依赖源码目录导入 |

## Smoke 终止分布

| 状态 | 次数 |
|---|---:|
| SUCCESS | 56 |
| PLAN_SEARCH_EXHAUSTED | 15 |
| CAPACITY_SHORTFALL | 8 |
| OFFSET_INVALID | 8 |
| PATH_UNREACHABLE | 1 |

资源不足与非法偏移夹具预期失败；其他随机场景的 expectation 允许科学失败。
因此 expectation 全部匹配说明运行和失败记账符合协议，不说明每个算法达到论文性能要求。
本轮每个随机配置仅使用一个开发 seed，不作泛化或显著性结论。

## 本轮修正

- Mass CVT 改为 logsumexp 与全曲线共同尺度，修正按采样点归一化丢失相对质量的问题；新增直接 Gaussian 计算对照。
- SQLite 来源只读首帧，随后严格冻结；新增数据库哈希不变测试。
- 非法配置早期失败也可以离线复判。
- 失败和程序错误保留在统计分母；程序异常另存 traceback 与任务标签。
- 批量进程内部限制数值线程；依赖、所有包源码和输入快照参与追溯。
- 50 人例子的占据包络外偏移为 1.45 m，以容纳保守导航余量；Guide 初态与旧部署 CSV 不同，不直接比较其耗时。

## 复现

```bash
python -m pip install -e ".[dev,analysis]"
python -m pytest -q
python -m pip check
python experiments/validation_runner.py --output runs/smoke --phase development --smoke --workers 4
python experiments/validation_runner.py --output runs/smoke --reevaluate
python run.py --config configs/step1/static_50.yaml --output runs/static50
python run.py --reevaluate runs/static50
udc-analyze --result runs/static50 --output runs/static50_analysis
udc-summarize --results runs/smoke --output runs/smoke_statistics.json
```

## 尚未完成

Crowd-Management 的非凸估计器比较、完整 4860 任务因子表、动态多人群、crowd–guide 交互、经验证风险模型、
去中心化部署和 Vadere backend 尚未完成。预留 validation seeds 5000–5029 没有运行。
360 任务消融只说明间隙投影在这些凸曲线、开发 seed 上恢复了贴近下界时的可行性。它不是正式验证，也不说明浓度调节有贡献。
