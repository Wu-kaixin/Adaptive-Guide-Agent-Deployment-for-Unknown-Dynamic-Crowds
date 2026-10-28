# Adaptive Guide-Agent Deployment for Unknown Dynamic Crowds

[![Step 1 CI](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/workflows/ci.yml/badge.svg?branch=Major-Research-Branches)](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/workflows/ci.yml?query=branch%3AMajor-Research-Branches)

围绕未知人群边界的 Guide 机器人部署研究框架，连接**观测、资源分配、部署规划、安全执行与独立评价**。
本分支包含单群集成基线，以及静态多群、有界人员位置误差和固定资源预算下的 Step 1 扩展。

**阅读最新成果：**[审查入口与证据包](reports/step1_review_20261029/README.md) · [科研评估与限制](reports/step1_closure_20261028/ASSESSMENT.zh.md) · [源码架构](docs/ARCHITECTURE.zh.md)

## 分支与研究范围

| 分支 | 内容 | 适合用途 |
|---|---|---|
| [`main`](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/tree/main) | 单群 Step 1 集成基线及历史实验协议 | 安装、运行基线 |
| **`Major-Research-Branches`（当前）** | 静态多群扩展、独立确认结果与冻结证据 | 审查最新研究和实验实现 |

当前扩展保持**静态人员、已知封闭场地、全局有界误差观测、无限通信、无人群—机器人动力学交互**。
`K` 表示群体数量，不是总人数。路线确认覆盖 `K=1,2,3,5`、圆形／椭圆／凹形／不规则形状及三种资源条件；
50步连续确认使用 `K=2,5` 和凹形／椭圆。每种方法沿自身实际机器人终态继续推进。

人员真值仅用于独立评价；控制器读取观测、机器人状态和已知几何。`demand` 是数值规划权重，尚不是经验证的行为风险模型。
动态人员和受限通信需要另立协议；已有 Step 2a 开发模块不构成这些阶段的稳定性证据。

## Step 1 已有结果

| 研究问题 | 已完成证据 | 结论边界 |
|---|---|---|
| 安全替代路径 | 8,640条路线确认，320个独立物理块；安全部署完成率提高4.03个百分点，95%区间[3.51,4.55] | 安全部署不等于完整覆盖或路径全局完备 |
| 原参考服务保证 | 13,286次实际认证帧检查未发现声明界反例 | 充分条件及float64数值审计；不足资源条件全部回退 |
| 连续数量／相位更新 | 108,240条完整记账，80个独立物理块；2,092/2,160条序列完成50步 | 保留68次部署失败和2,038个后续未执行帧 |
| 主路程比较 | 47/80对共同完成子集上的总路程差−144.88m，95%区间[−215.97,−84.34]m | 完整分母识别范围跨零，不能宣布总体路程优势 |

任务行、认证帧与独立物理块是不同统计单位。服务证书比较同一方法当前状态下的贪心反事实，
不提供相对另一方法整条历史的确定损失界，也不证明任意时间的渐近稳定性。

原确认因安全违反停止在 **16,474/17,280**，806条未执行，未被新结果替换。见[原停止报告](reports/static_service_audit_20261026/REPORT.zh.md)。
本轮排定的新批次及独立复判已完成；连续部署失败与低资源证书保守性的后续独立研究暂缓。

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
```

该命令校验发布工件及归档成员的 SHA-256，不启动实验。完整轨迹复判仍需原始本地数据和对应冻结源码。

## 代码布局

| 路径 | 职责 |
|---|---|
| `src/udc/boundary/`、`deployment/` | 单群边界估计与部署基线 |
| `src/udc/multigroup/` | 误差观测、静态分群、资源分配、服务界及安全路径 |
| `src/udc/experiments/`、`evaluation/`、`analysis/` | 封存、连续执行、完整记账与独立复判 |
| `configs/step1/`、`tests/` | 实验协议与自动检查 |
| `reports/step1_review_20261029/` | 审查导航、哈希清单及原字节证据副本 |
| `docs/`、`reports/` | 方法契约、历史报告和科学边界 |

科学算法位于 `src/udc/`。各研究的冻结快照与当前活动代码分开保存；历史结果按对应版本解释。

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
