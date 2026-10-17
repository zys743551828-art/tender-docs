# 编制与新增知识引用生命周期增量02

candidate.json 是精确179输入；主后端07集成的是6个生产文件，tests/test_authoring_knowledge_lifecycle.py 为新增机制用例。不要覆盖助手01、内容06或旧知识候选的原始证据。

apps/api/authoring.py 的命令统一调用 work_access.begin，嵌套draft/section/action在返回旧ACK前验证真实bid范围。所有修改入口的旧ACK仍可读，新命令在关闭、终止或归档时拒绝。repository.lock_draft/create_draft提供直接领域入口守卫；generation、proposal、save、build freeze均实际经过这些入口。publish_build以受限Worker同锁复查，不能迟到新增生成原件。

企业知识全局维护不按原来源项目阶段封死；查询、trace、历史、派生索引不加普通work限制。新增project reference两类入口及其实际cite/cite_unit领域入口在bid锁内校验工作可写，保留既有更严格的已提交项目引用规则和来源/身份资格。归档后历史引用仍可读，不能新增引用。

5个新真PG/ASGI用例通过，包括pg_blocking_pids实证等待后观察已提交close；原回归106通过，另3环境skip/7真实build排除。01缺少未变rag_coordinator依赖的1项import失败保留；02只增加该原冻结依赖，原178文件摘要完全相同，该单项重跑通过，累计107原受影响机制通过。所有调用均Mock/内存对象，0真实模型/OSS/consumer。6生产文件ruff/format/mypy/Bandit通过。单独authoring+knowledge的实际28path/33operation契约与助手01基线摘要相同，未扩大API面。

正式backend07注册、全producer/Worker组合、实际Kafka/OSS、浏览器、独立架构/QA及真实质量仍另验；本增量不把它们算通过。主源码所有权已交还backend07组合；更丰富A2树、模板、计划、表图、Word导入仍是后续产品需求。
