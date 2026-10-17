# 助手协议与SQL静审

结论：SQL与协议允许由PM在全新隔离库初始化，DDL级阻塞0；本次没有执行DDL，不是应用实现或生产放行。被审[SQL副本](observed-assistant-sql01.sql) SHA256 `30104431617fea8798bd21955d9dc8f349b70b1f6d25006a27c7705aecb3b8f9`；[协议副本](observed-assistant-protocol01.md) SHA256 `264e9f2cb52b6e54db9c1268043184c69a3b48a59f31fa6f9c17a0eb137dd5b9`。核对10张新表、组合身份、触发器和逐列授权，原始输入保持只读。

项目调用继续使用真实operation与普通账本；global为真实null项目范围，独立run/call/outbox，不伪造bid。API只能请求取消，Worker控制实际终态。消息、冻结job、输出、publication追加不可变。Action只给API更新终态，终态后不能重写；跨入口执行仍以既有action_consumptions.action_id为一次性receipt。普通Worker没有新增会话、Action、commands、公司或工程身份权限。

项目message与call的同项目、同operation绑定并非由现有单列FK完全证明，协议已明确交由API/Worker验证；不能把FK成功写成领域身份验证通过。global新增组合FK可约束会话成员、job/run/input/预算与对应call，但不能代替当前租约及fence检查。

## 必须在实现与独立验收中兑现的边界

1. [SQL第98行](observed-assistant-sql01.sql:98)的unresolved索引不是唯一防重约束。若两个不同run同时查空，各自的run锁和run_id唯一键都允许插入一次call，进而重复收费。Owner已明确采用事务advisory锁：按`assistant-global-call:intent_digest:profile_digest`一致派生，先锁逻辑调用，再锁run重核fence/owner/有效lease/取消，随后查询全global_calls相同intent/profile的reserved或unknown，最后预留。比request维度更保守，消息ID/会话修订不能充当额外授权。此方案无需改表，但应绑定后续实现候选。验收用两个run并发预留、跨run unknown、request变化而intent不变，外部调用最多一次；heartbeat/finish不反向获取逻辑锁造成死锁。
2. [SQL第184—204行](observed-assistant-sql01.sql:184)没有授API修改jobs、outputs、publications的能力。coordinator不可对这些表直接FOR UPDATE；Owner确认按真实bid排序→图锁→可更新会话→专用job advisory锁，插入唯一publication，并重核不可变输出、来源与当前上下文。需实际非owner正向执行，不只测试拒绝路径。
3. 全局私有主会话需由服务端成员确定，worker只读API冻结job，不读全量历史或工程元数据。项目与知识来源需按完整事实确认basis、身份authority、来源及祖先、真实trace实际供给守卫冻结和最终重核，知识派生文本不冒充原件引句。模型I/O与对象I/O都在事务外。关闭会话不应阻止原已排任务恢复，也不能触发重发收费。
4. 项目与global均应实证取消/失联接管、旧fence迟到、未知账单不可补发、成功响应字节可复用、远端已调用而写出失败的恢复。global每run唯一call和8192上限不等于真实货币费用预算。Outbox事件及DB精确身份需比较，正式topic固定，重放不再计算。
5. 工具提案只作pending卡；confirm/reject都消费同一个Action，跨成员及native A2入口最多执行一次。拒绝、stale和expired不能执行领域命令，执行受理不得写成业务任务已完成。A2生成沿原patch action_id，不增第二张确认卡。归档写入守卫须覆盖真正producer；只读问答资格与写Action资格分开。

以上是后续应用放行条件，不把尚未实施本身判为DDL缺陷。应在fresh库以真实非owner API/Worker执行正反向、组合身份与并发检查；SQL语法和运行权限由该阶段验证，静审未替代数据库执行。未读取私有配置、未启动消费者、未调用收费模型，也未审当前移动应用源码。输入摘要见本目录两个record.json。
