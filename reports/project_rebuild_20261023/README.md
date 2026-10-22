# 2026-10-23 项目整合审计

范围与限制见[重建日志](../../docs/PROJECT_REBUILD.zh.md)。

| 文件 | 内容 |
|---|---|
| `source_inventory.json`、`current_inventory.json` | 转移文件全清单与合并前当前项目文件清单 |
| `merge_manifest.json`、`git_sources.json` | 文件去向、冲突原件、37个Git恢复文件和提交历史 |
| `existing_backup_inventory.json`、`recovered_data_files.json` | 本机备份校验、883个字节一致的文件恢复 |
| `data_reference_audit.json` | 历史引用的匹配、异版、缺失及原始路径 |
| `environment.json`、`pip_freeze.txt` | `.venv`解释器与包版本 |
| `pytest.log`、`smoke.log`、`smoke_reevaluation.log` | 218测试、132任务及132一致复判 |
| `static50.log`、`static50_reevaluation.log`、`pedpy.log` | 50人示例和分析 |
| `reconstruction_attempt2.log` | 重建进度和失败证据 |

重新执行的数据单独保存在 `runs/reconstructed_20261023/`。完成统计在批次结束后生成，不将重建结果计作历史原件或新独立确认。

## 最终结果

已完成1,699行／次记录和120行离线相位政策表。31,305个新文件完成校验；8,974个NPZ禁用pickle读取通过。两次Windows写入中断从保存工件恢复，科学执行重试0次。历史8,393个NPZ／SQLite／WKB引用全部匹配；10,641个旧metadata版本继续标记缺失，新完整元数据独立保存。

- [八批统计与状态](reconstruction/REPORT.zh.md)
- [最终重建核验](reconstruction_verification.json)
- [逐文件哈希](reconstruction_files.json)
- [逐任务工件核验](reconstruction_task_checks.json)
- [11,599个重建字节匹配](reconstructed_byte_matches.json)
- [最终历史引用摘要](historical_references_after_reconstruction_summary.json)
- [合并前文件保留核验](preservation_verification.json)

全部转移项已保留在项目内完整ZIP中，转移目录已删除；没有提交、推送或main合并。
