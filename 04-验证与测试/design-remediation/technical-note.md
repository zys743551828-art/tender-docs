# 技术整改候选

本候选修正A01、A02、A03并消费产品四层覆盖、既有关键风险及DOCX失败接续定义。新系统仍使用Kafka＋Python Worker、PG17＋1024维pgvector RAG，正式评分仍为干净输入、完整相关章节和三次有效独立判断。应用尚未开始实现。

| 发现 | 实现设计与机器落点 | 复核要求 |
| --- | --- | --- |
| A01 | worker-functions.sql的lock_worker_bid与reserve_research_call；roles.sql普通/研究角色正向能力；domain.sql调用run/费用关联 | 真实非owner LOGIN领取、原子预留＋账本、finish；取消/fence/租约拒绝及标签/决定/路由写隔离 |
| A02 | 详细设计3.2精确三种投影、稳定as_of/下一效力边界；review_slots上下文、review_inputs完整证据根；identity_reference.py及7固定向量 | 不同入口/冻结时间同内容归并、nonce独立执行、真实时点/来源/方法变化新身份；并发归并须在实现时继续验收 |
| A03 | POST uploads/{id}/bindings、UploadBindingRequest/View、BatchCommit.accepted_binding_ids；不可变upload_bindings及current/committed指针；保留规约2.1 | 两稿竞争、显式重确认/另建文件、再次CAS、幂等、同bid/角色及7天保留期；API真实路径待实现 |
| PPR01/02 | ReviewCoverage四层、FindingView跨轮保留和生命周期；review_results冻结覆盖快照；保留规约5.1 | 关闭检测不隐藏既有未解决关键风险，人工有理由驳回/有效复核才关闭；读完不等于未漏提，评分不混核心分母 |
| U02 | UploadRequest.declared_source_document_version_id、DocumentView.source_relation、不可变document_relations；保留规约2.2 | 失败DOCX原件保留，就地补独立PDF；用户声明未核验，不冒充controlled_render或Word检查资格 |

当前为132个Schema、85条路径/92个操作、103张表、15份HTTP示例、7组身份向量。完整冻结来源摘要见[候选清单](technical-candidate.json)。本智能体执行了组件同步检查、Schema自检、15例形状检查、7向量重算及摘要关系断言；没有将它们解释为实际业务通过。

真实PG角色/预算/并发与独立契约检查由测试智能体在[整改检查目录](../design-remediation-checks/)另存证据，不覆盖原反例和历史通过结果；最终架构复审和PM放行仍独立进行。
