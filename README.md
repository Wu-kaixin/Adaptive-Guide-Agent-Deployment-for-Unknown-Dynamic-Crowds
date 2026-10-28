# Adaptive Guide-Agent Deployment for Unknown Dynamic Crowds

[![Step 1 CI](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/workflows/ci.yml?query=branch%3Amain)

围绕未知人群边界的 Guide 机器人部署研究框架：从人员观测估计边界、生成部署目标，执行运动并保存轨迹，最后由独立评价器验收覆盖、安全与到达。

## 分支与研究范围

| 分支 | 内容 | 适合用途 |
|---|---|---|
| **`main`（当前）** | 单群 Step 1 集成基线及历史实验协议 | 安装、运行基线、阅读既有方法 |
| [`Major-Research-Branches`](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/tree/Major-Research-Branches) | 静态多群、有界观测误差、固定资源分配及连续重观测扩展 | 查看最新研究代码和审查证据 |

本分支实现**单一静态人群、已知封闭方形／矩形场地、全局人员观测、无限通信、无人群—机器人动力学交互**。
人员在初始化后冻结；反馈回路更新机器人状态。项目名称中的“Dynamic Crowds”表示研究方向，当前基线不提供动态人群实验结论。

支持 `equal_arc`、`average_cvt`、`mass_cvt`、`distmesh`、`projected_mass` 及兼容入口 `ra_abdc`；
`fixed_n` 用于固定数量比较，`adaptive_resource` 在给定上限内搜索资源数量。
控制器使用 `Observation(positions, radii, demand)`、机器人位置和已知场地，不读取仿真私有出生区域作为人群边界。
`demand` 是可观测规划权重，尚不是经验证的行为风险模型。

最新扩展成果位于研究分支，参见[审查入口](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/blob/Major-Research-Branches/reports/step1_review_20261029/README.md)。
两分支独立维护；该链接中的实验结果不代表本分支已经包含相应实现。

## 快速开始

使用独立 **CPython 3.12**，每个项目只使用根目录的 `.venv`。不要使用 Conda，也不要从其他机器复制虚拟环境。

Windows PowerShell，在仓库根目录运行：

```powershell
./scripts/setup_env.ps1
# 如需指定解释器：
# ./scripts/setup_env.ps1 -Python 'C:/path/to/Python312/python.exe'
./.venv/Scripts/python.exe run.py --config configs/step1/static_50.yaml --output runs/my_first_run
./.venv/Scripts/python.exe run.py --reevaluate runs/my_first_run
```

Linux/macOS：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,analysis,notebooks]"
.venv/bin/python run.py --config configs/step1/static_50.yaml --output runs/my_first_run
.venv/bin/python run.py --reevaluate runs/my_first_run
```

每次新运行使用新的输出目录。示例不需要历史 SQLite 数据；其结果属于当前配置的开发运行。
包依赖见 [pyproject.toml](pyproject.toml)，历史实测 Windows 环境见[依赖锁](requirements/locks/windows-py312.txt)。

## 检查与可视化

以下命令使用 Windows 解释器；Linux/macOS 替换为 `.venv/bin/python`。

```powershell
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pip check
./.venv/Scripts/python.exe visualization.py --result runs/my_first_run
./.venv/Scripts/python.exe -m udc.analysis.pedestrian --result runs/my_first_run --output runs/my_first_analysis
```

工程检查、开发实验和独立确认具有不同用途。测试通过不等于科学假设已得到验证；
实测记录见[验证日志](docs/VALIDATION.zh.md)，当前检查结果见上方对应分支的 CI。

## 代码布局

| 路径 | 职责 |
|---|---|
| `src/udc/boundary/`、`deployment/` | 观测边界与部署目标 |
| `src/udc/navigation/`、`controller/`、`safety/` | 指派、路径、反馈与安全约束 |
| `src/udc/experiments/`、`evaluation/`、`analysis/` | 实验编排、独立验收与统计分析 |
| `configs/step1/`、`tests/` | 场景协议与自动检查 |
| `docs/`、`reports/` | 方法说明、结果及来源证据 |

根目录 CLI 只转发包内功能；科学算法位于 `src/udc/`。

## 数据与复现

Git 包含源码、配置、报告和校验清单；完整运行数据位于本地 `runs/`，环境与备份分别位于 `.venv/`、`backups/`。
**克隆仓库不等于取得全部实验数据。** 原始工件、重建数据和新增开发运行分别追溯，重建不增加独立确认样本。
历史缺口和跨机整合记录见[数据可用性](docs/DATA_AVAILABILITY.zh.md)、[重建日志](docs/PROJECT_REBUILD.zh.md)。

复判历史结果应使用其冻结配置、源码和工件；不能用当前代码重跑后替换原结论。
**种子 5000–5029 为预留验证输入，禁止用于开发。** 科学失败、程序异常和未执行任务须保留在相应分母中。

## 文档导航

| 阅读目的 | 入口 |
|---|---|
| 代码职责与数据流 | [架构](docs/ARCHITECTURE.zh.md) |
| 方法与数学条件 | [理论](docs/STEP1_THEORY.zh.md) |
| 已有证据及适用范围 | [证据图谱](docs/STEP1_EVIDENCE_MAP.zh.md) |
| 冻结协议与公平比较 | [实验协议](docs/EXPERIMENT_PROTOCOL.zh.md) |
| 后续阶段 | [路线图](docs/ROADMAP.zh.md) |

采用 [MIT License](LICENSE)。
