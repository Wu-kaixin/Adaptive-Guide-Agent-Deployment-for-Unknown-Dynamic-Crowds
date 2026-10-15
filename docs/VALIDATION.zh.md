# 验证记录

当前重构检查写在第一节。第二节是提交 `56f0197` 的历史记录，不能当作当前工作树的证据。

## 2026-10-16 重构检查

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

Crowd-Management 的非凸估计器比较、完整主要实验、动态多人群、crowd–guide 交互、风险模型、
去中心化部署和 Vadere backend 尚未完成。预留 validation seeds 5000–5029 没有运行。
本地检查在 Linux 上进行；Windows 验证由新增 Actions 工作流承担，须查看在线运行结果。
