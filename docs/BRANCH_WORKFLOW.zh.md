# 日常开发分支与恢复记录

日常开发使用原项目根目录的 `Major-Research-Branches`。它保留研究分支的静态多群实现、协议与冻结报告，并合入主仓库重建的目录结构、资产检索恢复、环境配置和 Windows 原子发布保护。单群 CLI 继续可用；研究模块按各自协议使用，不据此声称动态 Step 2/3 已完成。

## 本轮合并前的版本

| 引用 | 提交 | 用途 |
|---|---|---|
| `main` | `06b0d7945726cd56d46624decbb6b04ab1b94316` | 保留的原单群集成基线，未移动 |
| 研究分支原头 | `2806bd1182901e590e9525e6dd4ad71e8c3464b2` | 合并的第一父提交 |
| 重建分支原头 | `40628ae9551b31b72553429966674827a00eabbc` | 合并的第二父提交 |

采用正常双父合并，不压缩、不 rebase、不改写原历史。合并验证后用普通 `git branch -d` 删除 `codex/repository-rebuild-20261031` 名称，其提交仍由新研究分支的祖先关系保留。没有执行 push；远程默认分支及远程提交不会因本地切换自动改变。

## 日常操作

在本项目根目录工作，确认 `git branch --show-current` 为 `Major-Research-Branches`。
使用根目录 `.venv`；代码放在 `src/udc/`，协议放在 `configs/`，新运行使用 `runs/` 中全新目录。
历史数据通过 `scripts/research_assets.py` 检索和恢复，详情见 [数据说明](../data/README.md) 与 [恢复说明](RECOVERY.zh.md)。

`data/archives/`、`data/catalog.sqlite`、科研资料的本地明细、`.runtime/`、`.venv/`、`.tmp/` 和新运行输出均不纳入本轮提交。研究分支此前已经跟踪的公开证据包保持原字节，不重新生成或加入本地大型原始数据。

## 合并前恢复保护

本机保护目录为 `research/project-management/branch_merge_20261031/`，其中 `before-merge.bundle` 保存合并前的全部 Git 引用，已经实际克隆并通过 `git fsck --full`，且核对了上表三个提交。

bundle SHA-256：`08e5036633c849061391542dbf488ac629925cb68f40581509eb56c971108edd`。

需要查看旧版本时，将 bundle 克隆到一个全新目录，再按上表提交检出；不要覆盖当前工作目录或替换其 `.git`。bundle 只保护 Git 内容，历史大型资产继续由主项目内部归档及其来源索引提供恢复。此处不声称独立磁盘灾备。

重建审计目录 `repository_rebuild_20261031` 是上一个阶段的冻结记录，里面的原开发分支名描述当时状态。当前日常分支以本文和 `branch_merge_20261031` 的最终回执为准。实测检查记录在 [验证日志](VALIDATION.zh.md)。
