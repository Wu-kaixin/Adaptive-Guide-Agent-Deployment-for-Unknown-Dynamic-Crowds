# 原有仿真与分析示例

本目录保留最初的 JuPedSim／PedPy 和 Guide 部署脚本及其数据。
这些脚本使用硬编码参数，并依赖 `results/` 中前一步的输出，适合学习与历史复查。

新的统一实验从仓库根目录运行：

```bash
python run.py --config configs/step1/static_50.yaml --output runs/static50
python run.py --reevaluate runs/static50
udc-analyze --result runs/static50 --output runs/static50_analysis
```

统一框架也可导入本目录 `results/static_50.sqlite` 的首帧并冻结。
在复制的 YAML 中设置 `crowd.source: sqlite`，移除 `crowd.grid`，将
`crowd.trajectory_file` 设置为相对于该 YAML 的 SQLite 路径。
人员半径与 demand 由 YAML 给定；数据库仅提供首帧位置，不向控制器提供后续轨迹。
