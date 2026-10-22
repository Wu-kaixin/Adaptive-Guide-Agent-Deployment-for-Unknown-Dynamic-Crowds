# 本次重建数据结果

全部为2026-10-23数据重建／开发回归，不增加独立确认样本，不改写原报告。

| 批次 | 完成行 | 成功行 | 状态 | 冻结源码 |
|---|---:|---:|---|---|
| record_v1 | 45 | 35 | SUCCESS: 35, INITIALIZATION_INVALID: 9, PATH_UNREACHABLE: 1 | `2c12886c` |
| confirmation_v2 | 90 | 59 | SUCCESS: 59, REPLAN_EXHAUSTED: 4, INITIALIZATION_INVALID: 27 | `a0781c56` |
| confirmation_v3 | 180 | 138 | BOUNDARY_INVALID: 6, SUCCESS: 138, PATH_UNREACHABLE: 3, INITIALIZATION_INVALID: 33 | `9427a2e9` |
| demand_pilot | 36 | 24 | SUCCESS: 24, PLAN_SEARCH_EXHAUSTED: 12 | `4b2b199f` |
| demand_confirmation | 240 | 184 | BOUNDARY_INVALID: 28, SUCCESS: 184, INITIALIZATION_INVALID: 28 | `125fd653` |
| resource_response | 360 | 275 | BOUNDARY_INVALID: 42, SUCCESS: 275, INITIALIZATION_INVALID: 42, PATH_UNREACHABLE: 1 | `125fd653` |
| resource_confirmation | 360 | 275 | SUCCESS: 275, INITIALIZATION_INVALID: 78, BOUNDARY_INVALID: 6, TIMEOUT: 1 | `0433bb19` |
| recovery_regression | 360 | 276 | BOUNDARY_INVALID: 42, SUCCESS: 276, INITIALIZATION_INVALID: 42 | `865e6a48` |

相位归因、恢复A/B观测和截止诊断另见对应重建目录；原件缺口、字节一致的重建匹配和完整性核验分别保存。
两次文件写入中断通过保存工件复判恢复，科学执行重试0次。
