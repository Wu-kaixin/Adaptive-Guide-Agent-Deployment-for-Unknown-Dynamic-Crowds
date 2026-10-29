# Snap-rounding结点的坐标来源契约

固定精度研究后的源码复核进一步给出单次结点操作的XY像集外包。
这是GEOS 3.14.1固定源码下的条件推导与算术核对，不是编译二进制的形式验证，
也没有新增直接原生SnapRoundingNoder调用或内部路径插桩。

核心实现位于`multigroup/snapround_enclosure.py`，未接入默认服务门控。
它与此前要求输入也留在框内的量化闭包不同：这里直接包围量化像集，不要求结果框同时包含原输入框，
单次调用无需假设量化幂等或无限次闭包迭代。

## 输入与坐标生成

输入须是有效、有限的segment strings，全部初始顶点及已存在节点都在给定轴框B内。
精度模型为正有限固定scale，标量乘除满足[已声明的binary64模型](STEP1_FIXED_PRECISION.zh.md)。
只断言正常返回的有限XY坐标，不保证相交存在、拓扑正确、成功终止或局部定位精度。
源码中的坐标来源逐项检查如下：

| 环节 | 坐标来源与集合 |
|---|---|
| 未量化交点 | 默认LineIntersector的包围盒检查／端点回退，使有限输出仍在全局B内 |
| 邻近顶点结点 | 复制原输入顶点，仍在B内，不用距离谓词的精度来估计坐标误差 |
| 像素中心 | HotPixelIndex先makePrecise，再由HotPixel保存原坐标；位于Q(B) |
| 初始取整线串 | getNodedCoordinates后的每个坐标makePrecise，位于Q(B) |
| 段／顶点吸附 | 插入已有像素中心或已取整顶点，仍在Q(B) |
| 新节点及子边 | SegmentNode构造及SegmentNodeList端点、折叠、拆分只复制上述XY |

主要出处为[SnapRoundingNoder.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/noding/snapround/SnapRoundingNoder.cpp)、
[SnapRoundingIntersectionAdder.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/noding/snapround/SnapRoundingIntersectionAdder.cpp)、
[HotPixelIndex.h](https://raw.githubusercontent.com/libgeos/geos/3.14.1/include/geos/noding/snapround/HotPixelIndex.h)及
[SegmentNodeList.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/noding/SegmentNodeList.cpp)。
其余构造、头文件与原版权／COPYING也已封存，完整21文件hash在本批manifest。
对坐标序列基本存储／复制按其通常XY语义作前提，没有完成全部依赖或编译器的形式验证。

因此，直接SnapRoundingNoder单次返回XY位于逐坐标Q_point(B)内。
这个结论只需要全局轴框，不会将它误用为每段自己的包围盒或严格凸包。
像素查询、树内随机插入顺序及选择谓词可以改变选中哪些已有坐标；这个外包不据此保证路径或拓扑一致。

## BufferOp中零平移缩放包装的条件结论

[BufferOp.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/BufferOp.cpp)的相应路径
构造单位精度内层SnapRoundingNoder和以fixedPM.getScale为S的ScaledNoder。
[ScaledNoder.h](https://raw.githubusercontent.com/libgeos/geos/3.14.1/include/geos/noding/ScaledNoder.h)的默认平移为0；
[ScaledNoder.cpp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/noding/ScaledNoder.cpp)在S≠1时先缩放取整，最后恢复坐标。

S≠1时，输入轴框经K(x)=J(RN(x·S))得到整数极值框I。
I的极值为可表示整数，单位精度量化固定这些极值；由单调性，内层结点输出仍在I内。
最后逐坐标RN(z/S)给出Q_scaled(B)外包。
S=1时包装器跳过缩放／恢复，由内层单位精度的直接结论得到同一Q_scaled(B)。
不将点量化的内部gridSize分支混同于这里使用S的缩放路径，不覆盖非零平移或其他内层noder。

## 证据与未完成项

21个固定版本上游文件及5个本地源码／测试文件已封存。
复用此前固定精度批的9种非浮动模型、3种端点框宽度和7个夹具，共189个坐标框条件；
逐项以另一套float／modf表达式核对直接和缩放外包的极值，378框／1512坐标极值全部一致。
这是标量算术核对，不是189次直接原生noder运行；新增人群输入0、运动0。
3项定向测试通过，包含像集收缩、点量化与缩放分支不同，以及S=1包装器分支。
新增代码后的全量检查517 passed、1 skipped／62.47s；pip check通过，原审查包61文件／3177归档成员hash核验通过。

这些后置条件推进了此前“未审查结点坐标生成操作”的义务，但不覆盖整个BufferOp：
仍须界定允许人员世界的原曲线／简化／偏置输入框，覆盖全部可能回退S及结点外的坐标生成，
再构造原参考的有符号需求质量界。服务认证率、独特性及全面稳定性仍未闭合。

工件：`runs/step1_foundation_20261029/snapround_coordinate_source_audit_v1`。
manifest的scope显式标记manual source-level review；完整输入框与两种输出框保存为`range_checks.json`。
