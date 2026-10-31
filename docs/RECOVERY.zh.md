# 历史资产恢复

从项目根目录运行 `scripts/research_assets.py`，用来源 ID 或准确的“机器 + 原相对目录”选择版本。
具体命令见[数据目录](../data/README.md)。工具不会运行归档源码，不会自动启用导入环境，不会改写原结果。

恢复到新目录后检查 `RESTORE_RECEIPT.json`、文件 SHA-256、原协议、配置与源码版本。
文件主数据、NTFS 备用流和 mtime 在 Windows 上恢复并核验；目录记录用于保留空目录。
创建时间、原属性和硬链接身份保存在目录中，默认恢复不重建 ACL、创建时间或硬链接拓扑。
不要将这些元数据限制误写成完整系统镜像恢复。

`research/archives/git/` 保存固定研究版本的普通归档；完整原 Git 元数据也作为来源资产保留。
恢复 Git 时先在独立目录核验 refs、对象和 `git fsck --full`，不要覆盖主仓库 `.git`。
用户明确取消本轮独立备份要求；当前保存仍在同一块磁盘，不具备独立灾备能力。

任务专用恢复演练、源删除清单与准确完成状态位于本机
`research/project-management/repository_rebuild_20261031/`。
