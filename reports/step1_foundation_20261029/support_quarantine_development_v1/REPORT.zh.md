# 50次静态重观测：数量与相位切换

记录：4077/4077；全部保存轨迹和决策重建一致。

每个方法沿自己的实际状态连续推进。普通部署失败结束受影响序列，其余帧明确未执行，不重置、不补抽。

本批支持域包含证明未闭合，两个门控全部显式不可认证并回退贪心；认证帧0。
270组匹配chunk的三方法轨迹文件逐字节相同，当前活动版本没有保持收益。
该回退回归不恢复支持域证明，见docs/STEP1_BUFFER_ENCLOSURE_AUDIT.zh.md。

主指标/完整分母界及按资源/扰动模式统计见下方；短路程必须与声明全部更新完成共同解释。

```json
{
  "manifest_sha256": "ecabda9a50e0b3ddfbab1cfcbc40958085dde4c1a245b256ae9affad2f871cf4",
  "summary": {
    "stopped": false,
    "completed_units": 9,
    "scheduled_units": 9,
    "recorded_step_records": 4077,
    "scheduled_step_records": 4077,
    "not_recorded_steps": 0,
    "statuses": {
      "PARTIAL_SERVICE": 2718,
      "FULL_SUCCESS": 1359
    },
    "orchestration_error_units": 0,
    "sequence_completed": 81,
    "episodes_recorded": 81,
    "independent_physical_blocks": 3
  },
  "audit": {
    "manifest_sha256": "ecabda9a50e0b3ddfbab1cfcbc40958085dde4c1a245b256ae9affad2f871cf4",
    "checked_step_records": 4077,
    "scheduled_step_records": 4077,
    "mismatches": [],
    "all_match": true,
    "certificate_frames": 0,
    "independent_physical_blocks": 3,
    "evidence": "development_reused_inputs_after_buffer_proof_quarantine",
    "by_method": {
      "recompute_all": {
        "episodes": 27,
        "completed_episodes": 27,
        "accepted_frames": 0,
        "fallback_frames": 0,
        "count_switch_events": 41,
        "nontrivial_hold_frames": 0
      },
      "phase_guard": {
        "episodes": 27,
        "completed_episodes": 27,
        "accepted_frames": 0,
        "fallback_frames": 1350,
        "count_switch_events": 41,
        "nontrivial_hold_frames": 0
      },
      "counts_phase_guard": {
        "episodes": 27,
        "completed_episodes": 27,
        "accepted_frames": 0,
        "fallback_frames": 1350,
        "count_switch_events": 41,
        "nontrivial_hold_frames": 0
      }
    }
  },
  "resource_pattern_method_cells": [
    {
      "resource": "insufficient",
      "pattern": "independent_jitter",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 136.50083879054247,
      "conditional_mean_event_worst_uncovered": 0.4694724543450703,
      "count_switch_events_all_recorded": 13,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "insufficient",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 136.50083879054247,
      "conditional_mean_event_worst_uncovered": 0.4694724543450703,
      "count_switch_events_all_recorded": 13,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "insufficient",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 136.50083879054247,
      "conditional_mean_event_worst_uncovered": 0.4694724543450703,
      "count_switch_events_all_recorded": 13,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "insufficient",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.20630013316149323,
      "conditional_mean_event_worst_uncovered": 0.46712212968897554,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "insufficient",
      "pattern": "slow_common_bias",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.20630013316149323,
      "conditional_mean_event_worst_uncovered": 0.46712212968897554,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "insufficient",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.20630013316149323,
      "conditional_mean_event_worst_uncovered": 0.46712212968897554,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 145.80433429482687,
      "conditional_mean_event_worst_uncovered": 0.4690777687730722,
      "count_switch_events_all_recorded": 10,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 145.80433429482687,
      "conditional_mean_event_worst_uncovered": 0.4690777687730722,
      "count_switch_events_all_recorded": 10,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 145.80433429482687,
      "conditional_mean_event_worst_uncovered": 0.4690777687730722,
      "count_switch_events_all_recorded": 10,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 92.65419034618162,
      "conditional_mean_event_worst_uncovered": 0.021063298167957295,
      "count_switch_events_all_recorded": 2,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 92.65419034618162,
      "conditional_mean_event_worst_uncovered": 0.021063298167957295,
      "count_switch_events_all_recorded": 2,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 92.65419034618162,
      "conditional_mean_event_worst_uncovered": 0.021063298167957295,
      "count_switch_events_all_recorded": 2,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "moderate",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.6920420937676882,
      "conditional_mean_event_worst_uncovered": 0.022351427646254127,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "moderate",
      "pattern": "slow_common_bias",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.6920420937676882,
      "conditional_mean_event_worst_uncovered": 0.022351427646254127,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "moderate",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.6920420937676882,
      "conditional_mean_event_worst_uncovered": 0.022351427646254127,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "moderate",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 68.95763972359869,
      "conditional_mean_event_worst_uncovered": 0.021461630113509857,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "moderate",
      "pattern": "oscillatory_shape_bias",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 68.95763972359869,
      "conditional_mean_event_worst_uncovered": 0.021461630113509857,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "moderate",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 68.95763972359869,
      "conditional_mean_event_worst_uncovered": 0.021461630113509857,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 331.0485836031683,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 16,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 331.0485836031683,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 16,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 331.0485836031683,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 16,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "sufficient",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.9599999999999757,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "sufficient",
      "pattern": "slow_common_bias",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.9599999999999757,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "sufficient",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 0.9599999999999757,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 66.82159298880318,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 66.82159298880318,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 3,
      "complete_sequences": 3,
      "conditional_mean_total_travel_m": 66.82159298880318,
      "conditional_mean_event_worst_uncovered": 2.220446049250313e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 150
    }
  ],
  "cross_method_original_service_comparison": {
    "joint_complete_sequences": 27,
    "mean_event_worst_gap_difference": 0.0,
    "maximum_event_worst_gap_difference": 0.0,
    "scope": "empirical comparison with external recompute method; certificate compares own-state greedy, not a different counterfactual history"
  },
  "reference": "ORIGINAL_WORLD_REFERENCE",
  "general_error_model": "arbitrary bounded independent or correlated person error; observation-certified group identity",
  "scope": "50 accepted observation indices; no reset within sequence; not asymptotic stability or dynamic-crowd proof"
}
```

有限序列结果不是任意时间的收敛或受限通信稳定性证明。Step2/3仍需独立动力学与信息年龄条件。
