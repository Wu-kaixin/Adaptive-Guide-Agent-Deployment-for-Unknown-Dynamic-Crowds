# 默认多边形联合与重采样的条件坐标传递

本轮补充初始圆盘到联合坐标的声明条件，以及给定偏置折线到重采样坐标的传递。
核心在`multigroup/union_coordinate_enclosure.py`；不是一般误差下的完整原参考或服务证书，未接入运行门控。
v1及v2都保留，旧68失败、停止确认和既有反例／hash不改。

## 实际入口与坐标来源

GEOS 3.14.1的[GEOSUnaryUnion_r](https://raw.githubusercontent.com/libgeos/geos/3.14.1/capi/geos_ts_c.cpp)
进入`Geometry::Union()`及默认`UnaryUnionOp`，使用`ClassicUnionStrategy`。
这与显式`OverlayNGRobust::Union(single_geometry)`内部选择SRUnionStrategy的入口不能混淆。
初始多边形由[CascadedPolygonUnion](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/union/CascadedPolygonUnion.cpp)
对抽取的多边形分量做平衡二分；P个分量的最大二元联合深度为`ceil(log2(P))`。
分量数指Polygon分量，不能把两个MultiPolygon容器当作两个分量。

二元联合先尝试浮点OverlayNG；失败后有吸附、分别自吸附再联合、固定精度结点，
最后默认Classic策略还可能退到对原两输入集合的`buffer(0)`。
失败尝试的结果被丢弃，不把所有尝试的坐标变换串接到成功输出。
联合操作没有intersection／difference使用的裁剪包络路径。

浮点交点由既有包围盒／端点后置条件控制；吸附索引返回现有或新插入的坐标，不计算坐标平均值。
近顶点结点直接采用输入端点。切分、环装配和Polygon提取按审查的XY复制路径处理。
声明前提包括默认FLOATING输入工厂、纯多边形、正常有限运算、已有结点契约和通常XY复制语义。
这些源码推导不是原生二进制的形式验证，也不证明所有允许世界都有有效输出。

## 从v1到v2的收紧

令`u=2^-53`、`η=2^-1075`、`C=2(1+u)^2`，沿用未知正量化器的条件幅值界。
v1保守地给每次二元联合两个未知量化阶段，整个级联为`2*ceil(log2(P))`阶段。

进一步追踪[零距离环分支](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/OffsetCurveBuilder.cpp)发现，
`getRingCurve(distance=0)`直接复制环，绕过`OffsetSegmentString.addPt`的普通偏置顶点量化。
`BufferCurveSetBuilder.addCurve`随后把这些复制坐标交给结点器。
因此浮点零buffer保持轴框；固定回退是零平移ScaledNoder及单位网格SnapRoundingNoder。

ScaledNoder先把轴区间端点单调映射到可表示整数`J(RN(x*S))`。
这些整数极值在单位网格点量化下严格不变。浮点交点在其轴框内，单位量化单调，
所以交点、顶点及热像素结点都仍在这两个整数极值之间；最后单调除以S恢复坐标。
整个零buffer路径可由**一次缩放量化的轴范围**控制，不能当作两种任意独立量化的连续放大。
固定OverlayNG也只需要已有单次SnapRoundingNoder像集契约。
v2故对每次二元联合使用一个未知正量化阶段，级联为`ceil(log2(P))`阶段。
常规非零Point.buffer仍保留其原两阶段条件界，不据零buffer特例改写。

## 重采样界

给定输入轴区间`[L,H]`，设`W=H−L`，`M=max(|L|,|H|)`。
对区间内任意binary64端点、有限`0≤t≤1`，以及分离或融合乘加的`RN((b−a)*t+a)`，
可用以下有理误差外扩输入区间：

    E = u*M + (3u+3u²+u³)*W + (3+3u+u²)*η.

推导分别计入端点差、乘法、加法舍入；`M≤2^500`保证本表达式的有限算术范围。
GEOS线性位置选择的有限比例有截断端点分支；该界不要求精确边长或准确选择哪一条边。
它只包围最终坐标及其连接直弦，不证明与未知人员世界之间的拓扑或需求质量对应。
较早的严格凸包反例仍成立，外扩坐标框并未恢复该已反驳命题。

## 输入、开发与独立复判

两版本都用同一冻结旧v2阶段库的720输入（80旧真值输入、640旧观测输入）／2520群。
它们来自已有80个物理场景，不能当作720新独立样本。
每个名义输入按数值观测接口取人员坐标及声明误差轴框，读取可观测半径并执行已绑定版本的实际Python圆盘半径表达式。
先组合所有人员的原始圆盘源框和既有两阶段Point.buffer条件框，再按全部人员数作为分量上界候选。
这里**每个人的初始Point.buffer至多一个Polygon是声明条件，尚未证明覆盖全部允许世界**；返回明确标记`component_limit_proved=False`。
旧真值输入仅作为离线数值开发输入，不表示控制器在线可读取真值。
框的构造没有读取保存的联合／偏置顶点；后者只用于检查。
重采样部分则有意使用各保存offset阶段的精确框，属于独立局部传递检查，不能混称完整观测到原参考传递。

| 项目 | v1 | v2 |
|---|---:|---:|
| 输入／群完整分母 | 720／2520 | 同一720／2520 |
| 保存联合坐标检查 | 4014943 | 同一4014943 |
| 保存参考坐标检查 | 481186 | 同一481186 |
| 条件界违反／未执行输入／未检查群 | 0／0／0 | 0／0／0 |
| 联合框宽度和／原始圆盘源框宽度和 | 30022.62–331656.71 | 469.10–1295.53 |
| 原生几何／独立输入／运动新增 | 0／0／0 | 0／0／0 |

另一冻结源码复判分别核验79560个人员源公式、全部旧输入／receipt／npz绑定、级联深度与量化闭式、
全部重采样公式及坐标成员关系，两版本均一致。复判没有调用候选联合或插值框函数。
720输入的源人员数据／原始框／初始Point框逐项相同，v2全部为v1严格子框，宽度和缩小约64–256倍。
这是证明中不必要组合松弛的减少，不是实际原生联合误差、服务提升或认证率。
两个版本都仍宽数百倍，当前不足以构造可用低资源证书。

## 来源与剩余义务

准备时发现扁平缓存的`PolygonBuilder.cpp`属于buffer目录，与overlayng的同名文件冲突。
在冻结实验前已按完整上游路径重新下载并区分保存；36项新清单全部成功，共67个所需上游文件（含COPYING）封存。
两次查找清单保留，不修改已有研究的缓存、源快照或hash。该准备发现不是科研批次的模型／程序异常。

当前还缺初始分量上界的观测证明、后续闭运算输出分量数及第二次联合的计数界、全部输入有限域和精度选择收紧。
不能直接给`unary_union([disks,closed])`传P=2，因为两个对象都可能含多个Polygon分量。
此外必须把允许人员世界传到所有后续中间框，再把原线性结点需求与有符号质量集合绑定。
量化尺度完全未知的放大仍很大，下一步优先研究实际回退尺度集合及可观测有限域条件，继续保留不可辨识反例。
门控保持隔离；F4及整体功能／稳定性目标仍未完成。

协议在`configs/step1/union_transfer_development_v1.yaml`和`union_transfer_development_v2.yaml`。
运行与各自冻结复判在`runs/step1_foundation_20261029/union_transfer_development_v1`、`_v2`及各自`_audit`。
汇总证据为`reports/step1_foundation_20261029/union_transfer_results.json`。
