# 未知正精度参数的统一坐标界

`multigroup/universal_precision_enclosure.py`给出不依赖具体正scale／grid的条件外包，
并结合已审查的默认Point.buffer坐标来源，构造包括原精度及固定精度回退的粗轴框。
它减少了取得任何外包时对精确回退参数的依赖；**不提供可用的低资源服务界，也不恢复运行门控**。
F4、独特性和整个稳定性闭环仍未完成。

## 标量定理

固定GEOS 3.14.1相关标量表达式依据[PrecisionModel.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/geom/PrecisionModel.cpp)
和[math.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/util/math.cpp)。
设u=2^-53、η=2^-1075，RN为binary64最近舍入，J为就近取整数且中点朝+∞。
对正有限S或G，且相关运算有限，
Q(x)=RN(J(RN(x·S))/S)或RN(J(RN(x/G))·G)满足：

|Q(x)|≤C|x|+η，C=2(1+u)²。

证明分两支：

1. k=J(t)=0时，输出为0，不需要引入η/S或ηG。
2. k≠0时，|t|≥1/2且|k|≤|t|+1/2≤2|t|。
   若第一次乘／除的精确结果绝对值≤1/4，单调RN不会得到绝对值≥1/2；
   因而该精确结果处于正规数域，可用不带次正规加项的相对误差界。
   结合恢复坐标的最后舍入，即得上述C与η。

正参数量化单调且固定0，因而保留坐标符号；FLOATING恒等路径也被该外包覆盖。
对区间[a,b]，一阶段可用
[C·a−η（a<0，否则0）, C·b+η（b>0，否则0）]。
迭代n个阶段得到C^n的增益及几何级数形式的η项；0坐标保持精确0。
参数可在不同阶段改变，但不覆盖非零平移、FLOATING_SINGLE或任意未审查几何运算。
这是有限运算下的坐标后置条件，函数返回不代表已证明运算不会溢出、库一定终止或输入世界可行。

因子2不能在完全未知参数下简单改成1：x=1、网格2时输出为2。
若再换成网格4，输出变为4；同一网格2重复量化则仍为2。
四倍例子属于两个独立参数，不是BufferOp内部同一参数必须放大四倍的证明。
共享参数关联、可观测参数范围或原精度路径证据都有可能收紧该界，仍须单独推导。

## 默认Point.buffer的坐标流组合

`default_point_buffer_box`只覆盖默认FLOATING Point、圆cap、非single-sided的固定源码路径，
并以有限单位范围三角分量、有限正回退模型、binary64运算及通常XY存储／复制语义为前提。
源码人工复核包括：

| 环节 | XY外包传递 |
|---|---|
| 圆弧源坐标 | 使用已实现的[圆弧条件界](STEP1_CIRCLE_SOURCE_ENCLOSURE.zh.md) |
| 原精度结点 | LineIntersector包围盒／端点后置条件，IntersectionAdder插入这些坐标 |
| 固定精度源线串 | 插入前makePrecise，计一个未知参数量化阶段 |
| 固定精度结点 | 零平移ScaledNoder包装单位SnapRoundingNoder，计另一个阶段 |
| 重复点删除及子边 | 删除或复制已有坐标，保持轴框 |
| 图到环及多边形 | EdgeRing从边坐标序列取点，PolygonBuilder及GeometryFactory复制／转移这些环 |

主要连接依据为[BufferBuilder.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/BufferBuilder.cpp)、
[EdgeRing.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/geomgraph/EdgeRing.cpp)及
[PolygonBuilder.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/PolygonBuilder.cpp)。
结合此前[结点坐标来源契约](STEP1_SNAPROUND_SOURCE_CONTRACT.zh.md)，最多两阶段的统一框包含两种路径的有限输出。
原精度路径无需量化，其原始框也包含在两阶段统一框内。

本轮封存34个上游文件及原版权／COPYING，包括C API、工厂、图、环、偏置源、结点和精度文件。
这是人工源码层条件推导，没有形式验证所有依赖、编译器或二进制；也没有插桩证明旧原生批触发了哪些回退。
它不适用于一般线／多边形buffer，更不能将后续布尔组合、简化、正负偏置和需求质量省略。

## 控制开发与独立算术复判

协议冻结16条网格路径×11个x输入，共176条路径；点坐标取(x,−x)，
覆盖0、次正规输入、正规数边界、半整数相邻值、1的相邻值、大数及最大有限值。
全部轴和全部阶段先在固定标量模型中检查有限性；不符合条件的整条路径不调用原生库，也不重抽。

| 项目 | 数量 |
|---|---:|
| 预定路径 | 176 |
| 有限条件准入并原生重现一致 | 169 |
| 有限域未认证，原生调用前拒绝 | 7 |
| 实际原生点量化调用 | 211 |
| 最大观察路径坐标放大率 | 4，来自不同网格2→4 |
| 旧初始圆盘源框组合 | 2894 |

7条拒绝均是固定模型中的运算溢出，完整输入和原因保留。
它们不是已观察到的原生失败或通过样本；也不能由跳过它们声称极端数值域已稳定。
真实原生异常或模型界违反会保存错误／坐标工件并停止本批。
每阶段都重新构造FLOATING Point，避免相同精度元数据的复制捷径。

另冻结复判再次实际执行211次原生调用，按hex比较输出，且用另一套float／modf表达式核对。
直接检查422个坐标增益／符号／非零分支条件，并重现全部7条有限域拒绝。
2894个旧源框的两阶段组合用独立闭式C²及(C+1)η核对，全部一致。
收尾另逐记录核对父批原中心框、原浮动源框和实际距离，并核验父批receipt及payload哈希、唯一人员案例分母；
结果单独保存为`source_binding_audit.json`，避免只检验未锚定来源的自洽公式。
拒绝准入的复判复用固定标量模型；不声称它也是第二套独立溢出判定实现。

相对旧浮动源框，新框的坐标宽度和为44.2313–171.1853倍。
这揭示当前尺度无关界很松，不能据该条件外包就宣称低资源证书可用或保持收益恢复。
没有新增原生圆盘调用；旧257868圆盘调用是父批证据，不能与本轮211点量化调用混成新部署样本。
新增独立人群输入0、运动0；旧188个跨预算见证没有被排除，原参考、失败、hash及阈值保持。

4项新增定向检查覆盖两倍必要性、不同参数四倍／同参数重复、极小scale的零分支和默认点buffer组合。
全量536 passed、1 skipped／88.98s，pip check及原审查包61文件／3177归档成员hash核验通过；不替代完整稳定性验收。

## 下一步

先把此粗外包作为有明确范围的基础，继续研究实际参数关联及可观测的更紧路径条件。
同时覆盖剩余几何坐标生成，尤其任意有限非退化短边的偏置、闭运算和布尔构造；
之后才有完整的原参考曲线外包与有符号需求质量保证。
现阶段不能把“任意正参数已有粗界”写成“所有BufferOp路径和服务已获证明”。
方法独特性还需当前文献、基线、消融和新输入独立确认，不自称首创。

协议：`configs/step1/universal_precision_development_v1.yaml`；
工件：`runs/step1_foundation_20261029/universal_precision_development_v1`及同名`_audit`；
统一摘要：`reports/step1_foundation_20261029/universal_precision_development_v1_results.json`。
