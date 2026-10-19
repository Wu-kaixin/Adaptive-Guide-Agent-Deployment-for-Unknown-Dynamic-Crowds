# 2026-10-19 Step 1 笔记本恢复核验与可达性诊断边界

当前笔记本尚未取得 `e2a0430` 或更晚可靠源码，也未取得 seed 20–24 的45任务及33个有效任务原始数据。本轮已完成环境、版本、数据目录核验，旧九案例保存轨迹独立复判和重复 backups 清理。11次新失败尚不能逐例分层，未宣称修复。

## 源码和环境

- 分支 `Major-Research-Branches`；开始时工作区干净，HEAD 和远程只读查询均为 `b5eb3418a2363e0ec0864fcde4455b3fd8716d8b`。没有切换或修改 main，没有 reset、清理Git对象、推送或合并。
- 本地 `git cat-file` 不含 `e2a0430`；GitHub commit API 返回422：`No commit found for SHA: e2a0430`。本地未引用提交是旧 main stash；原样保留。origin同步不代表取得主机未推送工作。
- 本地最新可靠导航工作是 `814489d3` 的旧宽U重算修复，后有CI和闭合接缝精度修复。它们不是新增45任务的诊断结果。
- CPU：Ultra 7 258V，8核/8逻辑处理器；内存33054524 KiB（约31.5 GiB），检测时空闲14229700 KiB（约13.6 GiB）。沿用现有 uv 0.13.0 创建的 `.venv`，Python 3.12.15，没有升级或重装。
- `udc` 导入路径：`C:/Users/kai/Adaptive-Guide-Agent-Deployment-for-Unknown-Dynamic-Crowds/src/udc/__init__.py`。`direct_url.json` 确认 editable=true，指向本仓库。
- NumPy 2.5.4、SciPy 1.18.1、Shapely 2.2.0、JuPedSim 1.4.2、PedPy 1.5.1、pytest 9.1.1。科学源码哈希：`f3525f73905fa4574fe5318b433b1564a63fd776bbde18343a29b7dfe1dd02cd`。
- 首次沙箱内启动uv Python和读取硬件失败，获得工具授权后使用原环境完成检查。未复制其他机器的虚拟环境。

## 数据可用性及最小迁移清单

搜索了项目 runs/reports/backups/configs/experiments/.tmp、本地Git refs/reflogs/未引用对象、下载、桌面、文档（含百度同步盘）、OneDrive、百度下载和Public目录。对本地3021份 `metrics.json` 只读扫描，无解析错误，无seed 20–24任务，无 `e2a0430` 来源任务。名称 `high_demand_20_24` 指20/24台Guide，与本轮seed无关。

本地有原九案例配置、人员、初态、目标、指派、轨迹及结果，也有旧宽U失败轨迹和修复轨迹。没有找到本轮45行manifest、汇总和33任务原始输入。没有使用摘要重建人员或目标。

依次需要：

1. `e2a0430` 或更新已验证版本的Git提交链和必要dirty patch，含 `src/udc/`、配置、实验入口、测试、依赖及版本报告。
2. seed 20–24的45行manifest、汇总；保留4个输入失败单元对应12行的原始配置和错误记录。
3. 33任务的config、冻结人员/半径/demand、Guide初态、曲线/弧坐标、全精度目标、指派、轨迹、planning/metrics/state及哈希。尤其先取得宽U seed21的三方法数据。共享输入可以只保存一份，但须有精确映射。
4. 如果主机后续九案例不同于本地版，补对应配置、结果与哈希；旧九案例本地已具备。无需迁移全部历史轨迹、正式1620任务轨迹或其他电脑的虚拟环境。

用户允许找不到时重新运行。但本地连新增非凸输入生成配置/版本也缺失；相同seed不能保证相同输入。因此未用旧生成器、重抽数据或新目标替代固定输入研究。取得最新源码及原始快照后才能进行本轮所需的精确回归。

## 11次失败的分类状态

下表保留已知数量，不编造缺失的任务身份。主机提供的45=12输入失败+33有效任务、33=22成功+11路径失败均列为待独立核验。

| 原记录范围 | 数量 | 已知状态 | 初始端点/搜索/匹配/执行重算/恢复耗尽 | 根因与处理 |
|---|---:|---|---|---|
| wide_u，seed21，equal_arc | 1 | PATH_UNREACHABLE（主机摘要） | 数据缺失，阶段未定 | 未解决 |
| wide_u，seed21，projected_mass | 1 | PATH_UNREACHABLE（主机摘要） | 数据缺失，阶段未定 | 未解决 |
| 其余9次，身份待manifest | 9 | PATH_UNREACHABLE（汇总） | 数据缺失，阶段未定 | 未解决 |

已解释0/11，已解决0/11，待诊断11/11。没有证据将这些失败全归为实现错误、指派、目标不可达或执行重算。12行输入失败没有重抽或从分母删除；其原始文件尚未迁入本机，不能声称已独立复核。

Guide ID、失败时刻、实际位置/目标/航点、直接规划器端点、自由空间哈希和构建参数、连通分量、候选路径/旧路径、重算触发、异常和约束残差，均须从原任务读取。本轮未补造这些字段。

## 宽U seed21的固定输入对照

| 保存目标 | 主机报告 | 本机输入/目标/轨迹 | 可达匹配与执行证据 |
|---|---|---|---|
| Equal Arc | PATH_UNREACHABLE | 缺失 | 尚未确定 |
| projected_mass | PATH_UNREACHABLE | 缺失 | 尚未确定 |
| 成对欧氏约束 | SUCCESS | 缺失 | 尚未独立核验 |

是否因目标位置不同而成功、原目标是否有另一完整匹配、是否有坐标/缓冲/缓存/代价问题、是否只在重算时失败，四问均未确定。未把旧seed15宽U图冒充seed21对照；未绘制猜测的自由空间或路径。固定目标重新指派0次，改变目标重新规划0次，反事实执行0次。

## 现有实现核查及改动边界

`src/udc/navigation/paths.py` 的 `navigation_domain` 是共享保守域构造；`navigate_and_assign` 查找候选路径，以不可达项1e12参与匹配后检查所选边均为有限成本；`route_assigned` 固定原指派重算，其他Guide仅在此阶段进入障碍。`controller/abcg.py:recover` 的第一次策略是path_recompute，异常可能在重算次数和事件记录前终止。空事件表不能证明没有重算。

当前候选搜索在每对查询中重建可见图，没有共享静态图/路径缓存，也未先用连通分量过滤。这里只记录现状；本轮没有启动全配对搜索或把其他Guide加入初始全配对图。代码检查不等于11次失败的根因证据。路径搜索错误中的“does not exist”文字不作为严格不可行证明。

没有修改导航、指派、控制、安全、生成器或阈值，没有新增平行控制器或实验框架。报告辅助脚本只调用既有独立验收并记录哈希。不为旧版本实现盲目补丁；89项主机测试与本地87项的差异也不能靠新增凑数测试弥补。

## 原九案例的当前独立复判

读取 `runs/step1_wide_u_replan_repair/<scene>/<method>/run/`，用当前代码重新调用独立 `evaluate`。全精度NPZ、原目标、原指派均保留；五个历史核心文件逐案执行前后哈希一致。9/9成功、0失败、0判据退化。未重新执行仿真，因此这不是新的九任务执行回归。

下表仿真时间来自保存轨迹，路程按所有Guide轨迹重新求和；净距为中心距减原安全门槛，单位m。

| 场景 | 方法 | 本次复判 | 保存仿真秒 | 复算执行路程m | 保存重算次数 | 最小净距 人/Guide/墙 m |
|---|---|---|---:|---:|---:|---|
| elongated | equal_arc | 通过 | 18.8 | 144.066087 | 0 | 0.748993772 / 0.89039185 / 0.45 |
| elongated | projected_mass | 通过 | 18.8 | 144.057265 | 0 | 0.74899862 / 0.889594844 / 0.45 |
| elongated | pairwise_euclidean | 通过 | 18.8 | 144.057265 | 0 | 0.74899862 / 0.889594844 / 0.45 |
| tight_u | equal_arc | 通过 | 18.1 | 164.625594 | 0 | 0.748903391 / 0.624164647 / 0.45 |
| tight_u | projected_mass | 通过 | 18.1 | 164.626664 | 0 | 0.748903903 / 0.623233768 / 0.45 |
| tight_u | pairwise_euclidean | 通过 | 18.1 | 164.626664 | 0 | 0.748903903 / 0.623233768 / 0.45 |
| wide_u | equal_arc | 通过 | 36.2 | 253.242498 | 1 | 0.0301972776 / 1.02306333e-08 / 0.45 |
| wide_u | projected_mass | 通过 | 36.2 | 253.239571 | 1 | 0.0300827985 / 1.0238239e-08 / 0.45 |
| wide_u | pairwise_euclidean | 通过 | 36.2 | 253.239571 | 1 | 0.0300827985 / 1.0238239e-08 / 0.45 |

旧九案例初始Guide和目标均在同一个保守自由分量，整段轨迹未发现超过1e-7 m容差的首次越界。宽U最小Guide裕量约1.02e-8 m，仍按原安全容差独立检查，没有降低标准。最大终端跟踪误差不超过0.0038 m。

单案例独立评价墙钟约0.052–0.216 s，含初始已指派路径验证、几何、轨迹安全与覆盖评价；它不是单独的导航或重算耗时。历史metrics没有逐次重算计时，重算成本记为缺失。没有为了填补这一列重复仿真或沿用旧计时冒充当前测量。

## 测试、执行数量与存储

- 完整 `pytest -q`：87 passed，19.94 s。
- 导航域/端点/安全恢复专项：6 passed，1.01 s；该6项已包含在87项中。
- `uv pip check --python .venv/Scripts/python.exe`：71包兼容；`python -m pip check`：No broken requirements found。
- 本轮新执行任务0、独立复判9、增加seed 0、正式1620重跑0、昂贵成对优化0。没有通用导航修复，且33固定输入缺失，未为凑数运行42次。11次新失败无已验证修复，退化情况尚不可判断。
- 4个项目backups目录共17979文件、364162327字节（约347.3 MiB），全部与对应正式runs文件SHA-256一致，无唯一数据或唯一dirty源码。删除前再逐文件核对、验证绝对路径在本仓库内，随后删除该项目backups。没有新建替代备份目录，也没有复制整个runs。
- 完整路径映射：`runs/step1_reachability_diagnostics/storage_mapping.json`；SHA-256：`301b074563998907c42cec46a8da19e0c194c4283cdb116b734bfc5610f7e590`。历史报告若引用已删除backups，用映射中的destination。正式runs和历史指标未改写。

## 当前PowerShell命令

在仓库根目录运行。普通用户终端无需本工具沙箱的额外授权。

```powershell
git status --short --branch
git log -1 --oneline
.\.venv\Scripts\python.exe -c "import udc; print(udc.__file__)"
uv pip check --python .venv/Scripts/python.exe
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q tests/test_replan_domain.py
.\.venv\Scripts\python.exe -m pytest -q
# 仅更新本轮独立审计输出，不改写原轨迹，不运行仿真
.\.venv\Scripts\python.exe reports/step1_reachability_audit.py inventory
.\.venv\Scripts\python.exe reports/step1_reachability_audit.py reevaluate-nine
Get-Content reports/step1_reachability_diagnostics.json -Raw -Encoding UTF8 | ConvertFrom-Json
```

源码哈希、输入数组哈希、文件哈希、九类判据及清理记录保存在本轮JSON。重新运行审计会更新runs中的计时和当前Git环境；本报告保留2026-10-19实测快照。

## 下一轮唯一优先项

先取得最新可靠源码和原45任务最小数据包，先对宽U seed21三套原目标离线核查端点、连通分量和完整匹配。只有找到并独立验证路径才能记为可达；分量不同只证明当前保守域不连通；单纯搜索失败保持未确定。在这个证据出现前，无法决定继续修导航还是研究可达性约束部署。暂不扩seed、不换估计器、不开发完整RA-ABDC、不进入Step 2/3。
