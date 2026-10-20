# 数据可用性与复现范围

本仓库同时保存可运行的Step 1代码和多轮研究记录。代码版本、依赖环境、冻结输入、实际轨迹和报告摘要分别追溯；克隆仓库不等于取得全部历史原始数据。

## 新克隆可直接开展的检查

安装Python3.12及项目依赖后，可运行测试、自带网格／JuPedSim场景和新输出的独立复判：

```powershell
python -m pip install -e ".[dev,analysis]"
python -m pytest -q
python -m pip check
# 首次运行使用尚不存在的输出目录。
python run.py --config configs/step1/static_50.yaml --output runs/first_run --no-plots
python run.py --reevaluate runs/first_run
```

50人示例的人员网格与Guide初态来自配置，不需要历史SQLite。其配置参数是示例自己的参数，不与非凸比较的1.3 m偏移混用。Linux／Windows CI均执行安装、测试、依赖检查及该示例与复判。包依赖范围见pyproject，历史固定版本见各报告；“当前安装能运行”不等于恢复某轮依赖和逐坐标结果。

本仓库不分发虚拟环境。已有合适uv环境可沿用，不能复制另一台机器的venv，也不需要为阅读报告升级科学依赖。

## Git中有什么

| 内容 | 新克隆可做什么 | 范围限制 |
|---|---|---|
| `src/udc`、配置、测试、CLI、CI | 安装、运行自带示例、验证实现 | 受Step 1静态单群／已知封闭场地等条件限制 |
| `reports`的报告、JSON／CSV、图像和哈希清单 | 阅读冻结结论、状态、配对统计、来源与失败证据 | 汇总及哈希清单不包含其引用的每条原始轨迹 |
| `reports/step1_wide_u_replan_cases`的9份历史NPZ及记录 | 检查这9份原数组和指标 | 历史例外，不代表全部runs；某些配置／回放仍引用旧主机绝对路径 |
| `configs/step1/*`、`experiments/*`的历史协议 | 检查冻结因子和生成规则 | 配置存在不表示数据已随Git发布，也不授权重新使用已消费seed提供独立确认 |

新实验的唯一原始数据保存在被Git忽略的 `runs/`。已跟踪的少量历史NPZ保留其既有位置以保持证据和路径引用，不在本次整合中再次复制或迁移。`.venv/`、`results/`、`backups/`也不是本次新建的数据分发渠道。

## 历史实验需要哪些本机工件

| 记录 | Git中的依据 | 额外数据与版本要求 |
|---|---|---|
| seed55–74，60输入／120行边界比较 | [完整报告](../reports/step1_boundary_estimator_comparison_v1/REPORT.zh.md)、120行表、60输入哈希映射 | 本机 `runs/step1_boundary_estimator_protocol/future_inputs` 与 `future`，包括NPZ、SQLite、原目标、WKB、轨迹和事件；执行源码为3220233，算法历史基线为0ef6e1f |
| 覆盖几何契约，102份几何／408项检查 | [契约报告](../reports/step1_coverage_geometry_contract/REPORT.zh.md)及精确证书 | 原比较的完整保存几何／输入，以及本机 `runs/step1_coverage_geometry_contract/audit_v1`；只做诊断，不新增部署 |
| v3目标域修复开发回归 | [报告](../reports/step1_target_domain_consistency/REPORT.zh.md)、141／33／6和哈希对照 | 本机协议重建输入与原曲线／目标；缺少原v3原始输入，不能称作原180行独立确认的复判 |
| 更早完整73任务 | 各轮来源说明及19任务补充核验 | 完整原输入未迁入，不宣称73/73重现 |
| 正式凸曲线1620任务 | [冻结摘要](../reports/formal_step1_summary.json)、校验清单及[验证记录](VALIDATION.zh.md) | 原冻结版本f5b5d9c及对应原始工件；不是当前源码的全量回归，不在开发中重跑5000–5029 |

以上路径说明本机数据布局，不表示新克隆自动带有这些目录。原记录可能包含 `E:\...` 或其他主机绝对路径；原始路径、配置和文件哈希都是来源证据，不能为了让命令通过而改写旧配置、删除锁检查或填造新哈希。异机逐轨迹复核需要另行明确经哈希校验的数据读取／路径映射方案，本次只记录需求，不执行迁移。

## 三类操作不可混同

1. **阅读摘要**：可直接打开Git中的Markdown、CSV和JSON，不生成输入或运行控制器。
2. **读取原工件再汇总／复判**：需要对应原始文件和兼容的读取环境。先核对版本、路径与哈希；需要派生输出时使用新目录，不覆盖Git中的冻结报告。部分报告脚本是Windows主机工具，并非通用迁移入口。
3. **重新生成或执行实验**：是新的操作，不能称为读取旧数据。`--prepare`会建协议目录，`--freeze-future`会生成输入，`--run-future`会执行任务；120行已完成，seed55–74已消费，不再次调用这些入口来制造新确认。

边界比较的 `protocol.json` 准备provenance、`baseline_commit`和实际执行提交各有含义，保留原记录。当前新增覆盖诊断后的完整源码哈希不同于原比较，旧执行入口会按锁拒绝版本串用；不能因此改写原锁。16行开发检查、重建v3开发回归和完整新seed比较分别记账，不合并样本或追认历史结论。

## 本次整合的数据处理

仅更新入口文档、数据说明和审查清单；不生成或复制runs、不新建backups、不升级依赖、不删除历史报告或重跑实验。旧main中的脚本／演示数据清理已发生于研究历史，删除列表、旧Git blob及当前大文件清单见[整合审查说明](STEP1_INTEGRATION_REVIEW.zh.md)。

## 2026-10-20资源响应工件

[资源响应报告](../reports/step1_nonconvex_resource_response_v1/REPORT.zh.md)及360行台账、60条数量诊断、输入／manifest哈希、复判与统计保存在Git。唯一新增Guide初态、184条新增运动、目标、事件和原始状态在本机`runs/step1_nonconvex_resource_response_v1/`；另92条运动只读引用`runs/step1_nonconvex_demand_confirmation_v1/`，不复制原runs。克隆报告不等于取得这些原始轨迹、旧SQLite和全精度输入。

运行前57688a1a、编排审计补充125fd653分别追溯；原52行检查点及两条程序错误不可覆盖或当成重新执行成功。独立物理验收275条通过与完整流程273成功分开记账。当前只读分析入口核验除自身CSV／图例导出层以外的全部执行源码，原执行和重新复判须使用相应冻结提交及经哈希验证的本机依赖；不得改写原锁绕过版本检查。原seed75–94已暴露，本轮资源响应也不提供新的独立人员样本。
