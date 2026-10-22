# Adaptive Guide-Agent Deployment for Unknown Dynamic Crowds

围绕未知人群的 Guide 自适应部署研究框架。当前实现 **Step 1：静态单人群、已知封闭方形／矩形场地、全局观测、无限通信，无 crowd–guide 动力学交互**。动态多群、分裂／合并和有限感知／通信属于后续阶段。

2026-10-23 已合并两台电脑的项目文件，采用转移项目的后续代码，保留两边数据和冲突原件。缺失数据按授权重新执行，并明确标记为重建数据。见 [重建日志](docs/PROJECT_REBUILD.zh.md)。

本次已完成 **1,699行／次重建记录**，并核验31,305个新文件。明确审计的8,393个NPZ／SQLite／WKB引用全部哈希匹配；旧主机元数据版本的剩余缺口与新完整工件分开记录。见[重建结果](reports/project_rebuild_20261023/reconstruction/REPORT.zh.md)与[数据范围](docs/DATA_AVAILABILITY.zh.md)。

## 环境与首次运行

**一个项目一个 `.venv`，使用独立 CPython 3.12，不使用 Conda 或依赖 Conda 的 venv。** 本机已建立 Python 3.12.15 环境。直接指定解释器，不依赖终端当前激活的环境。

Windows PowerShell，在项目根目录运行：

```powershell
./scripts/setup_env.ps1
# 新机器可明确传入独立 Python：
# ./scripts/setup_env.ps1 -Python 'C:/path/to/Python312/python.exe'
./.venv/Scripts/python.exe run.py --config configs/step1/static_50.yaml --output runs/my_first_run
./.venv/Scripts/python.exe run.py --reevaluate runs/my_first_run
./.venv/Scripts/python.exe -m udc.analysis.pedestrian --result runs/my_first_run --output runs/my_first_analysis
```

新机器重新创建 `.venv`，不能复制旧环境。设置脚本会拒绝 Conda 来源的解释器；编辑器默认解释器已指向项目 `.venv`。可选激活命令：`./.venv/Scripts/Activate.ps1`。

Linux/macOS 使用独立 `python3.12 -m venv .venv`，随后 `.venv/bin/python -m pip install -e ".[dev,analysis,notebooks]"`。将下文 Windows 解释器替换为 `.venv/bin/python`。Windows 完整实测依赖见 [版本锁](requirements/locks/windows-py312.txt)，直接科学依赖见 [requirements.txt](requirements.txt)，包依赖范围见 [pyproject.toml](pyproject.toml)。

每次实验使用新输出目录；`--resume` 只用于源码、配置和输入哈希一致的已定义任务。

## 项目结构

| 目录 | 职责 |
|---|---|
| `src/udc/boundary/`、`deployment/` | 观测边界、Guide 数量和部署规划 |
| `src/udc/navigation/`、`controller/`、`safety/` | 路径指派、执行反馈、恢复和安全过滤 |
| `src/udc/crowd/`、`environment.py`、`interfaces.py`、`scene.py` | 冻结人群源、观测与已知场地 |
| `src/udc/experiments/`、`evaluation/` | 编排、来源追溯和独立验收 |
| `src/udc/analysis/`、`visualization/` | 配对统计、PedPy、图像与回放 |
| `configs/step1/` | 场景和实验协议配置 |
| `experiments/` | 已冻结的早期协议 YAML 和薄 CLI 入口，保留原路径 |
| `tests/`、`notebooks/` | 测试，以及调用包内接口的研究笔记 |
| `runs/` | 两台机器已有原始数据和新运行结果，不随 Git 发布 |
| `runs/reconstructed_20261023/` | 本次单独标记的重建数据和历史源码快照 |
| `reports/`、`docs/` | 冻结报告、校验清单、架构、协议和验证日志 |
| `scripts/` | 环境设置和数据重建维护入口 |
| `backups/project_rebuild_20261023/` | 转移文件完整归档、冲突旧件和旧环境 |

根目录 `run.py`、`live_demo.py`、`visualization.py` 仅调用包内 CLI。科学算法位于 `src/udc/`；历史报告分析脚本、既有协议和少量 NPZ 保留原位置，维持证据引用。

## 方法与证据

支持 `equal_arc`、`average_cvt`、`mass_cvt`、`distmesh`、`projected_mass` 和研究兼容入口 `ra_abdc`。`adaptive_resource` 在上限内搜索数量；`fixed_n` 固定数量用于配对比较。demand 是可观测数值规划权重，尚不是经验证的风险模型。

控制器输入为 `Observation(positions, radii, demand)`、Guide 状态和已知场地；不以出生多边形充当真实人群边界。人员初始化后冻结。凸包、圆盘闭运算和给定诊断曲线分别定义；解析圆盘、q32 构造与 q16 验收保持分离，不放宽严格覆盖或安全阈值。

最新研究入口是 [Step 1 证据图谱](docs/STEP1_EVIDENCE_MAP.zh.md)。历史资源确认的 275 成功／84 拒绝／1 超时，以及单现场 43.8 秒诊断晚到均保留原含义。本次重建不改写原结论，也不增加独立确认样本。

| 阅读目的 | 入口 |
|---|---|
| 理解代码、输入和阶段边界 | [架构](docs/ARCHITECTURE.zh.md)、[理论](docs/STEP1_THEORY.zh.md) |
| 确认数据来源与缺口 | [数据可用性](docs/DATA_AVAILABILITY.zh.md)、[重建日志](docs/PROJECT_REBUILD.zh.md) |
| 查看冻结协议与种子规则 | [实验协议](docs/EXPERIMENT_PROTOCOL.zh.md) |
| 查看本机实测检查 | [验证日志](docs/VALIDATION.zh.md) |
| 查看后续工作 | [路线图](docs/ROADMAP.zh.md) |
| 审查旧 main 整合历史 | [2026-10-19 审查](docs/STEP1_INTEGRATION_REVIEW.zh.md) |

## 检查、输出和回放

```powershell
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pip check
./.venv/Scripts/python.exe experiments/validation_runner.py --phase development --smoke --workers 4 --output runs/my_smoke
./.venv/Scripts/python.exe experiments/validation_runner.py --reevaluate --output runs/my_smoke
```

本次实测 **218 项测试通过**；development smoke **132/132 完成、0 程序错误、132 预期匹配、93 实际成功**；132 条复判与在线结果一致。完整测量见 [本次审计](reports/project_rebuild_20261023/README.md)。科学失败保留在分母，这些检查不替代历史矩阵。

运行保存 `config.yaml`、`trajectory.npz`、`planning.json`、`metrics.json` 和 `state.json`；程序异常另存 traceback。

```powershell
./.venv/Scripts/python.exe visualization.py --result runs/my_first_run
./.venv/Scripts/python.exe live_demo.py --cases square,rectangle --snapshot runs/live_preview.png
```

重建维护入口：`./.venv/Scripts/python.exe scripts/rebuild_missing_data.py --suite all --workers 2`。输出只写独立重建目录，完成任务通过检查点读取，不覆盖或自动重试已完成的科学失败。历史种子再次执行属于重建／开发回归；**5000–5029 禁止用于开发重跑**。

三个分支 `main`、`Major-Research-Branches`、`integration/step1-framework` 同步发布同一份完整项目；分支比较和健康检查见 [同步说明](docs/REPOSITORY_SYNC.zh.md)。`.venv/`、`runs/`、`backups/` 是本地环境与数据，Git同步针对跟踪文件。实时跨平台 CI 以 [GitHub Actions](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/workflows/ci.yml) 为准。采用 MIT License，来源与提交见架构文档。
