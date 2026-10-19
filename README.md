# Adaptive Guide-Agent Deployment for Unknown Dynamic Crowds

面向未知人群周围自适应 Guide 部署的统一研究框架。

当前整合分支实现 **Step 1：单个静态人群、已知封闭场地、全局观测、无限通信、无人群与 Guide 动力学交互**。项目最终研究多个人群的移动、扩散、分裂、合并，以及有限通信下的搜索与去中心化部署。

先读 [框架逻辑与代码位置](docs/ARCHITECTURE.zh.md)，再看 [实验协议](docs/EXPERIMENT_PROTOCOL.zh.md)和 [本分支验证记录](docs/VALIDATION.zh.md)。

## 安装与第一次运行

需要 Python 3.12。以下命令在仓库根目录运行。

```bash
python -m venv .venv
```

Windows PowerShell：`.\.venv\Scripts\Activate.ps1`。Linux/macOS：`source .venv/bin/activate`。

```bash
python -m pip install -e ".[dev,analysis]"
python run.py --config configs/step1/static_50.yaml --output runs/static50
python run.py --reevaluate runs/static50
udc-analyze --result runs/static50 --output runs/static50_analysis
```

50 人案例固定 5×10 网格和 8 个 Guide，人群位置严格冻结。JuPedSim 随机放置案例：

```bash
python run.py --config configs/step1/square.yaml --output runs/square
python run.py --config configs/step1/rectangle.yaml --output runs/rectangle
```

每次使用新输出目录；已完成同一任务可加 `--resume`。只有代码、配置与输入 hash 一致才会跳过。

## 逻辑顺序

| 顺序 | 处理 | 去哪里修改 |
|---|---|---|
| 1 | 定义场地、人员源与实验参数 | `configs/step1/` |
| 2 | 初始化并提供冻结人员观测 | `src/udc/crowd/`, `environment.py`, `interfaces.py` |
| 3 | 估计占据包络与外偏移部署曲线 | `src/udc/boundary/` |
| 4 | 选择 Guide 数量与部署目标 | `src/udc/controller/abcg.py`, `deployment/` |
| 5 | 绕障路径与可达目标匹配 | `src/udc/navigation/` |
| 6 | 反馈跟踪、安全过滤与有限停滞恢复 | `src/udc/controller/abcg.py`, `safety/` |
| 7 | 保存执行轨迹并独立验收 | `src/udc/experiments/`, `evaluation/` |
| 8 | 汇总实验、分析人群和绘图 | `src/udc/analysis/`, `visualization/` |

根目录脚本仅是入口；科学算法统一放在 `src/udc/`。控制器不读取人员生成多边形，也不从模拟器获取真实人群轮廓。

需求有三种可区分的生成方式：均匀（`std=0`）、逐人独立抽样（`std>0`）、以及由场景坐标中固定圆盘 `demand_region` 决定的空间热点。人员需求只通过 Gaussian 曲线密度进入规划；热点配置见 `configs/step1/scenarios/spatial_hotspot.yaml`。历史场景 `high_demand_side` 是逐人独立抽样，不是空间热点。Step 1 中这些数是规划权重，不是已经验证的人群危险。

## 方法与资源协议

| 方法 | 参数 | 定义 |
|---|---|---|
| Equal Arc | `equal_arc` | 等弧长部署 |
| Average CVT | `average_cvt` | 局部平均 demand 驱动的周期 CVT |
| Mass CVT | `mass_cvt` | Gaussian 需求质量驱动的周期 CVT，保留空间相对尺度 |
| DistMesh-inspired | `distmesh` | 按平方根密度分位做间距松弛，并非完整 EikMesh／DistMesh 库 |
| 带间隙约束的需求加权部署 | `projected_mass` | 原始需求密度上的周期 CVT，每步把弧间隙投影回上下界；这是当前有实验支持的候选 |
| RA-ABDC（兼容入口） | `ra_abdc` | 与上者同一投影；浓度减半和 Equal Arc 回退仍在代码中，凸曲线实验里没有触发 |

`adaptive_resource` 在数量上限内搜索可接受目标；`fixed_n` 固定数量，适合公平算法对照。

```bash
python run.py --config configs/step1/square.yaml --output runs/fixed24 --method equal_arc --protocol fixed_n --fixed-n 24
python run.py --config configs/step1/square.yaml --output runs/batch --seeds 0 1 2 3 4 --workers auto --no-plots
```

`--workers auto` 在不同回合间使用多进程；每个 worker 限制数值库线程。

## 批量协议与统计

```bash
python experiments/validation_runner.py --output runs/smoke --phase development --smoke --workers 4
python experiments/validation_runner.py --output runs/smoke --reevaluate
udc-summarize --results runs/smoke --output runs/smoke_statistics.json
```

完整 development 会使用 0–29 seeds。未来 validation 预留 5000–5029；整合开发不运行这些 seeds。smoke 现在覆盖 11 个场景、五种方法和两资源协议，共 110 个任务；本轮没有重跑这份 smoke。

方法比较使用因子表。同一单元共享场景、人群快照、需求场、Guide 数量、边界、导航和安全参数，只更换部署方法。开发种子是 0–4。完整组合现为 2 种场地 × 3 种形状 × 3 种规模 × 3 种需求 × 3 种资源 × 6 种方法 × 5 个种子，共 4860 个任务；默认不整表运行。投影消融是圆形／椭圆 × 均匀／弱热点／强热点 × 三档曲线数量 × 四种方法 × seed 0–4，共 360 个任务。

```bash
python -m udc.experiments.comparison --output runs/fair --dry-run
python -m udc.experiments.comparison --output runs/fair_cell --seeds 0 --environment square --shape circle --size small --demand uniform --resource sufficient --workers 4
python -m udc.experiments.comparison --hotspot --output runs/pilot/high_demand_20_24 --workers 4
python -m udc.experiments.comparison --spec experiments/step1_hotspot_pilot.yaml --output runs/pilot/hotspot_smoke --seeds 0 --workers 4
python -m udc.experiments.comparison --spec experiments/step1_hotspot_pilot.yaml --output runs/pilot/hotspot --workers 4
python -m udc.experiments.comparison --spec experiments/step1_projection_ablation.yaml --output runs/ablation/projection_matrix --plan-only --workers 8
python -m udc.experiments.comparison --spec experiments/step1_projection_ablation.yaml --output runs/ablation/projection_matrix --resume --workers 8
python visualization.py --result runs/pilot/hotspot/square_circle_medium_hotspot_moderate/ra_abdc/seed_0 --demand-profile
```

Step 1 研究笔记在 `notebooks/step1_static_deployment.ipynb`。算法仍在 `src/udc/`，笔记只调用已经保存的结果。

给定曲线诊断使用 seed 15–19，比较高曲率胶囊、浅凹和深槽上的弧间隙与欧氏间距。它不估计未知边界。随后的成对约束诊断和场景闭环也是开发诊断，不替换正式种子结果。

```powershell
.\.venv\Scripts\python.exe -m udc.experiments.curve_diagnostics --spec experiments/step1_general_curve_diagnostics.yaml --output runs/step1_general_curve --workers 6
.\.venv\Scripts\python.exe -m udc.experiments.curve_diagnostics --spec experiments/step1_general_curve_diagnostics.yaml --output runs/step1_general_curve --execute-selected --workers 1
.\.venv\Scripts\python.exe -m udc.experiments.curve_diagnostics --spec experiments/step1_general_curve_diagnostics.yaml --source runs/step1_general_curve --output runs/step1_adhesion_repair --rerun-adhesion
.\.venv\Scripts\python.exe -m udc.experiments.curve_constraints --spec experiments/step1_curve_constraints.yaml --output runs/step1_curve_constraints --workers 6
.\.venv\Scripts\python.exe -m udc.experiments.scene_closure --spec experiments/step1_scene_closure.yaml --output runs/step1_scene_closure
.\.venv\Scripts\python.exe -m udc.experiments.replan_repair --diagnose runs/step1_scene_closure/execution/wide_u/disk_union_primary/equal_arc/run --diagnosis-json reports/step1_wide_u_replan_diagnosis.json --diagnosis-npz reports/step1_wide_u_replan_failure_window.npz --diagnosis-figure reports/step1_wide_u_replan_failure.png
.\.venv\Scripts\python.exe -m udc.experiments.replan_repair --source-root runs/step1_scene_closure/execution --output-root runs/step1_wide_u_replan_repair --scenes wide_u elongated tight_u --summary reports/step1_wide_u_replan_summary.json --comparison-figure reports/step1_wide_u_replan_comparison.png
```

任务完成、预设失败语义通过、实际 SUCCESS 分开统计；初态无效、资源不足和超时都保留。规划搜索耗尽记为“未找到可行方案”；只有数量下界或间距上界被违反时才给出不可行证书。统计包含成功率 Wilson 95% 区间与成功任务部署时间均值／中位数，不用确定性重复制造独立样本。规划目标指标与轨迹指标分开报告。


非凸确认 v2 的后续执行修复只改变自由空间内的航点切换：细长 seed30 成对方法由恢复耗尽变为 19.1 s 成功，宽 U seed30 三方法仍耗尽。90 行新标签为 60 成功、27 输入失败、3 次恢复耗尽。记录见 [执行跟随修复](reports/step1_execution_progress_repair/REPORT.zh.md)。

非凸记录重建的新批次为 `configs/step1/nonconvex_record_rebuild_v1.yaml`（三场景×三方法×seed20–24，45行）。它与丢失的旧45输入不同。当前批次基线35成功/9输入失败/1路径失败，固定目标恢复后36成功/9输入失败，原九案例回归9/9。完整记录、输入规则和可复制命令见 [重建报告](reports/step1_nonconvex_record_rebuild_v1/REPORT.zh.md)。已有结果可运行：

```powershell
.\.venv\Scripts\python.exe -m udc.experiments.nonconvex_diagnostics --output runs/step1_nonconvex_record_rebuild_v1
```

## 输出与回放

| 文件 | 内容 |
|---|---|
| `config.yaml` | 实际配置 |
| `trajectory.npz` | Guide 轨迹、速度、冻结人群、目标、分配、边界 |
| `planning.json` | 逐数量／相位的规划记录 |
| `metrics.json` | 九类验收、安全诊断、失败原因、commit 与 hash |
| `state.json` | 原子完成标记 |
| `scene.png` | 场景与 Guide 轨迹图 |

```bash
python visualization.py --result runs/static50
python live_demo.py --cases square,rectangle --snapshot runs/live_preview.png
python live_demo.py
```

实时图用于观察；科学结论从保存轨迹复判。实时窗口需要桌面环境，headless 实验不需要 GUI。

## 工具与阶段边界

| 工具／目录 | 当前用途 |
|---|---|
| JuPedSim | Step 1 物理间隔放置；后续负责动态人群仿真 |
| PedPy | 密度、Voronoi 与邻域分析；可分析原始 SQLite 轨迹 |
| Vadere | 后续跨模型验证，当前尚未接入运行 backend |

Step 1 凸包提供保守包络，会丢失凹陷。demand 是规划权重。在本轮凸曲线上，完整 RA-ABDC 与 Mass CVT＋间隙投影给出相同目标；浓度调节和 Equal Arc 回退没有被触发。实际人群响应与风险模型属于后续研究。

完整路线见 [ROADMAP](docs/ROADMAP.zh.md)，理论与实现边界见 [STEP1_THEORY](docs/STEP1_THEORY.zh.md)。

## 这台机器上的复现

已有 `.venv` 是 Python 3.12.15，数值库版本写在 `requirements.txt`。不要把另一台机器的 Python 3.13 记录当成必须重装的条件。新结果写到 `runs/reproduction/<device>/`，不要覆盖 `runs/` 里的旧目录。workers 先用小批量测量；这台 8 逻辑处理器的机器使用 6。完整记录见 [VALIDATION](docs/VALIDATION.zh.md)。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m udc.experiments.comparison --spec experiments/step1_projection_confirmation.yaml --output runs/reproduction/Kaii/phase_f/confirmation_smoke --seeds 5 --workers 6
.\.venv\Scripts\python.exe -m udc.experiments.comparison --spec experiments/step1_projection_formal.yaml --output runs/formal/step1_projection_v1 --workers 6
```

## 检查

```bash
python -m pytest -q
python -m pip check
```

GitHub Actions 配置 Linux／Windows 检查。实际在线 CI 状态以 Actions 页面为准。采用 MIT License；部署基线来源与提交说明见架构文档。
