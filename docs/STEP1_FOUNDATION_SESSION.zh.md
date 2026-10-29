# 静态基础会话接口与独立评价契约

版本`udc.step1.static_global.v1`。实现于`multigroup/foundation_contract.py`、`session.py`、`session_artifacts.py`，
独立物理审计于`analysis/session_audit.py`。这是已实现的观测→规划→执行→实际状态反馈模拟接口，
不是实时机器人backend，也不构成Step2动态人员或Step3受限通信的稳定性证据。

## 输入与状态

`ObservationFrame(frame_id,time_s,person_ids,BoundedMeasurement)`只包含观测、精确人员属性和声明误差。
会话持有已知房间、Guide实际状态、固定预算、每群上次已交付目标及身份、最后执行结束时间。
同一人员行可以重排，接口按稳定person_ids排序后处理；插入／删除、半径或需求变动均显式拒绝。
静态源保持冻结。带非零人员速度的Perception不能通过静态适配器，需要新阶段协议。

下一观测只能在上次实际执行结束后获取，不允许重用frame_id或把迟到帧伪装当前输入。
每次沿上次实际终态继续；规划／程序失败后也保存已执行前缀和实际时钟，禁止回滚物理状态。
失败使当前会话halted，后续恢复需显式独立协议，不自动循环重试。

## 输出与评价分离

`DELIVERED_OBSERVATION_PLAN`表示当前观测规划的部署交付，不声明完整真实覆盖。
输出`full_task_success=None`，独立评价才计算完整成功／部分服务、真实安全和原参考服务。
若物理到达后反馈程序异常，该事件仍记EXPERIMENT_ERROR、会计交付成功为false；
独立审计另记实际物理到达，不能把它重新计入成功次数。

身份变化输入没有对应的冻结评价世界：保存无运动拒绝，物理／服务评价明确不可用，不填零或伪造成功。
这不允许控制器读取真实群标签、出生多边形或真实服务分数。

## 日志和复现

每事件保存frame.json、observation.npz、metrics.json、planning.json、state.json、trajectory.npz和SHA-256 receipt。
程序错误另存runner_error.txt。目录与文件拒绝覆盖，公开返回的状态／轨迹数组只读。
这是完整事件的追加日志，不保证任意断电时刻的内存和半写文件均可恢复。
当前schema 3增加运动前intent、提交checkpoint、源码／环境绑定和排他写锁；完整事件恢复及限制见
[恢复契约](STEP1_SESSION_RECOVERY.zh.md)。这不改写早期schema 1／2工件。
研究调用方负责先封存全科学源码、配置和输入，再通过provenance绑定manifest；未绑定时日志明确标记not_bound_by_caller。
独立journal审计校验原工件、实际状态连续性、执行时间、速度／运动学、全区间物理安全及适用的服务界。
它不假装完成全控制器决策重建，完整研究协议的冻结轨迹／决策复判继续使用对应实验审计器。

手写开发fixture：

    .venv/Scripts/python.exe -m udc.experiments.foundation_session_smoke --output runs/<new-directory>

v1已完成3事件：两次低资源部署交付、一次按预定设计拒绝过期帧，独立物理／会计审计一致，完整任务成功0。
不将手写fixture计作新独立确认样本。
