# 静态扩展 Step 1 工程基线与研究结果发布

本发布同步到`Major-Research-Branches`，保留原停止确认、68条历史失败及全部旧哈希锁。
最后一次独立评估的执行、全量复判、公共API与完整事件边界恢复通过；原参考服务证书保持隔离，方法独特性及全部研究目标未完成。
发布打包、测试和CI示例属于工程检查，不增加独立研究样本，也不重新开启研究目标或定时器。

## 成果导航

| 阅读目的 | 入口 |
|---|---|
| 当前数字、成功与部分服务、方法零增益及限制 | [最终评估原件](../step1_final_20261029/REPORT.zh.md) |
| 完整精度和逐资源／扰动／方法指标 | [metrics.json](../step1_final_20261029/metrics.json) |
| F1–F12逐项完成状态 | [验收表](../step1_final_20261029/REQUIREMENTS.zh.md) |
| Step2/3接口和准入条件 | [交接说明](../../docs/STEP1_FINAL_HANDOFF.zh.md) |
| 一般误差证书前提缺口与隔离 | [实现前提审计](../../docs/STEP1_BUFFER_ENCLOSURE_AUDIT.zh.md) |
| 方法定位及未完成的比较／消融 | [原文初审](../step1_foundation_20261029/LITERATURE_AND_BASELINES.zh.md) |
| 后续条件外包及质量预算研究记录 | [研究状态](../step1_foundation_20261029/STATUS.zh.md) |
| 本次打包、构建、测试和数据恢复验证 | [发布前验证](VALIDATION.zh.md) |
| 旧版本公开证据和原失败记录 | [历史审查包](../step1_review_20261029/README.md) |

当前结果：48个物理输入、1296条连续序列全部交付、64944条记录及全量复判一致；30525完整成功、34419部分服务。
1211851运动区间不安全计数0；API144会话／7344事件／576次完整事件边界恢复一致。
当前门控无认证保留，三策略对应轨迹相同、移动收益0；不得转用旧冻结代码的保持收益。
学术独特性、一般误差服务保证、真实进程崩溃和动态／实时／有限通信backend均未验收。

## 数据分层与获取方式

| 数据层 | 获取方式 | 能验证什么 |
|---|---|---|
| 当前源码、配置、测试、研究报告与旧公开包 | 克隆本研究分支 | 当前工程检查及历史公开凭据 |
| 新公开包：原字节元数据、48个输入和观测序列、两份封存源码、复判摘要及receipt | Git内[evidence/final_metadata_inputs_sources.tar.gz](evidence/final_metadata_inputs_sources.tar.gz) | 包内每个文件的SHA-256、封存输入和源码；不含完整轨迹 |
| 完整最终运行目录，约1.54GB未压缩 | 向项目维护者获取`step1_final_raw_20261029.tar.gz`；当前没有公开原始轨迹下载URL | 完整原始轨迹、状态、观测和receipt链的数据完整性；配齐环境后可作只读科学复判 |
| 其他基础研究批次的完整运行数据 | 维护者本地`runs/step1_foundation_20261029/`，按具体报告索取 | 对应条件原型／反例研究；本公开包不声称包含这些批次的全部运行工件 |

完整最终数据归档已保存在维护者本地`backups/step1_publish_20261030/step1_final_raw_20261029.tar.gz`，不进入普通Git。
公开包与完整归档的SHA-256、字节数和成员数在[publication_manifest.json](publication_manifest.json)中。
原报告、最终封印和旧路径均保留原字节；归档成员使用仓库根目录相对路径，获取后按下面映射恢复，不改旧锁。

## 纯 Git 克隆验证

先按根README建立本项目`.venv`。下列脚本只使用标准库，不运行人群／机器人实验：

```powershell
./.venv/Scripts/python.exe scripts/package_step1_final.py
./.venv/Scripts/python.exe scripts/package_step1_review.py
```

新验证器检查已发布报告、原最终封印、公开归档SHA-256及每个成员的原字节哈希。
输出明确包含`raw_trajectories_in_public_archive=false`及`full_local_data_verified=false`。
纯Git验证通过不等于重新执行64944条轨迹的物理验收。
新源码、配置和工程测试不等于已取得所有历史数据；包内的冻结源码是该实验实际使用的版本。

可将公开子集恢复到一个**不存在的新目录**，便于离线阅读原始元数据、输入和源码：

```powershell
./.venv/Scripts/python.exe scripts/package_step1_final.py --restore-public runs/final_public_data
```

这会创建`runs/final_public_data/runs/step1_final_evaluation_20261029/`，不生成缺失轨迹，也不重跑实验。

## 获得完整原始归档后的验证

将维护者提供的归档放在任意可读取位置，先按发布清单核验SHA-256，再恢复到新目录：

```powershell
Get-FileHash 'D:/data/step1_final_raw_20261029.tar.gz' -Algorithm SHA256
./.venv/Scripts/python.exe scripts/package_step1_final.py --restore-raw 'D:/data/step1_final_raw_20261029.tar.gz' --destination runs/final_raw_data
./.venv/Scripts/python.exe scripts/package_step1_final.py --full-local --data-root runs/final_raw_data
```

恢复器会先检查归档SHA-256，再检查成员类型、重复名与相对路径，不覆盖现有目标。
`--data-root`是包含`runs/`子目录的仓库式数据根；完整原件本来就在当前项目时直接运行`--full-local`。
完整模式会校验最终索引、receipt所指的轨迹／观测／状态，以及原输入／源码哈希；缺失原件会失败，不将元数据子集当完整数据通过。

## 完整科学复判与范围

字节完整性验证不重新计算决策或物理指标。需要完整科学复判时，使用归档中的`source_snapshot/src`与其锁定环境，
调用`udc.analysis.sequential_audit.audit(study, workers, new_output)`，输出必须是新的目录。
该入口执行只读决策和轨迹评价，不重新规划运动；科学组件仍复用该冻结版本，不宣称第二套实现或形式证明。
不要对原目录调用`prepare`／`run`或更新manifest／seal来迁就当前环境；原始绝对路径仅为历史来源，不授权从新主机同名路径借用数据。
保留5000–5029，不补抽失败输入、不篡改服务／安全／截止阈值。

本发布不包含`.venv`或其他机器的环境。按根README重建本项目环境；原始环境／包版本见封存manifest，
Windows实测锁为`requirements/locks/windows-py312.txt`。原生GEOS条件研究有特定Windows／GEOS版本限制，不能把平台跳过当测试通过。
