# Step 1 理论—实现对应

## 模型与符号
距离单位为 m、时间为 s。观测是静态中心 $p_j$、半径 $r_j$ 与数值权重 $d_j>0$；权重仅是规划输入，不解释为经验证的行为风险。房间已知，生成多边形对控制器不可见。占据集是观测圆盘的凸包，故对凹点集是保守外包而不是真实边界重建；deployment curve 是该集合的外偏移。代码还验证闭合多边形、人员圆盘包含、墙和人员距离。静态导航障碍从该人体占据包络出发，只再膨胀 guide 半径与 clearance；guide–human 中心安全阈值仍是两者半径与 clearance 之和，两种表达等价但不能重复相加人体半径。

## 周期规划
长度为 $L$ 的曲线以弧长 $s\in[0,L)$ 参数化。离散目标为
$J_h=\sum_q w_q d_L(s_q,z_{a(q)})^2$。average 使用
$\rho=\sum K_jd_j/\sum K_j$，mass 使用 $\rho=\sum K_jd_j$；Average 的核按行平移，比例中的共同尺度抵消；Mass 用 logsumexp 并仅减去全曲线共同尺度，保留各位置的相对 Gaussian 质量。此归一化后的积分不解释为物理人数。固定分区时，周期展开后的加权质心最小化对应二次子问题；重新分区是逐点取最近站点。因此 CVT 更新只在数值接受检查通过时非增。ties 由 `argmin` 的最小索引确定。这不是连续目标、唯一极限或全局最优证明。Equal Arc 是直接构造；DistMesh-inspired 是 $\sqrt\rho$ 累计质量分位的阻尼松弛，明确没有 Lloyd 下降声明。

人员需求进入曲线的唯一映射是 Gaussian 密度。Mass、`projected_mass` 与 RA-ABDC 使用同一份 $\rho(s)=\sum_j d_j K(x(s),p_j)$；Average 使用核加权均值。人员权重全部为 1 时，$\rho$ 仍随人群相对曲线的位置变化，不是平的。评价始终用这份原始 $\rho$，不用规划时的 $\rho^a$。空间热点只改变部分 $d_j$，不另建第二条边界。

`projected_mass` 与 RA-ABDC 最小化同一个 $J_a=\sum_q w_q\rho_q^a d_L(s_q,S)^2$。前者固定 $a=1$ 并做间隙投影，没有浓度日程，也没有回退。后者从 $a=1$ 开始；只有投影后的欧氏间距仍违反时才把 $a$ 减半，直到记录在案的 $a=0$ Equal Arc。投影后的第一步就是 `projected_mass`。投影迭代没有下降或最优性声明。

## 数量必要条件

设部署曲线长度为 $L$，规划弧间隙上限为 $H_p=H-2\epsilon_s>0$，最小欧氏间距为 $d_{\min}$。若每个目标都落在这条闭合曲线上，则相邻弧间隙不超过 $H_p$ 要求 $N\ge\lceil L/H_p\rceil$。弦长不超过对应弧长，所以全体成对欧氏距离都 $\ge d_{\min}$ 要求 $N\le\lfloor L/d_{\min}\rfloor$。违反其中一条，就证明在这个曲线模型里不存在可接受目标；代码记为 `certified_infeasible`。两条都成立只说明数量上还没有被排除。它不包含障碍、可达指派、安全过滤和终端保持。搜索把预算用完仍叫 `no_plan_found`，不是新的不可行证明。实际间隙始终另测。桥接式 $H_{actual}\le H_{targets}+2e_s$ 只在目标身份的相容周期 lift、投影有效且环向顺序保持时适用；欧氏误差不替代弧长误差。

## 弧间隙投影

`project_gaps` 把间隙向量投影到
$\{g\in\mathbb{R}^N: d_{\min}\le g_i\le H_p,\ \sum_i g_i=L\}$。
该集合非空当且仅当 $N d_{\min}\le L\le N H_p$。实现用二分搜索 $\lambda$，取 $\mathrm{clip}(g+\lambda, d_{\min}, H_p)$，再把残差分到严格位于开区间内的分量上。这是带盒约束的单纯形上的欧氏投影的数值形式；二分 200 步和残差分配给出的是容差内的可行点，不是无限精度投影。投影后的站点由累积间隙得到，再平移到投影前站点的平均弧长，以保留整体相位，然后按弧长排序，闭合曲线的起点不单独固定。最小弧间隙不能代替最小欧氏间距：非相邻站点的弦长在投影之后仍然单独检查。

投影 Lloyd 没有原始目标的下降证明，也不保证收敛到全局最优，更不保证每一种几何下都存在满足欧氏间距的点。弧间隙可行、欧氏间距可行、指派可达、执行区间安全是四件不同的事。安全层的证据仍是零阶保持线段上的二次距离极小值复核；本轮实验成功不能把它升级成一般定理。

当前已在凸曲线上验证的候选是 `projected_mass`：原始需求密度上的固定 $a=1$，加上上述间隙投影。RA-ABDC 的浓度减半和 Equal Arc 回退仍在代码里，但这批实验没有触发，不能算成已验证的额外能力。

## 热点超额

人员权重均匀不等于曲线密度均匀，因为 $\rho(s)=\sum_j d_j K(x(s),p_j)$ 还取决于人员到曲线的距离。声明了热点圆盘时，超额权重是冻结需求减去背景：$e_j=d_j-d_j/m$（未截断时）。评价把同一核、同一带宽下的 $\sum e_j K$ 与 $\sum d_j K$ 放在一个共同对数尺度上。超额加权代价是超额质量下的均方弧长距离，单位 m$^2$，越小越好。局部超额质量达到该点需求质量一半的弧段在比较方法之前就固定；没有超额时这些指标记为不适用。这不替换原来的全曲线需求代价。

DistMesh-inspired 的不动点使 $\int\sqrt{\rho}$ 在各间隔上相等，因此局部间距 $h(s)\propto 1/\sqrt{\rho(s)}$，最宽间隙出现在密度最小处。它没有间隙上限；阻尼 $0.35$ 只影响到达不动点的路径，不改变该间隙公式。

## 采样区间安全
零阶保持时相对位置 $r(t)=r_0+tw$。程序把二次函数 $\|r_0+tw\|^2$ 在 $[0,dt]$ 的极小点（含 $w=0$）求出，对 guide–guide 和 guide–human 逐段复核；矩形墙因坐标仿射，只需端点半空间极值。物理中心阈值已包含双方半径和 clearance，numeric tolerance 仅用于浮点比较。线性投影约束是保守求解器，执行前的区间复核才是软件证据。初态安全时零速度维持安全，但不保证任务进展；初态违规不会包装成 fallback。

## 跟踪
无饱和/安全修正时 $e_{k+1}=(1-gdt)e_k$，收缩因子为 $|1-gdt|$；实现选择更保守的 $0<gdt\le1$ 防过冲。若安全修正为 $\delta u_k$，则 $e_{k+1}=(1-gdt)e_k-dt\delta u_k$，只能得到受扰界，不能声称零误差收敛。visibility path 存在、单机器人可达和安全过滤均不证明多机器人全局到达。

## 停滞与有限恢复

目标欧氏距离在合法绕障段上可能增加，因此 supervisor 使用当前 waypoint 之后的剩余折线路径长度作为进展量，并要求窗口进展不足、尚未到达且实际速度很低才报告停滞事件。事件不等于终止：实现按确定顺序执行路径重算、可达二分图重分配、单 guide 优先级让行。每次恢复重置进展窗口；`max_replans` 耗尽后才返回 `REPLAN_EXHAUSTED`。该机制是有界工程恢复，不构成任意多机器人初态的无死锁或全局到达证明。

## 假设映射
| 假设/结论 | 代码检查 | 记录 | 限制 |
|---|---|---|---|
| 静态人群 | `Environment.advance` 冻结 | `crowd_static` | 非动态遏制 |
| 保守几何有效 | `boundary.estimate_boundary` | geometry criterion | 凸包丢失凹陷 |
| 固定 N 离散下降 | `deployment.plan_coverage` | 每次 attempt 的 cost | 仅 CVT 离散目标；RA-ABDC 投影后无下降声明 |
| 需求加权可行候选 | `deployment.plan_risk_aware` | `adjustments`、浓度、Equal Arc 回退 | 不是风险控制，也不是全局最优 |
| 静态路径存在 | `navigation.navigate_and_assign` | path criterion | 非联合 MAPF 证明 |
| 每段采样安全 | `path_clearances` + evaluator | minima/危险步 | 浮点软件验算 |
| 有界停滞恢复 | `remaining_path_lengths` + `recover` | deadlock events/replan count | 非全局完备性证明 |
| 到达和覆盖 | 独立 evaluator、连续 hold | criteria | 有限案例经验结果 |
