# Static Step 1 review package

This release packages completed experiments; it does not start a new study.
The original stopped confirmation, failed development attempts, and corrected
read-only analysis remain separate from the new independent confirmations.

## Review order

1. [Final scientific assessment](../step1_closure_20261028/ASSESSMENT.zh.md)
   and [architecture](../../docs/ARCHITECTURE.zh.md).
2. [Safe routing](../../src/udc/multigroup/safe_routing.py) and
   [routing confirmation](../step1_closure_20261028/routing_confirmation_v2/REPORT.zh.md).
3. [Original-reference bound](../../docs/STEP1_GENERAL_REFERENCE_BOUND.zh.md),
   [support implementation](../../src/udc/multigroup/reference_support.py), and
   [stable update](../../src/udc/multigroup/stable_update.py).
4. [Continuous experiment](../../src/udc/experiments/sequential_deployment.py),
   [independent audit](../../src/udc/analysis/sequential_audit.py), and
   [sequence confirmation](../step1_closure_20261028/sequence_confirmation_v3/REPORT.zh.md).
5. [Preserved original stop](../static_service_audit_20261026/REPORT.zh.md),
   [read-only summation repair](../step1_closure_20261028/readonly_audit_repair_v2/REPORT.zh.md),
   and [completion verification](../step1_closure_20261028/final_readonly_summary_v1/completion_verification.json).

## Results and limits

| Question | Independent evidence | Limit |
|---|---|---|
| Safe deployment | 8,640 rows, 320 physical blocks; reverse assembly 2,880/2,880 vs baseline 2,764/2,880; +4.0278 percentage points, 95% CI [3.5069, 4.5486] | Deployment is not full coverage or global path completeness |
| Original-reference service certificate | 13,286 actual certified frame checks without refutation | Sufficient conditions only; all insufficient-resource cases fall back; float64 audit is not a formal machine proof |
| 50-update stability | 108,240 accounted rows, 80 physical blocks; 2,092/2,160 complete sequences | 68 deployment failures and 2,038 unexecuted tail frames retained |
| Predeclared movement comparison | 47/80 jointly complete pairs; stratified difference -144.8835 m, 95% CI [-215.9668, -84.3428] m | Conditional result; full-denominator identification interval crosses zero |
| Original confirmation | 16,474/17,280 recorded | One safety violation; 806 unexecuted; never resumed or replaced |

Physical blocks, task rows, methods and time frames are distinct sample units.
The certificate compares a policy's retained layout with its own current-state
greedy counterfactual, not another policy's entire history. Step 2/3 stability
and arbitrary-time convergence are not established.

## Portable evidence and local data

[manifest.json](manifest.json) maps each published artifact to its original
location and SHA-256. The `evidence/runs/` subtree contains byte-identical copies
of manifests, seals, protocols, summaries, completion receipts and audit results.
[Frozen sources](evidence/frozen_sources.zip) preserve the three study source
snapshots and the separate corrected analysis snapshot. This is necessary because
the integrated review branch also contains upstream changes made after freezing.
[Unit receipts](evidence/original_readonly_unit_receipts.zip) archive the original
per-unit failure classification and replay receipts without thousands of diff entries.

Original reports are not rewritten. Their `runs/` links and absolute machine
paths refer to local artifacts; resolve published copies through this manifest.
The full observations, per-step trajectories and checkpoint chunks remain under
the original local `runs/` directories. They are **not included in a Git clone**.
The included receipts document completed replay; verifying this package is not
a fresh full trajectory replay. There is no public raw-data download in this PR.
Full replay requires the original dataset with its original paths and frozen
source snapshots. Do not invoke experiment runners to reconstruct missing data.

On a clean clone, after creating the project `.venv`:

```powershell
./.venv/Scripts/python.exe scripts/package_step1_review.py
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m pip check
```

The first command verifies published hashes and every archived member without
requiring local `runs/`. `--build` is an export operation for the original machine
and refuses to overwrite an existing evidence bundle. It never runs science.
Validation measurements are in [the validation log](../../docs/VALIDATION.zh.md).

## Deferred work

Further experiments on the 68 continuous deployment failures and conservative
low-resource certificates are deferred at the user's request. No new independent
research, original confirmation retry, or reserved seed 5000–5029 run is part of
this release. Existing upstream Step 2a files are retained as a separate work line.
