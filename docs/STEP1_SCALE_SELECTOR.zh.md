# 实际精度选择器与给定尺度下界的坐标界

本轮直接测量本项目Windows GEOS 3.14.1 DLL的精度选择函数，
并实现给定内部尺度下界后的条件量化包络。没有执行新的完整buffer／overlay、人群部署或运动。
测量输入仍是旧保存阶段；全部允许未知世界的尺度下界尚未证明，门控继续隔离，F4未完成。

## 真实函数与绑定

[BufferOp](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/buffer/BufferOp.cpp)的选择过程使用

    b = RN(envMax + RN(2 * max(distance,0)))
    m = int(log(b) / log(10.0) + 1.0)
    S = pow(10.0, precisionDigits − m).

回退按12至6有效位尝试；这不证明某个旧buffer实际进入了回退。
[PrecisionUtil](https://raw.githubusercontent.com/libgeos/geos/3.14.1/src/operation/overlayng/PrecisionUtil.cpp)
的scalar `precisionScale(b,p)`使用同一表达式，`safeScale(b)`取p=14。
实际OverlayNGRobust的固定回退使用safeScale；本轮未将慢速inherentScale／robustScale误作该入口。

`analysis/native_precision_selectors.py`绑定已清点的真实C++导出符号和对应静态函数签名，固定C／C++ DLL hash。
对BufferOp构造临时点`(envMax,0)`，用C API核验其extent严格等于`[envMax,0,envMax,0]`，
再调用真实`precisionScaleFactor`。源码只读取包围盒最大绝对坐标，因此该点供给同一标量输入。
每次在finally销毁临时点。这里的点是原生测量工件，不是人员或新独立几何场景。
同时直接调用PrecisionUtil的scalar helper和safeScale，对照全部输出hex。
控制域明确限正有限输入，排除零／负log参数及不可控整型转换，不测试未定义C++行为。

## 条件坐标界

设实际归一化内部尺度`S ≥ L ≥ 1`，J为中点朝正无穷取整，
实际分支所有运算有限，输入binary64数满足`|x|≤M`。
此时点量化和零平移ScaledNoder都属于`Q(x)=RN(J(RN(x*S))/S)`。
取`u=2^-53`、`η=2^-1075`，有

    E1 = u*M + (η+1/2)/L
    |Q(x)−x| ≤ E1 + u*(M+E1) + η
              = ((1+u)^2−1)*M + (1+u)*(η+1/2)/L + η.

令A=(1+u)²、B=(1+u)(η+1/2)/L+η，则至多n次映射的累计位移不超过

    D_n = (A^n−1)*M + B*(A^n−1)/(A−1).

允许各阶段尺度不同，也允许FLOATING恒等，不依赖量化幂等。
`multigroup/lower_scale_enclosure.py`使用精确有理数按各轴D_n外扩框。
这是**已经给定有效L**之后的条件能力，不推断GEOS selector的全部输入范围，亦不证明所有分支运算有限。
不支持L<1的coarse-grid分支；尺度下界不能用请求grid的倒数直接代替归一化后的内部值。

## 旧阶段实测

父批为`round_buffer_source_development_v1`及其冻结复判，坐标来自旧v2阶段库。
720个旧输入（80真值／640观测）、2520群，每群三个已保存偏置阶段，共7560次传递。
每阶段读取精确保存源坐标的envMax；原始源框R来自父批已审查的有限偏置／圆弧／辅助坐标来源，
不能当作在线未知人员世界的源框。对全部7个回退位数实际调用选择器，取归一化尺度最小值L。
以此L、两阶段量化预算重算R后的条件框，检查父批保存输出。

| 项目 | 结果 |
|---|---:|
| 输入／群／阶段传递 | 720／2520／7560，全部完成 |
| 保存输出坐标检查 | 4705946 |
| 原生BufferOp及scalar／safe选择器调用 | 120960 |
| 为旧阶段创建／检查extent／销毁的临时点 | 各7560 |
| 原生完整buffer／overlay调用 | 0 |
| 测得最低归一化内部尺度 | 10000 |
| 条件框宽度和／父批原始源框R宽度和 | 1.0000041163–1.0000520096 |
| 条件框违反／未执行输入／未检查阶段 | 0／0／0 |

7551阶段的7个尺度为10^10至10^4，另9阶段为10^11至10^5。
全部测得尺度均为精确binary64整数，源码的近整数归一化不改变它们。
这7560个新框都被父批任意正精度的粗框包含；宽度增幅约0.0004%–0.0052%仅是**R之后的量化外扩**。
它不能与上一批完整初始联合框仍膨胀469–1296倍混为同一口径，也不是完整人员误差或服务损失界。

## 临界值诊断与独立复判

取10^-6至10^6各十进制幂的最近binary64值及两侧邻值，再分别用距离−0.4、0、1.3：117个确定性标量条件。
另实测45个已知整数幂输出，绑定选择器的pow表。该校准新增117临时点、1917次scalar selector调用。
没有新完整buffer，没有确认内部回退是否实际执行。

117条件中40个实际m与“精确floor(log10(b))+1”的注释解释不同。
独立有理分类发现32条件的精确实数log表达式向零截断本就不同于该floor解释；
22条件的原生m又不同于精确实数log表达式向零截断。这些条件有重合，不能相加为54个不同异常。
例如b=1000的原生m=3、精确实数m=4；
b=`0x1.869ffffffffffp+16`（100000的下邻值）原生m=6、精确实数m=5。
后者可使尺度比朴素十进制推算小10倍，不能仅按源码注释或实数floor公式推导尺度下界。
这些是选择表达式的诊断差异，**不是本批已采用契约的违反或原68失败原因**。

独立冻结复判再次调用全部7560阶段与117校准的真实函数，含45幂表，共122877次selector调用及7677次临时点生命周期，
全部输出hex相同。对每阶段另外按逐次E1／末次舍入公式累加，核对闭式位移界、全部坐标及声明范围，全部一致。
复判不调用候选尺度包络函数；并以有理十进制分类核验两类临界差异。

## 文献及剩余范围

[Microsoft UCRT说明](https://learn.microsoft.com/en-us/cpp/c-runtime-library/floating-point-support?view=msvc-170)
只称多数结果接近正确舍入，并明确可能有更大误差、CPU和版本差异，不能据此填入普适1 ULP契约。
[Lee等，POPL 2018](https://www.microsoft.com/en-us/research/wp-content/uploads/2017/12/popl18.pdf)
提供对特定Intel math.h实现的证明，尚未绑定本机实际log／pow实现，不能直接移用为本机保证。
这些来源提示后续验证路径，不把进一步底层形式验证自动追加成用户要求的交付物；
需要的是当前声明模型下有效、可审计的尺度选择界，仍应寻求可执行的保守方法。

导出清点预检曾一次把PE的AddressOfFunctions偏移28误作AddressOfNames偏移32，
已按[微软PE表规范](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format#export-directory-table)修复并保留lookup记录。
当时未开始科研批次、未调用原生函数；正式研究／复判无程序、模型或原生异常。

新增3项定向检查通过；全量551 passed、1 skipped／67.95s，pip check与旧审查包61文件／3177成员hash核验通过。
下一步须把观察允许的envMax范围、实际表达式／参数选择与有效L绑定，同时继续分量／有限域及原线性需求质量义务。
原阈值、门控、原失败与停止分母不变，预留5000–5029未运行；整体目标仍未完成。

配置：`configs/step1/scale_selector_development_v1.yaml`。
工件：`runs/step1_foundation_20261029/scale_selector_development_v1`及同名`_audit`。
摘要：`reports/step1_foundation_20261029/scale_selector_results.json`。
