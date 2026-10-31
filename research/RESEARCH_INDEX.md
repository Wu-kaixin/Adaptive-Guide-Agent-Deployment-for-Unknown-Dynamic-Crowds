# 研究索引

本索引把活动开发、冻结证据和研究资料连接起来。历史报告中的百分比不是当前代码的验证结果。

| 研究问题或用途 | 方法与假设 | 入口与对应资料 |
|---|---|---|
| 单群部署链路 | 静态人群、已知房间、全局观测；部署与独立评价分离 | [架构](../docs/ARCHITECTURE.zh.md)、[理论](../docs/STEP1_THEORY.zh.md)、[证据图谱](../docs/STEP1_EVIDENCE_MAP.zh.md) |
| 非凸边界、资源和需求 | 凸包与圆盘并集的协议边界、失败计账、demand 限制 | [覆盖几何契约](../docs/COVERAGE_GEOMETRY_CONTRACT.zh.md)、[实验协议](../docs/EXPERIMENT_PROTOCOL.zh.md)、原 `reports/` 冻结包 |
| 静态多群与有界观测误差 | 研究分支的固定模型；原参考服务证书仍未闭合 | [实现版本说明](README.md)、[固定最终报告](implementations/major-research-2806bd118290/reports/step1_final_20261029/REPORT.zh.md)、[交接说明](implementations/major-research-2806bd118290/docs/STEP1_FINAL_HANDOFF.zh.md) |
| 会议演示与汇报 | 展示材料引用对应冻结结果，不产生新样本 | [2026-10-30 演示目录](materials/presentations/PC2/step1_meeting_20261030/)、[2026-10-26 周报](materials/meetings/PC2/weekly_report_20261026/) |
| 演示来源与修改历史 | 生成脚本、图表转换收据、页码引用、校验记录 | [制作历史](materials/presentations/working-history/PC2/)、[高价值资料清单](indexes/HIGH_VALUE_ASSETS.csv) |
| 几何库诊断依据 | 87 项 GEOS 参考源码及许可；不自动合入控制器 | [诊断资料](materials/reviewed/diagnostics/PC2/.tmp/) |
| 失败记录与恢复 | 保留程序异常、科学失败和未执行记录 | [数据定位](../data/README.md)、[验证记录](../docs/VALIDATION.zh.md) |

本轮额外辨认了一个静态多群开发失败：`stall-exec/execution.json` 记录
`ABCGController` 缺少 `_track_note` 的程序异常，`motion_executed=false`。
它保存在资料目录与完整来源档案中，不作为成功执行，不因失败而删除。

44,175 项待审内容已获得逐项哈希与结构/上下文分类；20,064 项与分发包 RECORD 哈希一致。
19,936 项历史运行环境文件尚无该类哈希证明；3,113 项项目字节码有源码候选关系，387 项版本关系未闭合。
时间戳与大小只能支持候选关联。这些内容完整保留；分类完成不等于逐项科学语义得到证明。
152 项进一步内容审查涵盖演示支持、几何诊断、安装元信息、许可证、回归夹具和历史交付目录。

来源级检索、原始数据、失败和缺口见[数据说明](../data/README.md)。
更细的证据关系与审查深度保存在本机 `project-management/repository_rebuild_20261031/asset_review.sqlite`。
周报的 6 份 PDF（共 31 页）和 4 份 Word 文稿已做只读文字提取，修订后 final PDF 的 5 页已渲染检查。
版本、正文哈希和提取文件见 [文稿阅读索引](indexes/document_reading.json)。周报中的未来计划仍是当时的计划，
三篇论文设想不是已经完成的论文；原文中引用但未随来源提供的材料不据此补造。
未提供独立论文草稿或文献结论时不补写；可恢复性与研究结论成立是两项不同判断。
