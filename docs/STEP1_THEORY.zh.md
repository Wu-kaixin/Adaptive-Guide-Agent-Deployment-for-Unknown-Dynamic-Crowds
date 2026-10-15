# Step 1 理论—实现对应

## 模型与符号
距离单位为 m、时间为 s。观测是静态中心 $p_j$、半径 $r_j$ 与数值权重 $d_j>0$；权重仅是规划输入，不解释为经验证的行为风险。房间已知，生成多边形对控制器不可见。占据集是观测圆盘的凸包，故对凹点集是保守外包而不是真实边界重建；deployment curve 是该集合的外偏移。代码还验证闭合多边形、人员圆盘包含、墙和人员距离。静态导航障碍从该人体占据包络出发，只再膨胀 guide 半径与 clearance；guide–human 中心安全阈值仍是两者半径与 clearance 之和，两种表达等价但不能重复相加人体半径。

## 周期规划
长度为 $L$ 的曲线以弧长 $s\in[0,L)$ 参数化。离散目标为
$J_h=\sum_q w_q d_L(s_q,z_{a(q)})^2$。average 使用
$\rho=\sum K_jd_j/\sum K_j$，mass 使用 $\rho=\sum K_jd_j$；Average 的核按行平移，比例中的共同尺度抵消；Mass 用 logsumexp 并仅减去全曲线共同尺度，保留各位置的相对 Gaussian 质量。此归一化后的积分不解释为物理人数。固定分区时，周期展开后的加权质心最小化对应二次子问题；重新分区是逐点取最近站点。因此 CVT 更新只在数值接受检查通过时非增。ties 由 `argmin` 的最小索引确定。这不是连续目标、唯一极限或全局最优证明。Equal Arc 是直接构造；DistMesh-inspired 是 $\sqrt\rho$ 累计质量分位的阻尼松弛，明确没有 Lloyd 下降声明。

人员需求进入曲线的唯一映射是 Gaussian 密度。Mass／RA-ABDC 使用 $\rho(s)=\sum_j d_j K(x(s),p_j)$；Average 使用核加权均值。空间热点只改变部分 $d_j$，不另建第二条边界。

RA-ABDC 候选在固定 $N$ 下最小化 $J_a=\sum_q w_q\rho_q^a d_L(s_q,S)^2$，$a\in[0,1]$，约束为弧间隙 $\le H_p$、弧间隙 $\ge d_{\min}$ 以及欧氏间距 $\ge d_{\min}$。算法对每次 Lloyd 步做间隙投影；若仍不可行则把 $a$ 减半，直到 $a=0$ 的 Equal Arc。投影迭代没有下降或最优性声明。Equal Arc 回退必须写进 `adjustments`。

规划要求 $H_p=H-2\epsilon_s>0$，必要的规划规范下界为 $\lceil L/H_p\rceil$，欧氏间距的必要上界为 $\lfloor L/d_{\min}\rfloor$。违反其中之一才是不可行证书。搜索耗尽只叫 `PLAN_SEARCH_EXHAUSTED`／`no_plan_found`；只有下界超过可用数才叫 `CAPACITY_SHORTFALL`。实际 gap 永远独立测量。桥接式 $H_{actual}\le H_{targets}+2e_s$ 只在目标身份的相容周期 lift、投影有效且环向顺序保持时适用；欧氏误差不替代弧长误差。

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
