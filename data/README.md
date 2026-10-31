# 实验资产目录

`catalog.sqlite` 保存初始 1,190,947 个文件和 235,433 个目录的来源身份，另列前轮后续登记的 7 个文件和 2 个目录。
`archives/` 是普通 ZIP64 分卷，主文件成员保留前轮规范目录的相对路径；NTFS 备用数据流单独保存并由索引关联。
分卷只有完成全成员 SHA-256 复核后才可由恢复工具使用。生成中的分卷不代表迁移完成，实际状态以本机审计回执为准。

这里保留原始实验、失败记录、冻结源码、历史环境和未知价值内容。归档环境不用于运行。
实验身份独立于文件去重；来源机器、原相对路径、时间、哈希和处置记录都保留。
同 seed 不等于同实验，历史恢复不增加样本数。

## 查找与恢复

在项目根目录使用本项目解释器：

```powershell
./.venv/Scripts/python.exe scripts/research_assets.py find step1_final --machine PC2 --limit 20
./.venv/Scripts/python.exe scripts/research_assets.py show <source_id>
./.venv/Scripts/python.exe scripts/research_assets.py restore <source_id> --to data/restored/new-file.npz
./.venv/Scripts/python.exe scripts/research_assets.py restore-tree PC2 Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/runs/<batch> --to data/restored/new-batch
```

`source_id` 和批次路径以实际查询输出为准，尖括号表示需要替换的参数。
恢复只写入全新目标，拒绝覆盖、路径逃逸、链接和重复 ZIP 成员；每个文件核验哈希。
整树恢复在目标内保留所选目录本身，例如所选 `runs/example` 恢复为 `new-batch/example/`。
出现 `.asset-restore-partial` 表示事务未完成，检查错误后选择新目标，不把半成品当结果。

`prior_experiments` 保留前轮 128 个逻辑批次；`prior_study_metadata`、`prior_study_references`
保留实验元信息与引用关系。批次数、工件数和独立物理输入数是不同口径。
缺失的历史版本保持缺失标记，不用当前代码重跑补齐。详细限制见本机审计报告。

Git 克隆不包含这些大型分卷和私有索引。跨机复制时需一起携带 `catalog.sqlite`、`archives/`
和所需科研材料，并重新核验；普通代码 smoke 不需要全部历史资产。
