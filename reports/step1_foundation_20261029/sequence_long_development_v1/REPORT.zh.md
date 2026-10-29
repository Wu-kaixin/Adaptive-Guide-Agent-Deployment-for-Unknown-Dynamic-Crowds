# 200次静态重观测：数量与相位切换

记录：64908/64908；全部保存轨迹和决策重建一致。

每个方法沿自己的实际状态连续推进。普通部署失败结束受影响序列，其余帧明确未执行，不重置、不补抽。

服务门控使用原参考支持域，任意独立/相关有界误差下仅在充分条件通过时认证；不能认证回退贪心。

主指标/完整分母界及按资源/扰动模式统计见下方；短路程必须与声明全部更新完成共同解释。

```json
{
  "manifest_sha256": "2fc274e7030f943cce8792d7959cf919856a5c4f3e13b4c61d79ff6b63219716",
  "summary": {
    "stopped": false,
    "completed_units": 36,
    "scheduled_units": 36,
    "recorded_step_records": 64908,
    "scheduled_step_records": 64908,
    "not_recorded_steps": 0,
    "statuses": {
      "PARTIAL_SERVICE": 32513,
      "FULL_SUCCESS": 32395
    },
    "orchestration_error_units": 0,
    "sequence_completed": 324,
    "episodes_recorded": 324,
    "independent_physical_blocks": 12
  },
  "audit": {
    "manifest_sha256": "2fc274e7030f943cce8792d7959cf919856a5c4f3e13b4c61d79ff6b63219716",
    "checked_step_records": 64908,
    "scheduled_step_records": 64908,
    "mismatches": [],
    "all_match": true,
    "certificate_frames": 18387,
    "independent_physical_blocks": 12,
    "evidence": "development_long_horizon_not_confirmation",
    "by_method": {
      "recompute_all": {
        "episodes": 108,
        "completed_episodes": 108,
        "accepted_frames": 0,
        "fallback_frames": 0,
        "count_switch_events": 297,
        "nontrivial_hold_frames": 0
      },
      "phase_guard": {
        "episodes": 108,
        "completed_episodes": 108,
        "accepted_frames": 9133,
        "fallback_frames": 12467,
        "count_switch_events": 297,
        "nontrivial_hold_frames": 0
      },
      "counts_phase_guard": {
        "episodes": 108,
        "completed_episodes": 108,
        "accepted_frames": 9254,
        "fallback_frames": 12346,
        "count_switch_events": 210,
        "nontrivial_hold_frames": 8148
      }
    }
  },
  "resource_pattern_method_cells": [
    {
      "resource": "insufficient",
      "pattern": "independent_jitter",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 219.30001625557304,
      "conditional_mean_event_worst_uncovered": 0.4419113651423256,
      "count_switch_events_all_recorded": 28,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "insufficient",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 219.30001625557304,
      "conditional_mean_event_worst_uncovered": 0.4419113651423256,
      "count_switch_events_all_recorded": 28,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 2400
    },
    {
      "resource": "insufficient",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 219.30001625557304,
      "conditional_mean_event_worst_uncovered": 0.4419113651423256,
      "count_switch_events_all_recorded": 28,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 2400
    },
    {
      "resource": "insufficient",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.24501964866342005,
      "conditional_mean_event_worst_uncovered": 0.44336122886465984,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "insufficient",
      "pattern": "slow_common_bias",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.24501964866342005,
      "conditional_mean_event_worst_uncovered": 0.44336122886465984,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 2400
    },
    {
      "resource": "insufficient",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.24501964866342005,
      "conditional_mean_event_worst_uncovered": 0.44336122886465984,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 2400
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 328.6580726149673,
      "conditional_mean_event_worst_uncovered": 0.4429230039342373,
      "count_switch_events_all_recorded": 40,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 328.6580726149673,
      "conditional_mean_event_worst_uncovered": 0.4429230039342373,
      "count_switch_events_all_recorded": 40,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 2400
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 328.6580726149673,
      "conditional_mean_event_worst_uncovered": 0.4429230039342373,
      "count_switch_events_all_recorded": 40,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 2400
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 357.6773380171838,
      "conditional_mean_event_worst_uncovered": 0.01256587786671496,
      "count_switch_events_all_recorded": 14,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 201.0399920104363,
      "conditional_mean_event_worst_uncovered": 0.01256587786671496,
      "count_switch_events_all_recorded": 14,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 1323
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 187.68572573652537,
      "conditional_mean_event_worst_uncovered": 0.012565572000090338,
      "count_switch_events_all_recorded": 14,
      "nontrivial_hold_frames": 901,
      "fallback_frames": 1322
    },
    {
      "resource": "moderate",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.5841141241142807,
      "conditional_mean_event_worst_uncovered": 0.013217256653829543,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "moderate",
      "pattern": "slow_common_bias",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.5213808160363844,
      "conditional_mean_event_worst_uncovered": 0.013217256653829543,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 1200
    },
    {
      "resource": "moderate",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.41430218297761545,
      "conditional_mean_event_worst_uncovered": 0.013217256653829543,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 1200,
      "fallback_frames": 1200
    },
    {
      "resource": "moderate",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 282.45453566966415,
      "conditional_mean_event_worst_uncovered": 0.013057405682622076,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "moderate",
      "pattern": "oscillatory_shape_bias",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 263.1769676669536,
      "conditional_mean_event_worst_uncovered": 0.013057405682622076,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 1240
    },
    {
      "resource": "moderate",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 238.79218345254276,
      "conditional_mean_event_worst_uncovered": 0.013057405682622076,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 1098,
      "fallback_frames": 1162
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 795.7981547172508,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 135,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 532.1647998117164,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 135,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 584
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 396.45513904403066,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 90,
      "nontrivial_hold_frames": 1185,
      "fallback_frames": 584
    },
    {
      "resource": "sufficient",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.9714194924022218,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "sufficient",
      "pattern": "slow_common_bias",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.6993646669264834,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 400
    },
    {
      "resource": "sufficient",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.2426680767079148,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 2000,
      "fallback_frames": 400
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 503.8227319606238,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 80,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 449.35505064925195,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 80,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 520
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 196.58590490285937,
      "conditional_mean_event_worst_uncovered": 1.4802973661668753e-16,
      "count_switch_events_all_recorded": 38,
      "nontrivial_hold_frames": 1764,
      "fallback_frames": 478
    }
  ],
  "cross_method_original_service_comparison": {
    "joint_complete_sequences": 108,
    "mean_event_worst_gap_difference": -3.398518051330364e-08,
    "maximum_event_worst_gap_difference": 0.0,
    "scope": "empirical comparison with external recompute method; certificate compares own-state greedy, not a different counterfactual history"
  },
  "reference": "ORIGINAL_WORLD_REFERENCE",
  "general_error_model": "arbitrary bounded independent or correlated person error; observation-certified group identity",
  "scope": "200 accepted observation indices; no reset within sequence; not asymptotic stability or dynamic-crowd proof"
}
```

有限序列结果不是任意时间的收敛或受限通信稳定性证明。Step2/3仍需独立动力学与信息年龄条件。