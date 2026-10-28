# 本轮实验状态

更新时间：2026-10-28T03:05:02+09:00。

阶段：**protocol_stopped_and_reported**；原确认记录 **16,474/17,280** 行。
分群数量任务计数：`{'K1': 4320, 'K2': 4320, 'K3': 4320, 'K5': 3514}`。正式确认完整状态：**False**。

当前并发上限32个计算进程、每进程数值线程1；保留原输入、主指标与样本量。
使用原封存计算函数及独立封存加速后端：implicit_complete_visibility_strict_relax_v2；已完成结果已核验存档，新增记录另有后端来源凭据。
一次性后台监督进程只等待原运行结束，随后离线复判并生成报告；不会重启或重试科学任务。

已完成的开发结果见[开发报告](DEVELOPMENT.zh.md)和[检查凭据](validation.json)。
机器可读状态见[live_status.json](live_status.json)。

最终确认与评估见[REPORT.zh.md](REPORT.zh.md)。
