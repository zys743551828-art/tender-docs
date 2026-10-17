# 技术整改复核候选

本候选只闭合冻结复审发现及产品Owner已确定的人工补漏行为。原[技术候选](technical-candidate.json)及独立失败/通过证据均保留；新冻结身份见[候选清单](technical-candidate-02.json)。应用仍未开始实现。

| 修正 | 机器与规约落点 | 验证边界 |
| --- | --- | --- |
| 评分时点接线 | retrieval.sql的score_context_chunks使用i.as_of；RAG规约与详细设计3.2明确frozen_at仅审计 | 独立真实PG需覆盖不同as_of/frozen_at、known_at/valid_until/祖先与撤回 |
| NULL调用身份 | reserve_research_call对request/profile/run/reserved_amount均用IS DISTINCT FROM | 独立LOGIN需拒绝将既有NULL普通调用复用为研究调用，预算/账本不产生部分效果 |
| 漏提条款补充并确认 | POST bids/{id}/rule-set-corrections、RuleSetCorrectionRequest/RuleAddition/RuleScope/RuleBand/RuleEvidenceQuote；bid_profiles.current_rule_set_id及不可变rule_set_corrections | 当前包/资料CAS、有据原子新包与新增人工决定、等价原决定沿用、旧评审过期；引用/效力/字段错误整笔拒绝。API/浏览器真实行为待实现 |

用户显式“补充并确认条款”一次同时确认新增条款，不引入第二次确认或岗位审批；保存规则不等于正文满足，也不自动重复模型调用，提供按新包重新评审入口。现有缺口不因补一项消失。人工来源用origin_kind/manual_origin_rule_id/provenance_status固定；后续提取必须与人工规则合并，模型再次漏提仍保留，有据等价项去重，效力不明转待核验及覆盖缺口，不能静默消失。详细事务见[保留功能规约第3.1节](../../05-技术与设计/detailed-design/保留功能实现规约.md#31-人工补充并确认漏提条款)。

计数为137个Schema、86条路径/93个操作、104张表、16份HTTP示例、7身份向量。开发自检通过组件同步、Schema、全部16例、7向量和4个非法补漏请求形状；真实PG权限/时点及并发机制由独立测试与架构复核另存结果，未把Schema拒绝等同真实业务校验。
