# 50次静态重观测：数量与相位切换

记录：16308/16308；全部保存轨迹和决策重建一致。

每个方法沿自己的实际状态连续推进。普通部署失败结束受影响序列，其余帧明确未执行，不重置、不补抽。

服务门控使用原参考支持域，任意独立/相关有界误差下仅在充分条件通过时认证；不能认证回退贪心。

主指标/完整分母界及按资源/扰动模式统计见下方；短路程必须与50步完整交付共同解释。

```json
{
  "manifest_sha256": "30e56ec340d2b73bbd95b64193933817ceba4a123f31314f7bc2d609dbfc4ea6",
  "summary": {
    "stopped": false,
    "completed_units": 36,
    "scheduled_units": 36,
    "recorded_step_records": 16308,
    "scheduled_step_records": 16308,
    "not_recorded_steps": 0,
    "statuses": {
      "PARTIAL_SERVICE": 10577,
      "FULL_SUCCESS": 5388,
      "SERIAL_ASSEMBLY_UNRESOLVED": 8,
      "NOT_EXECUTED_PRIOR_DEPLOYMENT_FAILURE": 335
    },
    "orchestration_error_units": 0,
    "sequence_completed": 316,
    "episodes_recorded": 324,
    "independent_physical_blocks": 12
  },
  "audit": {
    "manifest_sha256": "30e56ec340d2b73bbd95b64193933817ceba4a123f31314f7bc2d609dbfc4ea6",
    "checked_step_records": 16308,
    "scheduled_step_records": 16308,
    "mismatches": [],
    "all_match": true,
    "certificate_frames": 2164,
    "independent_physical_blocks": 12,
    "evidence": "development_new_inputs",
    "by_method": {
      "recompute_all": {
        "episodes": 108,
        "completed_episodes": 104,
        "accepted_frames": 0,
        "fallback_frames": 0,
        "count_switch_events": 395,
        "nontrivial_hold_frames": 0
      },
      "phase_guard": {
        "episodes": 108,
        "completed_episodes": 106,
        "accepted_frames": 1059,
        "fallback_frames": 4252,
        "count_switch_events": 395,
        "nontrivial_hold_frames": 0
      },
      "counts_phase_guard": {
        "episodes": 108,
        "completed_episodes": 106,
        "accepted_frames": 1105,
        "fallback_frames": 4206,
        "count_switch_events": 304,
        "nontrivial_hold_frames": 985
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
      "conditional_mean_total_travel_m": 223.26044407506484,
      "conditional_mean_event_worst_uncovered": 0.4902481520508986,
      "count_switch_events_all_recorded": 87,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "insufficient",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 223.26044407506484,
      "conditional_mean_event_worst_uncovered": 0.4902481520508986,
      "count_switch_events_all_recorded": 87,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 600
    },
    {
      "resource": "insufficient",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 223.26044407506484,
      "conditional_mean_event_worst_uncovered": 0.4902481520508986,
      "count_switch_events_all_recorded": 87,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 600
    },
    {
      "resource": "insufficient",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.43809877802171937,
      "conditional_mean_event_worst_uncovered": 0.49002970109199717,
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
      "conditional_mean_total_travel_m": 0.43809877802171937,
      "conditional_mean_event_worst_uncovered": 0.49002970109199717,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 600
    },
    {
      "resource": "insufficient",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.43809877802171937,
      "conditional_mean_event_worst_uncovered": 0.49002970109199717,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 600
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 105.669191969981,
      "conditional_mean_event_worst_uncovered": 0.48984197560830106,
      "count_switch_events_all_recorded": 50,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 105.669191969981,
      "conditional_mean_event_worst_uncovered": 0.48984197560830106,
      "count_switch_events_all_recorded": 50,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 600
    },
    {
      "resource": "insufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 105.669191969981,
      "conditional_mean_event_worst_uncovered": 0.48984197560830106,
      "count_switch_events_all_recorded": 50,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 600
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 105.54157648941278,
      "conditional_mean_event_worst_uncovered": 0.024121004184568175,
      "count_switch_events_all_recorded": 8,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 102.97208554042037,
      "conditional_mean_event_worst_uncovered": 0.024121004184568175,
      "count_switch_events_all_recorded": 8,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 550
    },
    {
      "resource": "moderate",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 101.40659571604691,
      "conditional_mean_event_worst_uncovered": 0.024121004184568175,
      "count_switch_events_all_recorded": 8,
      "nontrivial_hold_frames": 50,
      "fallback_frames": 550
    },
    {
      "resource": "moderate",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 1.4273514109716334,
      "conditional_mean_event_worst_uncovered": 0.025386106477871356,
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
      "conditional_mean_total_travel_m": 1.4082502226127043,
      "conditional_mean_event_worst_uncovered": 0.025386106477871356,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 550
    },
    {
      "resource": "moderate",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 1.3723514109716313,
      "conditional_mean_event_worst_uncovered": 0.025386106477871356,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 50,
      "fallback_frames": 550
    },
    {
      "resource": "moderate",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 96.94414851821118,
      "conditional_mean_event_worst_uncovered": 0.024742054768769856,
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
      "conditional_mean_total_travel_m": 96.47564867872006,
      "conditional_mean_event_worst_uncovered": 0.024742054768769856,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 550
    },
    {
      "resource": "moderate",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 94.09502158387566,
      "conditional_mean_event_worst_uncovered": 0.024722542068871818,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 50,
      "fallback_frames": 540
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 9,
      "conditional_mean_total_travel_m": 743.9253567763129,
      "conditional_mean_event_worst_uncovered": 4.440892098500626e-16,
      "count_switch_events_all_recorded": 160,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 11,
      "conditional_mean_total_travel_m": 582.4362002025659,
      "conditional_mean_event_worst_uncovered": 4.239033366750598e-16,
      "count_switch_events_all_recorded": 160,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 311
    },
    {
      "resource": "sufficient",
      "pattern": "independent_jitter",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 11,
      "conditional_mean_total_travel_m": 447.4243373377424,
      "conditional_mean_event_worst_uncovered": 4.239033366750598e-16,
      "count_switch_events_all_recorded": 118,
      "nontrivial_hold_frames": 145,
      "fallback_frames": 309
    },
    {
      "resource": "sufficient",
      "pattern": "slow_common_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 1.654694517371438,
      "conditional_mean_event_worst_uncovered": 4.3483735131151963e-16,
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
      "conditional_mean_total_travel_m": 1.3904895673483466,
      "conditional_mean_event_worst_uncovered": 4.3483735131151963e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 250
    },
    {
      "resource": "sufficient",
      "pattern": "slow_common_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 12,
      "conditional_mean_total_travel_m": 0.8810227452546205,
      "conditional_mean_event_worst_uncovered": 4.3483735131151963e-16,
      "count_switch_events_all_recorded": 0,
      "nontrivial_hold_frames": 350,
      "fallback_frames": 250
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "recompute_all",
      "scheduled_sequences": 12,
      "complete_sequences": 11,
      "conditional_mean_total_travel_m": 317.32660773312557,
      "conditional_mean_event_worst_uncovered": 4.239033366750598e-16,
      "count_switch_events_all_recorded": 90,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 0
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 11,
      "conditional_mean_total_travel_m": 300.9947134589336,
      "conditional_mean_event_worst_uncovered": 4.239033366750598e-16,
      "count_switch_events_all_recorded": 90,
      "nontrivial_hold_frames": 0,
      "fallback_frames": 241
    },
    {
      "resource": "sufficient",
      "pattern": "oscillatory_shape_bias",
      "method": "counts_phase_guard",
      "scheduled_sequences": 12,
      "complete_sequences": 11,
      "conditional_mean_total_travel_m": 137.9864321752265,
      "conditional_mean_event_worst_uncovered": 4.239033366750598e-16,
      "count_switch_events_all_recorded": 41,
      "nontrivial_hold_frames": 340,
      "fallback_frames": 207
    }
  ],
  "cross_method_original_service_comparison": {
    "joint_complete_sequences": 104,
    "mean_event_worst_gap_difference": -2.251465372850357e-06,
    "maximum_event_worst_gap_difference": 0.0,
    "scope": "empirical comparison with external recompute method; certificate compares own-state greedy, not a different counterfactual history"
  },
  "reference": "ORIGINAL_WORLD_REFERENCE",
  "general_error_model": "arbitrary bounded independent or correlated person error; observation-certified group identity",
  "scope": "50 accepted observation indices; no reset within sequence; not asymptotic stability or dynamic-crowd proof"
}
```

有限50步结果不是任意时间的收敛或受限通信稳定性证明。Step2/3仍需独立动力学与信息年龄条件。