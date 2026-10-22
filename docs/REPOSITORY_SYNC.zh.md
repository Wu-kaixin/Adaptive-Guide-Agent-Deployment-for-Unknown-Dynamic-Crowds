# 2026-10-23 仓库同步与健康检查

本轮按用户要求，把 `main`、`Major-Research-Branches` 和 `integration/step1-framework`
统一到最新有效项目。同步范围是Git跟踪的代码、协议、报告、重建清单与维护工具。
`.venv/`、`runs/`、`backups/` 继续在本机保存，不上传环境或把本机原始数据误称为GitHub克隆自带数据。
数据范围和历史元数据缺口见[重建日志](PROJECT_REBUILD.zh.md)和[数据可用性](DATA_AVAILABILITY.zh.md)。

## 同步前实际远程状态

| 分支 | 远程提交 |
|---|---|
| Major-Research-Branches | c807c85ff873277fe9812747e39e47723c252920 |
| main | 45224a642ec1d36e8ccb80388751ab5ec43a379b |
| integration/step1-framework | db7d4e4d649cf2fb52504b2b27d8bdfb076d9103 |

最新科研生产源码、配置和测试采用c807c85；本地另有独立环境设置、重建工件清单、
Notebook路径修正与文档更新。按实际文件内容对比，最新远程文件没有本地缺失。
旧本地研究分支指向c37a023，不能据旧索引把已迁入但尚未暂存的后续文件误判为删除。

旧main额外保留一次合并提交，其项目树与c37a023一致。
旧integration额外两次提交含旧Test笔记、旧coverage控制器和六个旧试验配置，
其中quantile_hotspot按观测分位生成权重；当前显式demand_region协议的定义不同。
这些旧入口已被后续框架取代，不向当前算法移植旧语义；历史通过提交父链和备份保留。
当前路线图已删除“需求／资源尚未开展”的过时状态，历史冻结报告中的结论和失败分母保持原记录。

## 历史与本机保护

同步前建立 `backups/github_sync_20261023/before_sync.bundle`，通过Git bundle验证；
另存620个非忽略文件的ZIP，每项按原SHA-256复核。
清单、原分支指针和差异审计位于 `runs/github_sync_20261023/`。
最终提交以最新科研分支为基础，并保留旧main与integration的祖先关系，
三个现存分支以普通快进、原子推送发布，不强推、不删除分支、不清理悬空历史对象。

## 健康检查入口

活动环境是本项目 `.venv`，独立CPython3.12；不使用Conda。
CI在Ubuntu和Windows分别创建项目 `.venv`，执行安装、全量测试、pip check、
50人示例、保存轨迹复判，以及跟踪文件／语法／文档链接／工作区检查。
本机再对31,305个重建文件与8,393条历史原始数据引用核验哈希。

```powershell
.\.venv\Scripts\python.exe scripts/check_repository_health.py --require-clean --reconstruction --output runs/github_sync_20261023/local_health.json
git fsck --full --strict
git ls-remote --heads origin
```

`--reconstruction`依赖本机冻结数据；普通克隆的CI不声称持有这些忽略的数据。
检查结果见[验证记录](VALIDATION.zh.md)。最终本机提交／项目树、三个远程分支的实际SHA和
对应新CI状态另存本机 `runs/github_sync_20261023/final_health.json`，避免把提交自身SHA写入提交而形成递归。
远程运行以[GitHub Actions](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions)的实际结果为准，旧绿灯不代替新提交检查。
