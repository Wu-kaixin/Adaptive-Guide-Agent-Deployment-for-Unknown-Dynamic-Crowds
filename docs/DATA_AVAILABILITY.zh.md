# 数据可用性、跨机整合和重建范围

## 2026-10-29 静态多群审查包

[公开审查包](../reports/step1_review_20261029/README.md)包含已完成批次的原字节清单、封印、摘要、复判凭据、
三份科学源码快照及独立分析快照。`scripts/package_step1_review.py`可在纯Git克隆中校验这些工件。
完整人员观测、机器人轨迹和每5步检查点仍保留在本地`runs/`，未随Git发布；因此公开凭据校验不等于重新执行全量轨迹复判。
冻结报告中的本地路径按审查包`manifest.json`映射阅读，原报告和旧锁不改写。原确认16,474/17,280且安全停止，806条未执行不补跑。

## 当前本地项目（2026-10-23）

本文件夹已合并当前电脑与转移电脑的代码、配置、报告和可用运行数据。全部转移文件均保存完整归档和逐文件SHA-256；冲突旧件保留。从Git恢复37个转移时丢失的跟踪文件，并依据历史哈希找回883个相同字节的文件。去向与校验见 [重建日志](PROJECT_REBUILD.zh.md) 和 [整合清单](../reports/project_rebuild_20261023/merge_manifest.json)。

用户已授权重新运行缺失实验。**已有原件、字节一致的找回文件、2026-10-23重新生成的数据分别追溯。** 重建数据位于 `runs/reconstructed_20261023/`，不能冒充历史原始工件，也不增加独立确认样本。每批保留源码快照、输入、完整结果、复判、失败和来源标签。

两份项目及本机既有备份并不包含所有历史工件。整合后的初次审计覆盖252份历史JSON中的32,001个路径／哈希组合：9,668匹配，93为所引版本不同，22,240个runs路径缺失。只统计带哈希的明确引用，不把目录存在当作数据完整；详见 [引用审计](../reports/project_rebuild_20261023/data_reference_audit.json)。重建后的实际完成范围另记，不篡改旧清单的缺失事实。

## 文件位置

| 数据层 | 本地位置与使用范围 |
|---|---|
| 可安装框架、场景、协议与测试 | `src/udc/`、`configs/`、`experiments/`、`tests/`，可开展新开发运行 |
| 两台电脑已有原始工件 | `runs/`内各历史目录；有些只有部分数据，先读哈希审计 |
| 历史报告、汇总、图表与校验清单 | `reports/`，保存冻结结论及引用；报告存在不保证全部所引轨迹在本机 |
| 9份既有历史NPZ例外 | `reports/step1_wide_u_replan_cases/`，保留原位置和原版本 |
| 本次重建 | `runs/reconstructed_20261023/`，各批的 `reconstruction.json` 与 `reconstruction_summary.json` 标明完成范围 |
| 旧转移项目完整快照 | `backups/project_rebuild_20261023/incoming_snapshot.zip`，含旧Git、虚拟环境和缓存 |
| 43个冲突的当前旧件 | `backups/project_rebuild_20261023/previous_conflicts/` |
| 旧Conda来源虚拟环境 | `backups/project_rebuild_20261023/previous_environment/`，仅作归档 |

当前本地运行环境唯一入口为根目录 `.venv`，使用独立CPython3.12.15。一个项目一个环境，不采用Conda，不复制另一台机器的环境。新机器按 [README](../README.md) 创建环境。

## 可从源码开始的检查

```powershell
./scripts/setup_env.ps1
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pip check
./.venv/Scripts/python.exe run.py --config configs/step1/static_50.yaml --output runs/first_run
./.venv/Scripts/python.exe run.py --reevaluate runs/first_run
```

50人示例不需要历史SQLite。配置的偏移和初态属于该示例，不与1.3m非凸实验混用。132任务development smoke使用新的独立输出；正式5000–5029不用于开发。

## 原始路径和版本

旧工件含 `C:/Users/kai/...`、`E:/...` 等路径。审计按已知项目根和明确包内路径解析至本地位置，保留原路径和预期哈希；没有批量改写旧YAML、provenance或锁。源码的历史版本与当前活动版本不同不等于文件损坏，应按对应Git引用阅读。转移历史可用 `refs/remotes/transfer/20261023` 访问。

读取旧轨迹与重新运行是不同操作。原协议的prepare／freeze入口要求未使用种子、干净冻结源码或完整原数据；本次重建用独立维护编排调用已有科学生成器、规划器、runner和独立验收，另建重建锁，不伪造旧clean状态或关闭原锁检查。旧dirty源码若没有完整对应字节，只能使用已注明的冻结版本近邻，不能宣称原样重放。

## Git和可携带性

`runs/`、`backups/`和`.venv/`被Git忽略，因此完整本地项目不等于Git克隆含所有数据。搬迁本项目时应保留runs、reports和backups；到新机器重新创建`.venv`。依赖锁描述本次Windows环境，历史冻结版本仍以各报告为准。

历史seed55–74边界比较、覆盖契约、v3重建及原正式1620任务结论保持原样。正式实验仍属于原冻结版本f5b5d9c；本次不重新使用5000–5029。更多科学边界见 [证据图谱](STEP1_EVIDENCE_MAP.zh.md) 和 [实验协议](EXPERIMENT_PROTOCOL.zh.md)。

## 重建完成后的数据范围

本次1,699行／次记录已全部完成；31,305个重建文件与各批完整配置、轨迹、状态和复判已独立保存。8,974个新NPZ均可禁用pickle读取；11,599个与历史预期字节相同的重建文件已补回原缺失路径。

**审计中的8,393个NPZ／SQLite／WKB引用全部哈希匹配，原始数组、轨迹、数据库和几何在此审计范围内没有缺口。** 仍缺10,641个旧JSON／YAML／TXT版本，它们涉及旧主机元数据和中间运行记录；新完整元数据位于重建目录，不冒充旧版本。此结论只覆盖明确抽取的哈希引用，不声称找回所有未记录的历史文件。

[重建统计](../reports/project_rebuild_20261023/reconstruction/REPORT.zh.md)、[完整核验](../reports/project_rebuild_20261023/reconstruction_verification.json)、[最终历史引用摘要](../reports/project_rebuild_20261023/historical_references_after_reconstruction_summary.json)分别说明新结果、工件完整性和旧版本差异。2次文件写入中断均从保存工件复判恢复，未重跑科学执行；对应错误日志保留。
