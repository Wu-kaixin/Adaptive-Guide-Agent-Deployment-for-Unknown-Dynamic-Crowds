# 整合分支验证记录

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

## DistMesh 停止条件（2026-10-14，Windows）

`src/udc/controller/coverage.py`：DistMesh-inspired 只在目标移动小于 `1e-5` 时标记 `CONVERGED`，或达到原迭代上限。CVT 代价停滞不再作为它的停止条件。另外三种方法的停止条件未改。人数固定为 8，规划间隙上限仍为 3.90 m。

| 检查 | 结果 |
|---|---|
| `python -m pytest -q` | 34/34 PASS |
| `runs/exp03_distmesh_stopfix` | `PLAN_SEARCH_EXHAUSTED`，`success=false`，仿真时间 0 s |
| 四相位规划 | 均 `CONVERGED`，25–27 次迭代，`max_gap=4.0250 m`，`min_separation=3.2093 m`，均未接受 |

充分迭代后间隙仍超过 3.90 m，保留为有效失败。Mass CVT 结果未重跑。

## 尚未完成

Crowd-Management 的非凸估计器比较、完整主要实验、动态多人群、crowd–guide 交互、风险模型、
去中心化部署和 Vadere backend 尚未完成。预留 validation seeds 5000–5029 没有运行。
本地检查在 Linux 上进行；Windows 验证由新增 Actions 工作流承担，须查看在线运行结果。
