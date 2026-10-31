# 外置科研资产工作区

活动算法仍为已知封闭场景、单静态群的 Step 1。静态多群/Step 2a 历史协议保留在外置归档，不改变本仓库默认算法或 CLI 行为。

本仓库可以单独安装、测试和运行开发示例。使用项目 `.venv` 与现有依赖锁；不激活其他机器复制来的环境。工作区通常以 `01-Code-Repository`、`02-Experimental-Data`、`03-Research-Materials` 三个兄弟目录组织。

新输出使用显式 `--output` 指向数据区下不存在的新运行目录。例如在代码根用 `.venv/Scripts/python.exe experiments/validation_runner.py --phase development --smoke --workers 4 --output ../02-Experimental-Data/runs/development/<unique-id>`；独立复判使用同解释器和 `--reevaluate --output <同目录>`。不要覆盖旧运行，不使用开发禁止的验证种子。

历史定位入口是 [workspace_assets.py](../scripts/workspace_assets.py)。用 `--catalog <本地inventory.sqlite> --source PC2 --path <完整来源相对路径>` 定位，可加 `--sha256 <冻结期望哈希>`。定位器核验字节和命名流，不需要原 PC2/PC3 路径仍存在；私有catalog和个人路径不提交Git。

恢复用 `--restore-prefix <来源目录> --destination <全新目录>`。它复核每个普通文件的SHA-256、命名流和原mtime，拒绝覆盖；空目录、ACL、创建时间和链接不在该恢复器的承诺范围。

旧报告、哈希锁和Notebook保留原字节。历史Notebook中可能有数据缺失时重分析并写入的单元，先做只读定位，不整体执行。图表使用 `visualization.py --result <明确结果目录> --output <新图文件>`。

批量整合的实时进度、来源缺口和独立备份状态以资料区 project-management/consolidation 下的 mission_state.json、CONTINUE.md 和审计报告为准。历史目录、文件数量或报告存在本身不证明科学结论或迁移完成。
