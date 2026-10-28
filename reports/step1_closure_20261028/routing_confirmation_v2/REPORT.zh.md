# 安全替代路径实验结果

完成：8640/8640，保存轨迹复判全部一致。

证据性质：prospective_confirmation。旧动作回归不增加独立样本，所有旧失败保留。

| 策略 | 安全部署完成 | 完整任务成功 | 状态 |
|---|---:|---:|---|
| verified_q4_forward | 2764 | 1044 | {'PARTIAL_SERVICE': 1720, 'FULL_SUCCESS': 1044, 'PATH_NOT_FOUND': 116} |
| adaptive_forward | 2851 | 1045 | {'PARTIAL_SERVICE': 1806, 'FULL_SUCCESS': 1045, 'PATH_NOT_FOUND': 29} |
| adaptive_reverse | 2880 | 1045 | {'PARTIAL_SERVICE': 1835, 'FULL_SUCCESS': 1045} |

部署完成、完整覆盖和路径失败分开记账；失败短路程不算效率优势。

三策略共享目标和初态，逐步改变圆盘表示与串行顺序；所有边和实际运动区间保留独立解析验证。

```json
{
  "manifest_sha256": "c1322a6bfa077b51e064fa7f3afc3413595d27897f12da62fd12c0da84b5e802",
  "recorded": 8640,
  "scheduled": 8640,
  "evidence": "prospective_confirmation",
  "all_replay_match": true,
  "new_independent_samples": 320,
  "by_policy": {
    "verified_q4_forward": {
      "statuses": {
        "PARTIAL_SERVICE": 1720,
        "FULL_SUCCESS": 1044,
        "PATH_NOT_FOUND": 116
      },
      "safe_deployments_completed": 2764,
      "full_task_success": 1044
    },
    "adaptive_forward": {
      "statuses": {
        "PARTIAL_SERVICE": 1806,
        "FULL_SUCCESS": 1045,
        "PATH_NOT_FOUND": 29
      },
      "safe_deployments_completed": 2851,
      "full_task_success": 1045
    },
    "adaptive_reverse": {
      "statuses": {
        "PARTIAL_SERVICE": 1835,
        "FULL_SUCCESS": 1045
      },
      "safe_deployments_completed": 2880,
      "full_task_success": 1045
    }
  },
  "safety_violations": 0,
  "program_errors": 0,
  "predeclared_primary_and_secondary_contrasts": {
    "adaptive_reverse_minus_verified_q4_forward": {
      "estimate": 0.04027777777777778,
      "ci95": [
        0.035069444444444445,
        0.045486111111111116
      ],
      "independent_blocks": 320,
      "strata": 16
    },
    "adaptive_reverse_minus_adaptive_forward": {
      "estimate": 0.010069444444444445,
      "ci95": [
        0.006944444444444445,
        0.013194444444444446
      ],
      "independent_blocks": 320,
      "strata": 16
    }
  }
}
```