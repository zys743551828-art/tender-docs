# 任务草案05最终增量交接

候选SHA `4f91d6aafcc397e89c19acbf626631853663a5c53e8ed95db300bc802856d650`，317固定输入，6生产/3测试自有文件。F05固定main只作为基底；本批未修改生产main/exporter或运行8808。

提供项目/成员私有草案分页、创建、GET恢复、PUT结构化编辑、POST save一次保存模板。原始请求、创建时间和所有修订保留。未解决的类型/范围/触发条件不能save；所有ID来自可信选择及同项目校验。保存草案不创建模板/计划/run；明确save才创建模板，定时配置才建计划。save不立即运行，归档新写拒绝、旧ACK/历史可读。共享SSE仅draft_id/revision，不含私有原文。

所有新增模型使用TaskDraft*专属名称；真实任务Template*类型覆盖旧规划映射；原编制DraftView保持完整。实际契约预览 `actual-task-draft02.openapi.json` 是固定main明确组合草案router/extender，164路径/193操作/325 schemas，SHA `4af4b5d611539485be74aeed8b98f07d95fb4bc4e9c16572a781f219e5877ae2`，与04逐字相同。集成Owner须include task_draft_routes.router并在目标映射后extend_tasks(actual)，再以最终组合actual生成前端类型。

04两个独立问题已修：带限定的时间和未支持的日历请求保持manual/pending，不把“每天9点以后”当09:00；save同事务追加包含saved_template_id的新草案修订，created_at保持、updated_at更新，GET强ETag改变。新key旧tag保存/编辑412；新tag再save409；原成功key重放201且只一模板。一次用户保存点击可先PUT明确编辑并获ACK，再save；失败/断线保留草案，不额外聊天确认。

固定101回归通过（45解释器纯、9真实PG/ASGI草案、3实际契约、44原任务回归），执行前后317SHA一致；9源ruff/format、6生产mypy/bandit通过。独立架构56项通过并关闭TD01/TD02，报告见 `task-drafts-architect-review/candidate05`。无broker/消费者，回归全部外部HTTP硬拒，实际尝试0；配置标记与账本是明确合成夹具，0真实模型调用。

原01误用运行中联调库的失败、02/03中间结果、03同名schema冲突、04时间与ETag反例全部保留，不倒改成通过。原01合成项目已终止/归档，无真实上传/实交项目受影响、无删除。

当前仍是受限中文解释器，通用NL、完整补遗语义、真实调度运输和真人体验均不在本结论。前端开始走实际页面；显式Mock助手设施在另一新库，不充当真实模型效果或Kafka验收。
