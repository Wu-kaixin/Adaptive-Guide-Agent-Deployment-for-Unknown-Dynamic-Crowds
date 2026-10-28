# 安全替代路径实验结果

完成：1923/1923，保存轨迹复判全部一致。

证据性质：fixed_old_action_development_not_independent_confirmation。旧动作回归不增加独立样本，所有旧失败保留。

| 策略 | 安全部署完成 | 完整任务成功 | 状态 |
|---|---:|---:|---|
| verified_q4_forward | 1 | 0 | {'PATH_NOT_FOUND': 640, 'PARTIAL_SERVICE': 1} |
| adaptive_forward | 410 | 6 | {'PARTIAL_SERVICE': 404, 'FULL_SUCCESS': 6, 'PATH_NOT_FOUND': 231} |
| adaptive_reverse | 641 | 10 | {'PARTIAL_SERVICE': 631, 'FULL_SUCCESS': 10} |

部署完成、完整覆盖和路径失败分开记账；失败短路程不算效率优势。

三策略共享目标和初态，逐步改变圆盘表示与串行顺序；所有边和实际运动区间保留独立解析验证。

```json
{
  "manifest_sha256": "f88f9e154f948c5d106747e4f1f2edd984b0e62bea241104867d4efd183e18d1",
  "recorded": 1923,
  "scheduled": 1923,
  "evidence": "fixed_old_action_development_not_independent_confirmation",
  "all_replay_match": true,
  "new_independent_samples": 0,
  "by_policy": {
    "verified_q4_forward": {
      "statuses": {
        "PATH_NOT_FOUND": 640,
        "PARTIAL_SERVICE": 1
      },
      "safe_deployments_completed": 1,
      "full_task_success": 0
    },
    "adaptive_forward": {
      "statuses": {
        "PARTIAL_SERVICE": 404,
        "FULL_SUCCESS": 6,
        "PATH_NOT_FOUND": 231
      },
      "safe_deployments_completed": 410,
      "full_task_success": 6
    },
    "adaptive_reverse": {
      "statuses": {
        "PARTIAL_SERVICE": 631,
        "FULL_SUCCESS": 10
      },
      "safe_deployments_completed": 641,
      "full_task_success": 10
    }
  },
  "safety_violations": 0,
  "program_errors": 0
}
```