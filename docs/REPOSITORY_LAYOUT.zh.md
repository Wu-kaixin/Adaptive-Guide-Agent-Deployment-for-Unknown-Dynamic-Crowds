# 主仓库使用说明

本项目根目录是长期开发入口，日常分支为 `Major-Research-Branches`。分支沿革和恢复位置见[分支说明](BRANCH_WORKFLOW.zh.md)。2026-10-31 重建保留原 Git 历史、main 引用、冻结报告和实验哈希锁。
原 `AGD-UDC-Workspace` 暂时保留；日常开发使用本目录的环境、代码和数据入口。

```text
Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/
├── src/udc/                 单群与静态多群 Step 1 实现、评价、资产恢复模块
├── configs/                 明确研究阶段的配置与协议
├── experiments/             薄 CLI 和实验清单
├── scripts/                 环境、维护、证据检查和资产定位入口
├── tests/                   科学契约与工程回归
├── notebooks/               可复现分析源文件
├── docs/                    架构、理论、协议、开发与恢复说明
├── reports/                 原有版本化报告和冻结证据，保持原路径
├── runs/                    新开发运行及明确标记的测试工件
├── data/
│   ├── catalog.sqlite       每个来源身份与历史路径的本地目录
│   ├── archives/            普通 ZIP64 分卷，保留原规范文件相对路径
│   └── restored/            用户显式恢复的独立目录，禁止覆盖旧结果
├── research/
│   ├── RESEARCH_INDEX.md    研究问题、证据与资料导航
│   ├── materials/           会议、演示、诊断与溯源资料的可浏览副本
│   ├── implementations/     固定提交的研究源码与证据视图
│   ├── indexes/             逐项来源关系和详细分类（本地）
│   ├── archives/            Git 历史与旧审计保护资料（本地）
│   └── project-management/  本轮状态、命令日志、删除与恢复回执
├── requirements/            原依赖规格与测量锁文件
├── .runtime/                本机独立 CPython 3.12.15 发行版
├── .venv/                   本项目唯一虚拟环境，由上述发行版新建
└── README.md
```

大型资产、解释器、环境和本地清单不提交 Git。源码、工具、公开说明和必要的既有报告继续版本管理。
不以当前文件名或 seed 推断实验身份，也不把字节相同的两个来源记录合为一个实验。

## 开发入口

```powershell
./scripts/setup_env.ps1
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pip check
./.venv/Scripts/python.exe run.py --config configs/step1/static_50.yaml --output runs/my_new_development
./.venv/Scripts/python.exe run.py --reevaluate runs/my_new_development
```

安装脚本优先使用本机 `.runtime/cpython-3.12.15/python.exe`，其他机器可以显式传入 `-Python`。
不要复制或激活归档环境。科学算法仍写在 `src/udc/`，CLI 只转发调用。
先读[架构](ARCHITECTURE.zh.md)与根目录 `AGENTS.md`。预留种子 5000–5029 禁止用于开发。

## 查找资料

从[科研索引](../research/RESEARCH_INDEX.md)进入报告、会议材料和研究版本。
历史数据从[数据说明](../data/README.md)按来源身份查找与恢复。
旧文档中的历史路径可以保留；恢复工具通过来源目录解析，不全盘替换冻结文件中的路径。
当前维护状态和实际完成度见本机 `research/project-management/repository_rebuild_20261031/mission_state.json`。
