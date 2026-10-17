# 任务草案整改复核

结论：任务草案05在本片架构范围内通过；04的TD01时间解释和TD02强ETag问题均闭合。可以交PM安排集成与前端验收，不代表任务调度/消费者或完整产品已经验收。04失败证据和原41项结果保留原样。

输入 [candidate05](../../task-drafts-development-checks/candidate05/candidate.json)，SHA256 `4f91d6aafcc397e89c19acbf626631853663a5c53e8ed95db300bc802856d650`；317文件在执行前后全部匹配。与04只有解释器时间线索/限制词、save追加真实草案修订、PUT校验顺序及相应两份测试变更。actual OpenAPI与04逐字相同，SHA256 `4af4b5d611539485be74aeed8b98f07d95fb4bc4e9c16572a781f219e5877ae2`，不重复无变化的A2类型冲突检查。

TD01：直接执行原5种时间表述（每天9点以后/之前、工作日9:30、明天9:30、指定9:30），现在均manual、trigger待明确、ready=false。对这5种表述进一步用真实登录ASGI创建草案后/save，均409 TASK_DRAFT_NEEDS_CLARIFICATION，项目下模板/计划/运行/operation/model_calls/outbox均未新增。正常每天9:30仍生成正确候选计划，显式保存的既有正向也通过。详 [时间投影](temporal-counterexamples.json)、[新增实际保存断言](runtime/tests/test_task_drafts_integration.py)、[时间保存JUnit](time-save-junit.xml)。此结论覆盖受控边界，不宣称一般中文时间理解精度。

TD02：使用04原场景创建→GET→save→GET，保存前revision1，保存后真实revision2；created_at保留、updated_at推进，saved_template_id写入新修订，强ETag从1变2。新key持旧tag再次save或PUT均412；当前tag再save409已保存。原成功key持原tag重放仍201并返回同一模板，仅1个模板和1条保存link，原ACK没有因版本推进失效。详 [实际结果](etag-counterexample.json)。静读确认模板创建、草案新修订、唯一保存关系和Command完成在同一事务；任何中途异常整体回滚。bid锁和RC前置、ACK优先恢复、闭档新命令阻断保持原协议。

独立执行56项通过：55项解释器/真实非owner PG与登录ASGI草案机制，加1项包含原5时间表达的/save拒绝组。第二次定向运行10项deselected是已在第一次运行的草案组，无skip或失败。原隐私、跨成员/跨bid、分页、并发CAS、单草案单模板、闭档旧ACK及新工作阻断等相关草案回归仍通过。固定源身份与全部证据摘要见 [review-record.json](review-record.json)。自有运行树仅测试追加反例与辅助public schema，未改被审源码、SQL或原结果。

无DDL、owner业务执行、模型付费、对象访问、Kafka消费者或主服务变更。尚未验证主main/exporter实际接线、前端“先PUT ACK再/save”的恢复体验、调度真实执行和一般NL效果；不能用本片通过替代这些后续验收。
