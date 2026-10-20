# 编排审计补充：保留程序错误后继续未开始行

原冻结提交57688a1a。首组六行通过；第二阶段在完成总计52行时，wide_u seed77 N21两方法触发继承自demand_confirmation的`exact_pairing`检查。两条运动分别31.8s和37.5s，原runner九项criteria全部通过；所有人员／半径／需求／热点／房间／初态／目标／曲线数组精确一致，唯一不同是最终assignment。

定位到原控制器既有`path_recompute → reachable_reassignment → priority_yield`有限恢复流程。两行都发生一次合法重指派。事件的before／after目标哈希保持不变，初始assignment哈希等于预先导航，合法重指派的安装成功后哈希等于最终轨迹assignment；共三次预算内恢复。原编排误将最终指派与初始指派相同当成恒常条件。没有发现实际安全违反。

这是编排断言错误，不是人员输入变化或新控制策略。现仅将该断言改为：预定初始assignment哈希→每个恢复事件before／after连续→目标哈希固定→仅accepted且installation_succeeded的reachable_reassignment允许变更→最终assignment匹配。无事件时仍要求初终相同。科学模块61文件继续核对原执行字节哈希；不改目标、路径、控制器、安全标准、恢复预算或运行预算。

原两行已被旧异常处理将metrics的termination_status改为EXPERIMENT_ERROR、success=false并改写state；其criteria和轨迹仍保存。不能声称原在线metrics未受影响，也不凭重建字段覆盖它们。52行及停止现场全部文件做补充检查点哈希。两行在主台账中继续为程序错误，独立物理验收另报成功，online/offline success不一致明确记为2，不把它们删除或自动重标成功。后续异常处理仅新增编排错误侧文件，不再改写原runner metrics。

不重跑任何已完成行，不覆盖原protocol、manifest、input锁、run_stop或阶段摘要。补充提交后用`--stage amend`保存当前源码、52行检查点、原protocol哈希和诊断，再用`--stage continue --workers 2`只处理剩余308行。原360行和60条数量诊断、全部初态及严格复用决定保持原锁。最终报告分别给出完整流程记账与独立轨迹验收，且披露两次源码版本（仅编排审计变化）。

新增回归测试要求有完整合法事件才接受重指派；无事件、错误策略或安装失败都拒绝。普通科学失败继续保留；实际安全违反仍暂停所有尚未开始行。
