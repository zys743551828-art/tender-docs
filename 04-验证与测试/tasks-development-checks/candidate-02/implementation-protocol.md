# FR040任务控制面02：实现与接线边界

本候选保全模板基础01 `6dcab3b2807598cf6b88ebb9066c0908cd6c9beaa5b1764fa716859c3c2be4f9`及原24项冻结证据。只改packages/tasks、task_routes和自有tests；不改main、reviews、engine、SQL或已有权限。全程无模型/OSS/Kafka调用，不启动任何消费者或常驻runner。

## 实际能力

- 模板与运行分开。四预置/task-kinds明确能力：否决走既有review控制面；章节走既有assistant.jobs.queue_generation的Kafka/账本/只提案端口；评分明确TASK_SCORING_NOT_QUALIFIED预检拒绝，不造成功operation；补遗只执行source_effectivity.evidence_impacts已登记影响快照，结果持续标registered_effectivity_only/semantic_impact_coverage=unknown。完整语义补遗建议handler未实现，不能以此关闭完整补遗模板需求。
- RunRequest只接受严格rerun布尔。手动命令经会话CSRF、bid→command锁序、模板强ETag和生命周期校验。相同命令重放原run_id但重读当前状态；当前输入重复默认复用，等价在途否决跨模板修订也复用。明确rerun不打断在途等价工作；同输入reserved/unknown计费，换幂等键、改模板指令/版本或nonce均不授权再发。
- task_runs保持append-only，operation_id是实际最初准备操作，不当最终评审身份。冻结bid/body/material修订及快照、规则/事实头、原件摘要/parse、章节修订、method、用途、模板version、触发事件/原定时刻、操作者/命令和摘要。初始准备尚未知的parse/rules为null，只允许同原件范围完成准备一次；真实就绪context另写唯一server audit+digest，不改原task_run。此后规则、事实、正文、解析、来源选取、method/检测设置漂移都superseded，不借auto_review重基。
- RunView分别返回root_operation_id/final_operation_id、stage、state和产物。parse成功仅awaiting_review。最终review只认唯一review.frozen request_id、真实commands.actor、唯一就绪context、完整head/method/parse集合绑定；缺失、多解或错上下文均unknown，不猜current_review。准备和最终review全部同request的operation都纳入未知费用守卫，歧义链接不能隐藏账本风险。源有明确previous_request_id替代事实才标superseded。底层现有自动评审intent等价时复用真实request/actor并分别记录任务发起者，不重复排队。

## 严格接线门槛

否决dispatch默认失败关闭：只有 `reviews.TASK_INPUT_GUARD is packages.tasks.inputs.review_guard` 才能创建运行，否则503 TASK_REVIEW_EXECUTOR_NOT_READY；预置读口如实显示未接通。Backend下一片必须把guard接到reviews._advance_head开始处和freeze_candidate中_same_head检查之前，False时用_write_intent持久化superseded并返回，完成后才注册该callable，不能只设标志。既有锁05/main/消费者未改。独立机制测试显式注册此callable并直接验证guard与最终review链接，真实共享控制面接线仍未验收。

事件hook `coordinator.material_committed(c,*,bid,event_id)`只认同项目实际materials.committed audit UUID。上传接线可在audit后按bid+command_id+event_type查唯一身份；或由runner补扫，客户端不能自报任意事件。事件身份＋模板version＋目标/用途/输入共同去重，同输入不同事件复用实际run并有各自trigger receipt。模板启用事件时持bid锁捕获已经存在的event ID基线（上限4096，超限明确失败），避免事务开始时间误序，也不在保存/恢复模板时追发旧事件；旧01没有此权威的事件版本不倒授自动资格，需明确保存新版本。暂停只停新触发，不取消在途操作。

## 计划与通知

计划保留PG schedules；tick先bid后schedule锁，有界处理当前version且未暂停的到期行。落后不足一分钟可执行原定slot一次，schedule_receipts与trigger ledger原子登记；更早遗漏默认全部跳过，记录from/through、count=null/not_enumerated，不无界枚举或补收费，推进到当前之后。恢复读口给明确run-current-once地址，仍需用户手动新命令/ETag冻结当前输入。不同slot可复用同输入已完成结果，receipt记录原定时刻，不冒称新算过。

站内通知只由显式coordinator按真实状态/结果摘要合并写一次audit＋stream；GET/轮询无写入。业务待决定对象不会被任务模板自动确认或应用。runner是API-role有界控制面，不是Worker/消费者：项目、事件、计划及通知分批，通知checkpoint用bid锁＋单调epoch并只记audit，不用事务开始时间排序，不随每次空轮询推送通知。--once明确返回next_after，常驻模式轮转项目页；尚未启动或验证常驻服务可用性。

## 验证与未完成

开发44项通过：01原24、20新增真实非owner PG/ASGI机制，包括失响应重放、parse不冒充review、四类能力边界、章节真实writing_plan/outbox且正文未应用、事件重复/同输入/旧事件/保存前基线、并发slot与400天遗漏跳过、暂停/归档、未知准备费用及换版本、输入漂移、最终绑定正例与错上下文/重复/缺权威、无guard失败关闭、通知合并和跨操作者读取。所有资料、review成功行和unknown账本为明确合成机制夹具；没有实际provider调用或正式判断效果。runner/guard的共享接线、语义补遗handler、正式评分资格、自然语言模板生成、完整UI及应用用户验收仍未完成；不能以此声称FR040全部交付。

实际独立router为10路径/12操作，OpenAPI列RunView/RunPage/RunRequest等actual模型。目录当前使用UUID顺序的有界分页，新增观察从首屏刷新或SSE获得，不宣称按真实动作时间排序的全局历史游标。质量ruff/format/mypy9源/bandit通过。无新SQL，不使用owner跑业务。
