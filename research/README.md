# 科研资料与实现版本

从[研究索引](RESEARCH_INDEX.md)查研究问题与证据。`materials/` 中的文稿、图表、演示和诊断资料
保留来源版本；`indexes/readable_materials.json` 给出来源 ID、显示路径和 SHA-256。
`indexes/HIGH_VALUE_ASSETS.csv` 是本轮从待审集合进一步识别出的 152 项资料与配置的用途索引。

## 实现选择

| 位置 | 科学范围 | 使用方式 |
|---|---|---|
| `../src/udc/` | 单一静态人群、已知场地、全局观测、无限通信 | 默认开发、单群回归和 CLI |
| `implementations/major-research-2806bd118290/` | 固定提交的静态多群、有界观测误差、固定资源分配及其证据 | 查看源码、协议、报告；隔离指定 import 路径后才做范围明确的兼容检查 |
| `implementations/pc3-working-source/` | PC3 初始工作树中的 187 个源码、配置、文档与测试文件 | `SOURCE_MAP.json` 记录原字节；本轮只验证既有近目标静态双群夹具 |
| `../data/catalog.sqlite` 的 PC2/PC3 来源 | 其他历史版本、工作树、未提交与运行期源码 | 按来源 ID 恢复到新目录，先核对协议，不覆盖活动源码 |

研究视图来自实际读取的 GitHub `Major-Research-Branches` 提交 `2806bd1182901e590e9525e6dd4ad71e8c3464b2`。
默认主线对应远程 `main` 的 `06b0d7945726cd56d46624decbb6b04ab1b94316`，本地另吸收前轮验证的工程修复。
固定视图不是第二套虚拟环境，也不会自动进入默认 `udc` 导入路径。
其中 `AGENTS.imported.md` 是保存的源资料；原名称与字节仍保存在 Git 和归档中。

研究分支 README 明确说明：当前服务门控全部回退、当前策略间移动收益为 0；这些限制必须与结果一起阅读。
静态多群不等于动态 Step 2/3，需求权重不等于经验证风险模型。
见[固定版本发布入口](implementations/major-research-2806bd118290/reports/step1_release_20261030/README.zh.md)。

`project-management/repository_rebuild_20261031/` 记录本轮实际命令、恢复、分类、删除门禁与未完成事项。
前轮整合保持为独立历史快照。本轮不改写历史结论、阈值或实验锁。
