# Step 1 可复核交付索引

机器可读清单是 [step1_delivery_index.json](step1_delivery_index.json)。`present` 必须是可交付文件。
`runtime_environment` 按Windows/Linux选择本项目的`.venv`解释器；环境本身不进入Git。
`local_gitignored` 是原机器的可选本地工件，是否存在不能由`runs/`根目录推断；`local_presence_at_export_20261029`记录本次实际检查，不代表其他机器可用。
`absent` 必须仍然不存在。`unrecoverable` 是明确无法恢复的内容，不能用重建文件顶替。
原远端索引字节见[28f57b9快照](../reports/step1_review_20261029/upstream_delivery_index_28f57b9.json)。最新扩展结果另见[Step 1审查入口](../reports/step1_review_20261029/README.md)。

不要复制整个 `runs/`，不要重写历史哈希锁，也不要把 `runs/reconstructed_20261023/` 称作原始数据。

## 最小复现

这些命令不读取 5000–5029，也不跑 4860 整表。输出必须是新目录。

```powershell
./scripts/setup_env.ps1
./.venv/Scripts/python.exe -m pip install -e ".[dev,analysis,notebooks]"
./.venv/Scripts/python.exe -m pytest -q --basetemp .tmp/pytest
./.venv/Scripts/python.exe -m pip check
./.venv/Scripts/python.exe experiments/validation_runner.py --phase development --smoke --workers 4 --output runs/step1-closeout-smoke
./.venv/Scripts/python.exe experiments/validation_runner.py --reevaluate --output runs/step1-closeout-smoke
./.venv/Scripts/python.exe run.py --config configs/step1/static_50.yaml --output runs/step1-closeout-static50 --no-plots
./.venv/Scripts/python.exe run.py --reevaluate runs/step1-closeout-static50
```

最小数据只需源码、`configs/step1/static_50.yaml`、`experiments/acceptance_manifest.yaml`、`pyproject.toml` 和本机 `.venv`。50 人示例不需要历史 SQLite。它的偏移和初态不能拿去和 1.3 m 非凸实验比较。

复核资源确认、截止诊断或重建声明时，还需要索引里对应的报告、配置和本机 `runs/` 工件。缺的元数据保持缺席。

## 入口

| 角色 | 位置 |
|---|---|
| 最终源码 | `src/udc/`；根目录 `run.py` 与 `experiments/validation_runner.py` 只转发 |
| 环境 | `.venv`，独立 CPython 3.12.15；锁文件 `requirements/locks/windows-py312.txt`；安装脚本 `scripts/setup_env.ps1` |
| 协议 | `docs/EXPERIMENT_PROTOCOL.zh.md`、`docs/STEP1_THEORY.zh.md`、`configs/step1/`、`experiments/acceptance_manifest.yaml` |
| 当前结论 | `docs/STEP1_EVIDENCE_MAP.zh.md`、`docs/STEP1_CLOSEOUT.zh.md` |
| 输入与结果 | 各 `reports/step1_*/REPORT.zh.md` 及其点名的 `runs/step1_*`；重建在 `runs/reconstructed_20261023/` |
| 图表 | 各报告目录内的图；新 smoke 图写到新的输出目录 |
| 失败台账 | 资源确认、资源响应、恢复诊断和截止诊断报告；合成会计复现 `runs/summary-error-audit-94tjgmgt/` |
| 哈希与缺口 | `reports/project_rebuild_20261023/`、`docs/DATA_AVAILABILITY.zh.md` |

## 不能恢复的内容

旧主机上约 10,641 个 JSON／YAML／TXT 版本仍然缺失。它们是元数据和中间记录，不是本索引里已经匹配的 NPZ、SQLite 或 WKB。没有这些字节，就不能声称原样重放对应的旧 provenance。

历史 1620 任务属于 `f5b5d9c`，本索引不把它的轨迹当作当前代码产物。完整 4860 因子表没有结果目录。5000–5029 没有开发运行。

原远端记录`runs/summary-error-audit-94tjgmgt/audit_result.json`为合成会计审计，它不是科研批次。
该文件及`runs/step1-closeout-accounting-audit/audit.json`在本次机器上缺失，未为发布重建，不作为本轮新验证证据。
