# 框架逻辑与代码位置

## 当前研究契约

Step 1：单一静态人群、已知封闭场地、全局观测、无限通信、无 crowd–guide 动力学交互。
人群初始化区域由仿真器持有。控制器从人员位置、半径和需求权重建立保守包络。
当前凸包会填平凹陷，不能据此声称准确重建非凸边界。需求权重还不是经验证的行为风险。

给定曲线是另一条输入，只供几何诊断。它从 `prepare_prescribed` 进入，并标明 `prescribed_diagnostic`。
普通 `prepare` 仍只调用 `estimate_boundary`。诊断曲线没有人员时，只评价曲线和目标几何；
人员、墙和路径保持未评价，不记成零违反。常数曲线密度也不等于每人权重为 1 的 Gaussian 映射。

## 数据流

```mermaid
flowchart TD
    A["场景配置与静态人群源"] --> B["人员观测"]
    B --> C["边界、覆盖目标与可达分配"]
    C --> D["路径跟踪与速度安全过滤"]
    D --> E["Guide 状态"]
    E --> D
    D --> F["保存完整执行轨迹"]
    F --> G["独立验收"]
    F --> H["PedPy 人群分析"]
    G --> I["成功、失败与统计报告"]
```

人员位置在初始化后冻结；反馈回路更新的是 Guide 状态。验收不依赖控制器的 settled 标志。
PedPy 负责人群密度等分析，专用 evaluator 负责 Guide 的覆盖、到达与安全。

## 按责任查找代码

| 层 | 位置 | 输入 → 输出 |
|---|---|---|
| 配置 | `src/udc/experiments/config.py` | YAML → 严格校验后的配置 |
| 已知场地 | `src/udc/scene.py` | 场地尺寸 → 方形／矩形与墙边界 |
| 人群源 | `src/udc/crowd/sources.py` | JuPedSim 放置／网格／SQLite 首帧 → 冻结人员位置 |
| 环境与感知 | `src/udc/environment.py`, `interfaces.py` | 仿真私有数据 → 行人 ID、位置、半径、demand、零速度、时间；`demand_region` 只在初始化时冻结；控制器仍只收 `Observation` |
| 边界 | `src/udc/boundary/` | 人员圆盘 → 凸包、外偏移曲线、弧长；`estimate_disk_union` 是标明参数的圆盘并集，仍只读位置和半径；诊断用的给定曲线在 `prescribed.py`，不进入普通估计 |
| 部署 | `src/udc/deployment/` | 曲线、权重、数量 → Equal Arc、Average CVT、Mass CVT、DistMesh-inspired；`projected_mass` 与 RA-ABDC 共用同一投影规划 |
| 路径与指派 | `src/udc/navigation/` | 初态、目标、障碍 → 同一套保守自由空间上的可见图；重算时其他 Guide 是障碍，自身不是 |
| 安全 | `src/udc/safety/` | 当前状态、速度 → 过滤速度、整段距离，以及不进入保守规划障碍的半平面 |
| 总控制器 | `src/udc/controller/abcg.py` | 观测、Guide 状态 → 期望速度、安全速度与恢复状态 |
| 资源分配接口 | `interfaces.ResourceAllocator` | Step 2 类型缝；Step 1 不调用，没有分配算法 |
| 实验编排 | `src/udc/experiments/runner.py` | 完整配置 → 执行、结果、resume、离线复判 |
| 公平比较 | `src/udc/experiments/comparison.py` | 因子表 → 仅方法不同的 fixed-N 任务 |
| 来源追溯 | `src/udc/experiments/provenance.py` | 代码、配置、输入 → commit 和 SHA-256 |
| 独立评价 | `src/udc/evaluation/acceptance.py`, `coverage.py` | 保存轨迹 → 九类验收；阶段诊断；共享的需求加权覆盖与热点诊断（不进入验收） |
| 批量协议 | `src/udc/experiments/validation.py` | manifest → 独立任务、多进程、CSV／JSON |
| 人群分析 | `src/udc/analysis/pedestrian.py` | 快照或 SQLite → 密度、邻域与分析配置 |
| 实验统计 | `src/udc/analysis/summary.py` | 全部结果 → 成功率、Wilson 区间、成功任务时间统计 |
| 可视化 | `src/udc/visualization/` | 保存结果或实时状态 → 图像与演示 |

根目录 `run.py`、`live_demo.py`、`visualization.py` 和 `experiments/validation_runner.py`
只调用包内的 CLI。开发时修改对应模块，避免在入口中新增控制算法。

## 数据与失败记录

每次运行生成 `config.yaml`、`trajectory.npz`、`planning.json`、`metrics.json`、`state.json`。
NPZ 禁用 pickle；人群行索引是冻结快照中的稳定人员身份，SQLite 首帧按原始 id 排序。
记录三类净距离：Guide–Guide、Guide–human、Guide–wall。人员半径按最大半径保守处理。
成功同时要求几何、资源、规划、路径、安全、到达保持、曲线距离和最大间隙通过。
`ceil(L/H_p)` 只是数量下界；满足它不能证明存在可行方案。搜索耗尽记录为未找到方案，除非下界或间距上界被违反。

资源不足、非法偏移、路径不可达、超时、恢复预算耗尽是科学失败。
程序异常保存 `runner_error.txt`，单独记为 `EXPERIMENT_ERROR`，不会被删除。
统计时间时注明仅成功任务，成功率的分母保留全部任务。

## 现有示例与后续阶段

`configs/step1/static_50.yaml` 固定相同 5×10 网格，关闭人员动力学，改用统一部署与验收。
它的 Guide 初态、偏移和控制器与旧 CSV 案例不同，因此不直接比较历史耗时。

`Perception` 在 Step 1 提供稳定行人 ID、冻结位置和零速度。控制器消费的仍是 `Observation(positions, radii, demand)`。
`ResourceAllocator` 与 `LocalPolicy` 没有实现，Step 1 运行不会调用它们。
公平比较因子表现为六种方法、4860 个任务，尚未整表运行。投影消融（360 任务）已跑完，只用于机制诊断。
`estimate_disk_union` 是一条标明闭运算半径和外偏移的非凸估计，只读人员位置和半径。它不替换凸包，也不是 alpha、radial 或 bootstrap 的比较。

导航的保守自由空间由 `navigation_domain` 构建，初始指派和执行中重算共用它。`segment_in_free` 对整段线段做几何判定：先用 `free.covers`，离开物理自由空间则拒绝，否则只允许深入保守障碍不超过原 1e-7 m。这一容差是原有的端点数值容差。实际运动区间仍按人、Guide 和墙的中心距检查。`free.covers` 可能拒绝距离边界为 0 的坐标；这种起点用既有的 1e-8 m 规划节点进入可见图，执行路径仍从真实位置开始。可见图边本身仍要求 `free.covers`，不把规划裕度里的弦当成静态边。房间内缩和人群多边形的第一层缓冲使用墙距 0.55 m：人群多边形已经包含人员半径，因此这一层对应 Guide 中心到人员中心 0.75 m。随后的 `simplify(0.10)` 和 `buffer(0.11)` 只是可见图的规划裕度，不是安全距离。安全过滤仍按中心距检查人、Guide 和墙，并拒绝把下一步推进保守障碍内部。已分配路径重算时，其他 Guide 按中心距 0.75 m 成为障碍，正在规划的那一台不进入自身障碍。1e-7 m 只用于边界上的浮点判定。
本次未迁入 Crowd-Management 的双环、未闭合 safe-geodesic、简化 gather 模型和静态多群实验。
Step 2 再加入动态群体 ID、分裂／合并、跨群分配和 crowd–guide 响应；Step 3 才限制感知与通信。
Vadere 后续先接离线轨迹，实时交互需要另建 backend 适配。
`projected_mass` 是固定浓度 1 的间隙投影。RA-ABDC 在此之上才减半浓度。本轮凸曲线上两者目标相同。demand 仍不是经验证风险。

2026-10-20固定资源恢复诊断发现一种数值边界起点：最近投影差仅约一个ulp，却大于旧方向选择阈值，1e−8m规划节点可能因此指向保守障碍内部，整行路径查询被遗漏。`_boundary_node`现仅在原节点失败且属于原1e−7m端点接触范围时，尝试既有外法向的同一1e−8m节点；保留真实起点，要求完整前缀物理入域及原完整线段检查。既有已接纳节点不变，不增加buffer或安全容差。这是导航／执行表示一致性修复，不是新部署规划方法。恢复策略顺序、失败传播、预算和进展监测未改变。

## 部署目标—静态导航域接口（2026-10-19）

`navigation.paths.target_domain_check` / `require_target_domain` 是部署验收、初始指派和固定指派重算共用的静态端点检查。几何来自同一个 `navigation_domain`：已知房间内缩、观测占据环的物理缓冲，以及既有简化／额外规划缓冲。其他 Guide 的临时占据不属于此目标禁区。物理自由域须覆盖目标，保守域覆盖或至多原 1e-7 m 边界数值接触才可接纳；不改变实际位置。

给定部署目标非法时，在指派／可见图搜索前保存 `TARGET_DOMAIN_INVALID`，证据包含目标编号、坐标、原弧坐标、域名／哈希、距离与原因。直接导航查询没有曲线时弧坐标可以为 null，部署调用必须提供弧坐标。提前拒绝只改变失败分类，不代表部署修复。

`deployment.admissible.admissible_phase` 是显式的 `static_domain_phase_v1` 后处理，仅在调用者选择该协议且原目标入域失败时运行。它在原闭曲线上检查预先固定的有限整体相位，保留数量和循环 gap pattern，重查弧间隙、全成对距离、原需求评价及目标域；候选与原方法输出分开保存并标记算法变化。不裁切非法弧段，不改变原曲线长度和覆盖定义。搜索失败只是预算内未找到配置。该步骤不替换边界估计，也不改变安全协调、执行或独立验收。

当前适用限制：闭运算产生的短尖角可能经 mitre 缓冲远距离伸出，使 1.3 m 偏移曲线局部不能进入保守导航域。单个非法目标不是整体不可行证明。seed51 已找到合法相位；非恒定需求和其他曲线上的效果不由本轮结果保证。运行与重建限制见 [目标域一致性报告](../reports/step1_target_domain_consistency/REPORT.zh.md)。

## 边界构造比较接口（2026-10-19）

`boundary.construct_boundary` 映射 `convex_hull` 到既有 `estimate_boundary`，映射 `disk_union_closed` 到既有 `estimate_disk_union`。两者读取同一位置、半径和房间；构造报告分开保存完整占据几何、外环控制几何和人员圆盘物理参考。圆盘闭运算步骤由原函数抽取为 `disk_occupancy`，几何计算未改变。多分量不选最大分量、不丢人员、不回退；孔洞及外环填孔明确记账。

`experiments.boundary_comparison` 冻结固定 N、Equal Arc legacy phase 和共用静态域相位政策。两构造的曲线和导航域可以不同，这属于实验效应；`evaluation.comparison.common_motion_metrics` 不接收估计器几何，只按共同人员／半径／房间及真实运动区间评价。各自曲线覆盖仍由原独立验收检查，内部代价不用于跨曲线排名。未执行行的运动指标为 null，构造失败留在共同合法输入分母。

协议将清单准备、输入冻结、实际执行分为独立操作。最初16行是协议开发检查；随后seed55–74的60共享输入／120行已全部完成，见[完整比较](../reports/step1_boundary_estimator_comparison_v1/REPORT.zh.md)。源码、配置、原快照和SQLite均以哈希锁定，不能在新源码上改写旧锁来重跑。详细定义见[比较协议](../reports/step1_boundary_estimator_protocol/REPORT.zh.md)，本机原始工件依赖见[数据说明](DATA_AVAILABILITY.zh.md)。

## 人员圆盘覆盖的表示契约（2026-10-19）

解析物理圆盘、构造用32段／象限多边形、原验收用16段／象限多边形是三个对象。默认估计器与独立验收保持原规则；`boundary.audit_person_disk_coverage` 提供 `observed_disk_polygon_coverage_v1` 的统一32段诊断候选和16段对照，分别审计完整占据集与控制器实际外环，保留原covers、关系矩阵、差集和精确保存坐标证据。诊断不修改几何、不做近似放行，也不参与物理安全评价。严格覆盖的操作性承诺、局部有理数证书及误差量纲见[覆盖几何契约](COVERAGE_GEOMETRY_CONTRACT.zh.md)。

## 源码依据

部署、导航、恢复和独立验收基于 `Wu-kaixin/ABCG-controller` 的 `a18480c`（MIT）。
原仓库冻结统计属于原实现；本次改变模块路径、Mass CVT 核密度和实验追溯，必须重新验证。
主仓库分支基于 `6d9bc09`，保留该提交的依赖与 Notebook 修改。
