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

## 方法与资源协议

| 方法 | 参数 | 定义 |
|---|---|---|
| Equal Arc | `equal_arc` | 等弧长部署 |
| Average CVT | `average_cvt` | 局部平均 demand 驱动的周期 CVT |
| Mass CVT | `mass_cvt` | Gaussian 需求质量驱动的周期 CVT，保留空间相对尺度 |
| DistMesh-inspired | `distmesh` | 按平方根密度分位做间距松弛，并非完整 EikMesh／DistMesh 库 |

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

完整 development 会使用 0–29 seeds。未来 validation 预留 5000–5029；整合开发不运行这些 seeds。smoke 默认覆盖 11 个场景、四方法和两资源协议，共 88 个任务。

四算法公平比较使用另一份因子表。同一单元共享场景、人群、Guide 数量、边界、导航和安全参数，只更换部署方法。开发种子是 0–4。完整组合是 2 种场地 × 3 种形状 × 3 种规模 × 3 种需求 × 3 种资源 × 4 种方法 × 5 个种子，共 3240 个任务；默认不整表运行。

```bash
python -m udc.experiments.comparison --output runs/fair --dry-run
python -m udc.experiments.comparison --output runs/fair_cell --seeds 0 --environment square --shape circle --size small --demand uniform --resource sufficient --workers 4
python -m udc.experiments.comparison --hotspot --output runs/hotspot_20_24 --workers 4
```

Step 1 研究笔记在 `notebooks/step1_static_deployment.ipynb`。算法仍在 `src/udc/`，笔记只调用已经保存的结果。

任务完成、预设失败语义通过、实际 SUCCESS 分开统计；初态无效、资源不足和超时都保留。统计包含成功率 Wilson 95% 区间与成功任务部署时间均值／中位数，不用确定性重复制造独立样本。

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

Step 1 凸包提供保守包络，会丢失凹陷。demand 是规划权重；实际人群响应与风险模型属于后续研究。下一步接入 Crowd-Management 的 alpha／radial 边界做模块对比，再推进动态多群。

完整路线见 [ROADMAP](docs/ROADMAP.zh.md)，理论与实现边界见 [STEP1_THEORY](docs/STEP1_THEORY.zh.md)。

## 检查

```bash
python -m pytest -q
python -m pip check
```

GitHub Actions 配置 Linux／Windows 检查。实际在线 CI 状态以 Actions 页面为准。采用 MIT License；部署基线来源与提交说明见架构文档。
