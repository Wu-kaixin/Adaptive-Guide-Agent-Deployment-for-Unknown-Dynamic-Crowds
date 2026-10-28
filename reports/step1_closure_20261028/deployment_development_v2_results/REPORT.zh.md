# 安全替代路径实验结果

完成：1296/1296，保存轨迹复判全部一致。

证据性质：development_new_inputs。旧动作回归不增加独立样本，所有旧失败保留。

| 策略 | 安全部署完成 | 完整任务成功 | 状态 |
|---|---:|---:|---|
| verified_q4_forward | 415 | 156 | {'PARTIAL_SERVICE': 259, 'FULL_SUCCESS': 156, 'PATH_NOT_FOUND': 17} |
| adaptive_forward | 428 | 156 | {'PARTIAL_SERVICE': 272, 'FULL_SUCCESS': 156, 'PATH_NOT_FOUND': 4} |
| adaptive_reverse | 432 | 156 | {'PARTIAL_SERVICE': 276, 'FULL_SUCCESS': 156} |

部署完成、完整覆盖和路径失败分开记账；失败短路程不算效率优势。

三策略共享目标和初态，逐步改变圆盘表示与串行顺序；所有边和实际运动区间保留独立解析验证。

```json
{
  "manifest_sha256": "d999dfd998a57b1ada084033bab11639b922baa1287522def0742bc82a2a88fa",
  "recorded": 1296,
  "scheduled": 1296,
  "evidence": "development_new_inputs",
  "all_replay_match": true,
  "new_independent_samples": 48,
  "by_policy": {
    "verified_q4_forward": {
      "statuses": {
        "PARTIAL_SERVICE": 259,
        "FULL_SUCCESS": 156,
        "PATH_NOT_FOUND": 17
      },
      "safe_deployments_completed": 415,
      "full_task_success": 156
    },
    "adaptive_forward": {
      "statuses": {
        "PARTIAL_SERVICE": 272,
        "FULL_SUCCESS": 156,
        "PATH_NOT_FOUND": 4
      },
      "safe_deployments_completed": 428,
      "full_task_success": 156
    },
    "adaptive_reverse": {
      "statuses": {
        "PARTIAL_SERVICE": 276,
        "FULL_SUCCESS": 156
      },
      "safe_deployments_completed": 432,
      "full_task_success": 156
    }
  },
  "safety_violations": 0,
  "program_errors": 0
}
```