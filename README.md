# Adaptive Guide-Agent Deployment for Unknown Dynamic Crowds

[![Step 1 CI](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/workflows/ci.yml/badge.svg?branch=Major-Research-Branches)](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/workflows/ci.yml?query=branch%3AMajor-Research-Branches)

围绕未知人群边界的 Guide 机器人部署研究框架，连接**观测、资源分配、部署规划、安全执行与独立评价**。
本分支包含单群集成基线，以及静态多群、有界人员位置误差和固定资源预算下的 Step 1 扩展。

**阅读当前研究版本：**[发布与数据获取](reports/step1_release_20261030/README.zh.md) · [最终评估](reports/step1_final_20261029/REPORT.zh.md) · [逐项验收](reports/step1_final_20261029/REQUIREMENTS.zh.md) · [Step 2/3接口交接](docs/STEP1_FINAL_HANDOFF.zh.md)

本次发布定位为**静态扩展Step 1工程基线与研究结果**。声明协议内的执行、复判和完整事件边界恢复通过；一般误差原参考服务证书与学术独特性仍未闭合，当前服务门控保持隔离回退。

## 分支与研究范围

| 分支 | 内容 | 适合用途 |
|---|---|---|
| [`main`](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/tree/main) | 单群 Step 1 集成基线及历史实验协议 | 安装、运行基线 |
| **`Major-Research-Branches`（当前）** | 静态多群工程基线、条件研究原型、最终评估与冻结证据 | 复用静态执行／审计基础，审查研究结果和限制 |

当前扩展保持**静态人员、已知封闭场地、全局有界误差观测、无限通信、无人群—机器人动力学交互**。
`K` 表示群体数量，不是总人数。最后一次独立评估覆盖 `K=1,2,3,5`、四种形状、三档资源、三种观测扰动和三种更新方法；
48个物理输入、每场景15–195人、最多80个Guide。每条50步序列沿自身实际机器人终态继续推进。

人员真值仅用于独立评价；控制器读取观测、机器人状态和已知几何。`demand` 是数值规划权重，尚不是经验证的行为风险模型。
动态人员和受限通信需要另立协议；已有 Step 2a 开发模块不构成这些阶段的稳定性证据。

## 当前静态 Step 1 结果

| 指标 | 已完成证据 | 结论边界 |
|---|---|---|
| 连续执行 | 1,296/1,296条序列交付，64,944条记录完整 | 48个独立物理输入；交付不等于完整覆盖 |
| 服务完成 | 30,525完整成功、34,419部分服务；低／中／高资源完整成功率0%／42.67%／98.34% | 不足资源与观测覆盖拒绝保留；sufficient不是可行性证书 |
| 安全与复判 | 1,211,851运动区间不安全计数0；64,944条决策／运动评价复判一致 | 限冻结静态模型和数值判据，不是普适安全证明 |
| 公共API与恢复 | 144会话、7,344事件及576次完整事件边界关闭／恢复一致 | 同worker恢复；没有实机、断电或动态backend验证 |
| 当前服务门控 | 两门控各0/21,600认证，全部回退；非平凡保持0 | 原参考质量映射未闭合，门控隔离 |
| 当前移动收益 | 三策略4,320组轨迹块逐字节相同，配对路程差0m | 不能转用旧冻结版本的保持收益或声称方法独特性成立 |

API step离线wall-time p50约2.179s、p95约6.867s，包含计划、仿真和落盘，不是纯规划时延或实时控制保证。
完整成功与终态空间连续服务分开记录；微小正积分残差保留原值。工程检查、数值复判与数学证明分别解释。

**历史版本独立保留：**[旧审查包](reports/step1_review_20261029/README.md)中的13,286次认证检查是旧冻结代码的条件数值审计；后续发现实现包络前提缺口，不能将其作为当前服务保证。
旧80块连续确认保留68次部署失败和2,038个后续未执行帧；原确认仍因安全违反停止在 **16,474/17,280**，806条未执行，见[原停止报告](reports/static_service_audit_20261026/REPORT.zh.md)。新结果不改写旧失败、阈值或哈希锁。
最后一次研究实验已结束，长期目标与定时器已停止；本发布仅进行工程检查、证据打包和版本同步，不开启新的独立研究实验。

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

本分支还可在纯 Git 克隆中验证公开证据：

```powershell
./.venv/Scripts/python.exe scripts/package_step1_review.py
./.venv/Scripts/python.exe scripts/package_step1_final.py
```

这两个命令分别校验旧审查包和新最终公开包的SHA-256，不启动研究实验。新包包含封存输入、源码与元数据；完整轨迹复判需要另行获取完整原始数据和对应冻结环境。

## 代码布局

| 路径 | 职责 |
|---|---|
| `src/udc/boundary/`、`deployment/` | 单群边界估计与部署基线 |
| `src/udc/multigroup/` | 误差观测、静态分群、资源分配、服务界及安全路径 |
| `src/udc/experiments/`、`evaluation/`、`analysis/` | 封存、连续执行、完整记账与独立复判 |
| `configs/step1/`、`tests/` | 实验协议与自动检查 |
| `reports/step1_review_20261029/` | 审查导航、哈希清单及原字节证据副本 |
| `reports/step1_release_20261030/`、`reports/step1_final_20261029/` | 当前发布／数据验证入口及原字节最终评估 |
| `docs/`、`reports/` | 方法契约、历史报告和科学边界 |

科学算法位于 `src/udc/`。各研究的冻结快照与当前活动代码分开保存；历史结果按对应版本解释。

## 数据与复现

Git 包含源码、配置、报告和校验清单；完整运行数据位于本地 `runs/`，环境与备份分别位于 `.venv/`、`backups/`。
**克隆仓库不等于取得全部实验数据。** 原始工件、重建数据和新增开发运行分别追溯，重建不增加独立确认样本。
本次Git提供封存输入／源码／元数据公开包；完整最终轨迹另有维护者本地归档，当前无公开原始轨迹下载URL。
获取、恢复及公开／完整两级验证命令见[新数据说明](reports/step1_release_20261030/README.zh.md)。
历史缺口和跨机整合记录见[数据可用性](docs/DATA_AVAILABILITY.zh.md)、[重建日志](docs/PROJECT_REBUILD.zh.md)。

复判历史结果应使用其冻结配置、源码和工件；不能用当前代码重跑后替换原结论。
**种子 5000–5029 为预留验证输入，禁止用于开发。** 科学失败、程序异常和未执行任务须保留在相应分母中。

## 文档导航

| 阅读目的 | 入口 |
|---|---|
| 当前成果、数据获取和验证 | [研究版本发布](reports/step1_release_20261030/README.zh.md) |
| 当前全目标验收与残余限制 | [最终评估](reports/step1_final_20261029/REPORT.zh.md) · [F1–F12](reports/step1_final_20261029/REQUIREMENTS.zh.md) |
| 代码职责与数据流 | [架构](docs/ARCHITECTURE.zh.md) |
| 方法与数学条件 | [理论](docs/STEP1_THEORY.zh.md) |
| 已有证据及适用范围 | [证据图谱](docs/STEP1_EVIDENCE_MAP.zh.md) |
| 冻结协议与公平比较 | [实验协议](docs/EXPERIMENT_PROTOCOL.zh.md) |
| 后续阶段 | [路线图](docs/ROADMAP.zh.md) |

采用 [MIT License](LICENSE)。
