# Step 1基线整合审查说明

候选科学版本为 `f9743ab`，旧main为 `6d9bc09`。准备前差异为31提交、327文件；本轮只补入口与状态文档，保留研究提交历史，不把中间失败版本单独挑成main基线。此次PR范围是可追溯的静态Step 1实现，不表示整个Step 1研究完成。

## 分块审查顺序

| 块 | 主要入口 | 可合入的能力及审查重点 |
|---|---|---|
| 框架与冻结输入 | `src/udc/environment.py`、`crowd/`、`interfaces.py`、`experiments/config.py`／`runner.py` | 输入仅观察人员位置／半径／需求，初始化后冻结；配置、metrics、state、异常和独立工件完整记账 |
| 几何与导航正确性 | `boundary/`、`navigation/`、`deployment/admissible.py` | 完整线段、统一静态入域、实际坐标保留；相位仅显式调用，保留原曲线，有限搜索不充当不可行证明 |
| 执行与安全 | `controller/`、`safety/`、`evaluation/` | 实际区间安全、有限恢复、优先进展监测及独立验收；不放宽阈值、不承诺一般收敛 |
| 实验与证据 | `configs/step1/`、`experiments/`、`reports/`、`tests/` | 配对输入、固定资源、失败分母、版本／哈希；历史确认、协议重建和开发回归分开 |
| 覆盖表示契约 | `boundary/coverage.py`与[契约](COVERAGE_GEOMETRY_CONTRACT.zh.md) | 解析圆盘与q32／q16参考区别，原谓词与完整几何证据保留；只诊断，无epsilon接纳或默认改变 |

普通控制器仍从凸包开始；圆盘闭运算、成对约束求解和相位后处理属于显式协议。`projected_mass`是研究候选，RA-ABDC浓度调节未证明独立收益。进入main不把这些功能升级为通用推荐默认，也不增加Step2/3能力。

## 证据与合并前检查

当前本机科学源码的完整记录为175 passed /24.34 s，pip check通过；本次只改Markdown及审查元数据，源码／配置哈希保持不变。`f9743ab`的[Linux／Windows CI](https://github.com/Wu-kaixin/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/actions/runs/39255838311)均已通过安装、测试、依赖检查、50人部署示例及复判。PR最新头提交及合并测试的状态仍需分别检查，旧绿灯不替代新提交检查。

120行比较、101条实际轨迹和seed70拒绝保持原样。覆盖诊断175项测试不是175个独立人员样本；测试通过不是边界任意可行或动态containment已解决的证明。数据可用性和本机绝对路径限制见[数据说明](DATA_AVAILABILITY.zh.md)。

本轮还检查新文档相对链接、原跟踪JSON可解析性、运行代码对被删除旧路径的引用、原科学源码哈希和历史文件哈希。审查范围及实际检查结果见 `reports/step1_main_readiness/review_scope.json`。仅文档更新不重跑120、180、1620或完整历史矩阵。

## 旧目录删除

相对旧main共有26个历史删除：`ABCG-Simulation`22项脚本／派生结果，`Test`4项演示数据／笔记本。当前运行代码、测试、配置、实验入口及CI没有指向这两个旧目录的引用。它们被模块化 `src/udc`、薄CLI、标准配置和新笔记本取代，不作为当前安装或CI的必需依赖。

原文件及二进制数据仍可按 `6d9bc09:<path>` 的Git blob查询；本轮不再次删除、恢复、复制或清理原文件。完整删除列表、原blob与字节大小保留在审查清单，便于对照这次大范围PR的删除部分。历史清理不是本轮新增科学算法。

## 当前报告体积与保留决定

在准备基线 `f9743ab` 的Git树上，总文件约21.57 MB，其中reports约19.78 MB；最大文件是 `reports/kai_14900kf_checksums.json`，约4.05 MB。这是现有树大小，不是整个Git仓库历史大小。

较大的校验清单、汇总、失败图和9份历史NPZ是来源或失败证据，不在本次整合中删除、重命名或重写以缩小diff。那9份NPZ是既有历史例外，不代表所有原始轨迹随Git发布；后续新实验的原始数据仍只放runs。PR保留研究历史，避免把未迁入原数据伪装为已公开的可完整复现数据。

若以后需要瘦身，应单独设计证据发布／归档协议，保证哈希与报告可追溯；本轮不做Git历史重写、LFS迁移、全runs复制或backups。

## 合并定位

此PR交付静态单群、已知封闭房间、精确全局观测、无限通信、无crowd–guide动力学交互条件下的代码、有限执行规则与证据。主分支仍允许记录明确科学失败和适用限制。创建PR供审查，当前不自动合并main；后续实验继续在研究分支开展。
