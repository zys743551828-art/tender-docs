# 企业知识与可靠复用

本模块实现公司级知识候选、结构化适用性、成员明确发布、独立版本、停用与引用追踪。历史案例只成为可核查参考；发布知识、选用参考都不会创建项目事实，不增加正式评分或训练资格。原件和已登记引用长期保留，停用不会覆盖旧报告或替换已提交正文。

## 实际接口与团队接线

主 API 注册 `apps.api.knowledge_routes.router`；实际 OpenAPI 最后调用 `packages.knowledge.contracts.extend_openapi(actual)`。它只扩展实际协议，不改冻结的目标 schemas/SQL。原有目标 POST `/knowledge` 仍接 `KnowledgeCandidate`，仅建立待完善候选，不把一段 scope 字符串猜成完整适用性。

| 路径（省略 /api/v1） | 用途 |
| --- | --- |
| POST /knowledge；GET /knowledge | 建候选；独立关键词搜索/目录 |
| GET /knowledge/{id} | 最新维护版本、当前发布版本与各自状态 |
| POST /knowledge/{id}/versions | 严格 `KnowledgeVersionRequest`，追加候选新版本 |
| GET /knowledge/{id}/versions；GET /knowledge/{id}/versions/{version_id} | 有游标的版本列表；不可变历史版本 |
| GET /knowledge/{id}/source-positions | 从已选原件的真实解析/页生成可选引用，前端不推算 source UUID 或 revision |
| POST /knowledge/{id}/submit；/publish；/withdraw | 目标 `DecisionRequest`，decision 分别为 submit/publish/withdraw |
| POST /knowledge/{id}/references | 明确目标项目/对象、用途、版本、摘录及适用说明，登记参考 |
| GET /knowledge-references/{citation_id} | 当时摘录/适用说明/来源清单与旧版本正文，另示当前失效原因 |
| GET /knowledge/{id}/impacts | 在办、已提交、归档、已结束/终止项目及已登记研究/模型派生影响 |

人工写入沿用登录 Cookie、Origin、CSRF、幂等键。版本/送确认/发布/停用使用强 `If-Match`；ETag 绑定完整只读表示，祖先失效或到期使表示变化时也变化，不仅绑定条目 revision。幂等返回原命令结果；引用重放还会重核当前资格，不能借旧引用 ACK 绕过停用。失冲突不覆盖他人决定。

D03 接入独立身份模块：项目筛选/新引用必须通过 `identity.snapshot` 的 confirmed 绑定及真实 authority_id/digest；bid_profiles 中存在 bidder UUID 本身不是确认。主体/工程 profile 被改而原绑定依据不再匹配时，即使 UUID/地区/专业仍相同也拒新用，维护目录和历史只读仍可查看。可信 `eligible_knowledge_sources` 返回 project_context（身份快照、bid/material/settings/fact heads 与事实决定基准摘要）；新引用在同一项目锁内冻结到审计，`check_reference(...,expected_context=原可信上下文)` 与 `assert_project_context` 供模型后最终采用重核。引用同键重放必须与原上下文完全一致，同 UUID 重新确认产生新 authority 后旧 ACK 也不能充当新用授权。actual KnowledgeReference 可选 project_context 是为旧 D01/D02 历史保留可读；新引用总会返回该字段。旧引用没有当时上下文时保持历史可读，但同键新用重放明确拒绝，要求重新查证选用，不补造历史身份。

## 候选、适用性与发布

`KnowledgeVersionRequest` 包含 title/content/categories/scope、applicability 和 source_refs。分类为 company_fact、method、standard、retrospective、chapter_reference；同一版本可多分类。本公司可用事实必须明确单一主体且来源全属该主体，外部企业或事后标签不能换成可用本公司事实。

适用性必须给出 bidder_ids、可选指定 bid_ids、明确 regions/engineering_types、conditions、purposes、validity_kind/from/until、validity_basis、specificity_note。地区、类型、主体和用途不能用缺失或空集合表示全适用；空 bid_ids 仅表示已明确主体/地区/工程类型范围内未再限具体项目。conditions 对应当前 fact_heads 的 subject/scope/name/value/unit（含值类型）精确等值条件；所有匹配事实都必须有当前 confirmed 决定，且确认时 fact_identity 与当前值/依据身份、完整祖先来源清单仍精确一致。共用 A1.2 fact_sets 的 identity、decision_basis 和 evidence_basis(publish=False)，不发布/修补来源。跨事实 ID 的决定沿用必须有逐步可验证的等价映射；无决定、暂缓、驳回、无依据、旧解析、撤回来源、未知沿用或任一冲突项均拒用，不能先过滤异常事实再取一个相等值。空 conditions 是人工明确无额外事实前提，仍需通过其余主体/工程/时间限制。有效性 unknown 可以保留版本，但不能送确认/发布；bounded 必须有截止；no_expiry_confirmed 必须给出核实依据，不把未知解释为永久有效。

source_refs 固定真实 source/原件 version/parse/物理页/evidence/逐字摘录。共用 RAG 的 parse_source_id/original_source_id，根只作祖先，不能把根或另一 parse 冒充定位。历史知识可以明确引用仍有效的历史解析，不因原件重新解析就改写当时证据；来源/祖先撤回、期限或 head 身份变化会使旧知识停止新用，重新发布祖先也不能复活旧知识。新版本与发布事务重新核验完整祖先清单。

候选完善后送确认，成员明确发布。新草稿产生期间旧发布版继续可用；明确发布新版才使旧版本被替代。发现来源错误或适用冲突可立即停用，不等待新版；停用/失效版本不直接恢复发布，应追加经复核的新版本。确认人、时间和依据保留为追加的 decisions/audit。事后发布不会倒灌到发布前的参考范围。

搜索 GET 支持 q、bid_id、purpose、state=published|all、after、limit。指定项目时先按实际主体、地区、工程类型、事实前提、时间、版本、用途和来源祖先筛选，再做文字关键词；无适用内容返回空，不以失效内容补足。未指定项目是目录查阅，不表示已通过某项目的适用性。该 GET 保持明确的原文关键词目录；项目语义检索使用下述独立 knowledge-retrievals 协议。

`source-positions` 接 document_version_id、physical_page、after_ordinal、limit 和可选 parse_id；继续同一解析分页时必须携带 parse_id。服务端返回 authoritative `reference`，可直接供版本编辑选择，超过 20,000 字的建议摘录标 excerpt_truncated，用户仍可按原页明确选择较短摘录；不把截短片段声称为完整解析。索引或解析未就绪如实返回，不伪造来源标识。

## 与 RAG / Worker 的桥接

`bridge.eligible_knowledge_sources(c,bid,purpose,as_of,explicit_version_ids=None)` 是受限 API 控制面的可信端口，返回适用知识 version/source/content_digest、正文及说明、完整 authority manifest、精确 `original_evidence_allowlist`。它先锁目标项目再取图锁/有序祖先 head；必须在短的 read committed 显式事务内调用，autocommit 和其他隔离级别会明确拒绝，释放数据库连接后才进行检索模型计算。事实更正/确认与桥接通过项目锁串行；条件依据来源也在同一图锁内重新核验。普通 Worker 没有 bid_rounds/engineering_projects 读取能力，不借 API/owner 补权限；API 可把这个显式输入冻结为受控 blob/manifest 交 Worker，最终采用/新引用仍由 API `check_reference` 重新计算完整适用性（包括事实条件），不能只比较知识原件清单。Worker 单独验证来源祖先清单不等于已批准项目适用性。

现有 chunks 强制真实 parse/page、原文片段核验及唯一 parse/ordinal。因此不把知识摘要伪装成某页原文，也不把整份跨项目文档全部放进允许集。首条桥接可仅检索 `original_evidence_allowlist` 内的原件证据，命中同时保留知识版本和原件位置；摘要的独立 embedding 使用下述 knowledge_units 协议，不写入原文 chunks。这个普通业务桥接默认且始终拒绝 formal_score，不替代研究分组/时间隔离或完整章节评分。

`bridge.cite` 核目标项目/对象、实际摘录和资格，只登记引用，不创建事实。新引用在项目/图锁后读取 DB clock_timestamp 评价期限，避免等待锁期间过期仍放行。audit 中固定 evaluated_at、当时清单与适用说明；旧引用读取不使用当前内容替换旧版本。已提交/归档/结束的项目不能新增选用，旧引用继续可查。

停用的影响按登记的 citation/lineage 分在办、已提交、归档、已结束/终止，不更改原报告。已登记的 feature/label/model/summary 派生可追踪；未登记谱系的实验/模型明确保留 unknown_ranges，不能宣称已全部查清。具体复核任务由控制面/任务模块接续，不将 UI 点开影响列表当作完成复核。

## 验证与范围

`tests/test_knowledge.py` 验证闭合协议和未知/越权范围；`RUN_KNOWLEDGE_INTEGRATION=1 .venv/bin/python deploy/run.py --env-file .local/backend-checks.env -- .venv/bin/pytest tests/test_knowledge_integration.py -q` 使用真实受限 PG、实际 S3、真实合成 PDF 的物理文本和登录 HTTP，覆盖跨主体、期限边界、来源失效/复活、并发 CAS、逐字出处、历史/原件 SHA、引用重放、事实不自动写入和影响分组。普通 Worker 的缺失能力是实际负例，未新授全表权限。

开发证据冻结到 `/workspace/tender-docs/04-验证与测试/knowledge-development-checks`，不会将 `.py` 快照放回执行包。独立 QA/架构、前端真实体验、跨项目 RAG 长路径与真实公司知识质量分别验收；本模块无付费模型调用、无生产发布、无原件物理清理。


## 企业知识 RAG 实际接线

本节对应独立 RAG 实现候选，D03 历史候选及其验收范围不变。六张追加表的 SQL 在 `packages/knowledge/sql/rag.sql`，仅由 PM 在独立全新 checks 库初始化过；不能从接口存在推断旧运行环境具备这些表或权限。API/Worker 缺少新表或 SELECT 能力时返回 KNOWLEDGE_RAG_NOT_READY，程序不会代建表、加授权或换 owner。正式启用仍需独立 QA/架构、集中候选与部署验收。

主 API 继续注册本模块 router；实际导出在 `knowledge.contracts.extend_openapi` 后调用 `knowledge.rag_contracts.extend_openapi`。严格 schemas 与真实路由分开导出，不改目标冻结协议。人工写入仍需 Cookie、Origin、CSRF、Idempotency-Key。客户端只能用 knowledge_ids 缩小已获资格的条目集合，不能提供 source allowlist、profile 或 formal_score。

| 路径（省略 /api/v1） | 实际业务 |
| --- | --- |
| POST /knowledge/{id}/indexes | body 仅 knowledge_version_id；当前有效发布版 CAS 后排真实索引 operation，202 不代表已 ready |
| GET /knowledge/{id}/indexes | 最近 100 代索引，真实 queued/ready/failed/superseded、完整数量/digest、operation 与错误；失败不借旧 ready 冒称成功 |
| POST /bids/{bid}/knowledge-retrievals | query、purpose、limit1–8、可选 knowledge_ids；按当前身份与事实资格取语义参考 |
| GET /knowledge-retrievals/{trace} | 当时实际提供内容及 evaluated_at 原样保留，单列 current_reasons |
| POST /knowledge-retrievals/{trace}/references | unit_id、逐字 quote、target_kind/target_object_id、applicability_reason；只能引用这次真正提供的单元 |
| GET /knowledge-unit-references/{citation} | 当时单元、出处、说明、上下文与当前失效提醒；不覆盖旧字节 |

`rag_coordinator` 是独立 API 角色的可恢复意图扫描器，`python -m packages.knowledge.rag_coordinator --once --knowledge-id <服务端选定条目ID>` 可精确限定条目，多次传参可处理多个；不传则每轮最多 100 个。自动意图以已发布版本/profile/dictionary/chunk policy 固定幂等，同代失败由明确新手工索引键发起新一代，不无限制造 operation。它只冻结输入并排 Outbox，不跑模型/consumer/relay。元数据 runtime I/O 在连接外。原件尚未有效或知识已撤回的条目不会排新索引。

Worker 引擎注册 handler `knowledge-index/1`、step `index-knowledge`、logical topic `tender.task.document.v1`，对接 `rag_tasks.compute(message,lease)` / `rag_tasks.publish(c,message,outcome)`。compute 先短读不可变输入、关连接，再做实际 tokenizer/lexical/embedding；publish 必须由引擎的当前 lease/fence、step receipt/effect 事务调用，不能用绕过 fence 的独立 daemon 发布。迟到旧代标 superseded，无局部单元或向量成功；不完整数量、模型身份不符、来源变化、发布失败都不能 ready。向量按 PG float32 固定字节摘要并完整读回，重放不能覆盖不同结果。Worker 只拿受控知识/出处输入，不读工程/公司/commands 身份表，不授 API 资格能力。

两类单元严格分离：knowledge_text 是人工批准知识正文的派生参考（offset_basis=knowledge_content、verbatim_original=false）；original_quote 只能在单条批准 excerpt 内再切片（offset_basis=approved_excerpt、verbatim_original=true）。二者有独立 UUID、span、digest、来源节点和完整祖先谱系，不假造 parse/chunk UUID。派生摘要保留 approved_references，不暗示摘要逐字存在于原件。原件 quote 的 reference 固定真实 document_version/parse/page/evidence/source_revision。分块实际 token 目标800/硬限1200/重叠120，每代最多1000单元，超限整代失败；不为填满分块扩展批准摘录之外的同段内容。

真实模型适配器共用受控本地 RAG runtime：BGE-M3 1024 cosine 归一化向量、固定四字段 embedding 身份与词典摘要；向量精确 Top50 + simple FTS Top50 → RRF(k60) → 前20实际 rerank → 最多8。重排不可用时明确 route=reranker_unavailable，并保留真实 RRF 顺序，不补造分数或模型身份；Embedding 不可用503，不返回假命中。每次检索最多500个有资格版本、5000个单元，过大422要求缩小知识条目。当前为精确向量搜索，未宣称大规模 ANN 性能已验收。

检索五阶段：API 短 RC 事务锁项目/来源图并冻结 D03 identity.snapshot confirmed+authority、事实决定/证据基准、有效范围/期限、完整 source heads、ready index 与实际 unit manifest；关连接做 query embedding/lexical；短只读事务取严格 allowlist 候选；关连接实际重排；最终短事务完整重核后才写 Command/trace。网络和模型不跨项目/图锁。模型失败没有未完成 Command 锁住重试。所有新用与重放统一 `assert_trace_scope_current`：检索首次提交、检索同键重放、引用首次提交、引用同键重放都比较完整 frozen scope/index/unit/context/fact basis/source heads，而不是仅问当前还 applicable。旧索引 ready 被新代替、事实依据改后值恢复、同 UUID 重新确认新 authority、来源 head 改后恢复都要求重新查证。保留原 evaluated_at，同时按当前 DB clock 另查期限；unit 自己的状态/有效窗/策略/已知时间/直接祖先关系也参与，不借 parent ready 继承资格。

引用只登记 reference，始终 reference_only=true、project_fact_created=false；不新建事实/规则/评分输入。目标必须在 trace 的实际项目，正文/operation/review 还需真实归属；已提交、归档或非可写项目拒新引用。源撤回后历史 GET 与原件 grant 仍可查，当前提醒不替换当时内容。正式评分、模型训练资格与历史成绩隔离均不由这条普通知识路径授予。

开发验证用 `tests/test_knowledge_rag.py` 与 `tests/test_knowledge_rag_integration.py`。集成明确区分非 owner 真 PG+显式 fixture vectors/内存对象机制、实际本地 BGE/reranker、隔离 Kafka transport、一次原生 OSS exact key 合成原件：RUN_KNOWLEDGE_RAG_INTEGRATION=1，后面三项分别需 RUN_KNOWLEDGE_RAG_MODELS/KAFKA/OSS=1，私有配置由 `deploy/run.py --env-file .local/knowledge_rag-checks.env` 注入。Kafka 测试只创建专用临时 topic/group，复制精确自有已提交 Outbox 消息、真实 engine 领取/发布，重复投递后精确删除自有 topic；不启动全局 relay、既有全 topic consumer，也不把隔离路由当全局部署证明。原件证据不物理清理。所有模型本地、付费调用0；真实公司检索质量、前端整合、规模性能和生产运行另验。
