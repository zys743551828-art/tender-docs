# 团队实施规则

- 这是全新AI投标工作台，业务、架构和协议主记录在`/workspace/tender-docs`。不承接旧库、旧接口或旧模型资格。
- PM对整体交付负责。产品Owner负责产品方案；开发、测试、架构师与用户角色均可评审，产品统一回应。
- 当前目标与文件分工见`/workspace/tender-docs/00-项目管理/实施任务板.md`；团队交接与验收见同目录`团队协作与质量准则.md`。
- `packages/contracts/{schemas,openapi,sql,policies}`为已审目标契约快照；完整目标契约不表示实际接口已实现。实现API应另行生成实际OpenAPI和范围清单。
- 消息使用Kafka和独立Python Worker；PG17+pgvector；RAG及正式评分遵守已审用途与完整输入协议。
- API和Worker使用独立非owner LOGIN。初始化owner只用于全新建库及账号引导，不能用来修复业务权限失败。
- 正文、口令、签名URL及私有原件不入日志或Git；本地开发配置放被忽略的`.env`，权限600。
- 未配置模型／OCR／转换能力如实返回。测试适配器仅显式测试配置可用，不能把机制或模拟测试报告为真实效果通过。
- 合并前执行适当后端ruff/format/mypy/bandit/pytest、前端format/lint/typecheck/test/build和部署编排检查。测试、架构、产品、用户独立验收实际实现。
- 各角色遵守PM分配的文件边界，不覆盖并发编辑。技术或产品阻塞先提交具体反例及最小修正，不能静默改冻结主记录。
