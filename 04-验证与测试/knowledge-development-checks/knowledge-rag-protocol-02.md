# 企业知识 RAG 实施协议候选 02（KR01 收口，待架构只读审查，尚未执行新 DDL）

本候选补足 D02 的纯关键词检索，保留其发布、适用主体／工程／目的／事实条件／期限、原件定位与撤回语义。它不是正式评分输入，不能将历史工期、业绩、数量自动写成本项目事实。不改变已冻结 D01/D02 结论。协议01原字节保留（SHA256 9f74cf4d724ea5b8378ea03f5b7e9982eede06de819e6328a5ebde72f486633a）；本02收口KR01：统一完整trace资格守卫覆盖所有首次/重放引用，并核unit自身权威，而非只核知识父节点。主记录基线为详细设计的《RAG与评分实现规约》；首批继续 BGE-M3 1024 维、余弦、归一化及现有本地受控模型端口。本协议审查前不创建表、不修改初始化脚本、不热改 release 数据库。

## 1. 文件边界与接线

本 Owner 新增 `packages/knowledge/rag_types.py`、`rag_index.py`、`rag_search.py`、`rag_tasks.py`、`rag_coordinator.py`、`rag_contracts.py`，审查后新增 `packages/knowledge/sql/rag.sql`；实际接口扩在本 Owner 的 `apps/api/knowledge_routes.py`，专属测试 `tests/test_knowledge_rag*.py`，候选与证据置本目录。已有 `packages/rag`、main、export_openapi、Worker engine、初始化器由原 Owner 接线，本 Owner不改。

复用 `packages.rag.core.normalized_vector/fuse_rankings`、`http_port.DenseHttp/LexicalHttp/RerankHttp` 与固定 profile 的 `indexing.ensure_profile`，不复用原文 chunks 表承载摘要。不读取 `.local` 私有密钥，不新调用付费模型。D02 `bridge.check_reference` 是新引用/最终采用的必要入口；不能用向量索引存在或单纯 source_manifest 相等替代它。

需主开发/PM 接入：实际 exporter 调本模块独立 extend_openapi；Worker engine 识别 handler `knowledge-index/1` 与 step `index-knowledge`（topic `tender.task.document.v1`、kind `index-knowledge`），compute/publish 分开；只在审查后的新空库初始化加入新 SQL；可单独运行 API-role 索引协调器。任务消息不含全文，仅索引输入 ID、digest/profile 和通常 fence 字段。

## 2. 两类可检索单元，绝不扩张批准摘录

* `knowledge_text`：已明确发布的 knowledge_versions.content 的片段，属于人工维护/派生参考文本；不能展示成原件逐字引文。无需为本模块付费生成摘要。
* `original_quote`：某一不可变知识版本 source_refs[ref_ordinal].excerpt 的片段。切块只接收这个批准 excerpt；不把其 evidence_id 对应整段、整页、整份原件交 embedding/rerank 或返回给模型。

两类均使用真实 tokenizer offsets，目标 800 token、硬限 1200、重叠 120，片段不跨 envelope。span_start/end 是 Python Unicode code point 半开区间，knowledge_text 相对于 content，original_quote 相对于批准 excerpt；`text == envelope[start:end]`。每单元 digest 为 UTF-8 文本 SHA256，token_count 为实际重新 tokenize 结果。单任务最多 1000 单元；超限拒整份发布并记录可恢复错误，不把部分向量叫 ready。空内容/空 tokenizer 结果同样不假成功。

original_quote 保留原版本、parse、物理页、evidence、批准 excerpt digest 与 ref_ordinal；物理来源由 D02 精确协议重核。批准 excerpt 在原 block 多次出现时不猜原字符坐标：offset_basis 明确为 approved_excerpt，必要时整页定位，bbox=null。返回原文时仍显示原件身份。单元没有伪造的 parse_id/chunk_id；quote 的 parse/page 是单独 provenance，knowledge_text 的这两个字段为空。

每个单元注册独立 source_node(kind=summary，表示从父文本形成的检索派生单元)，父边至少包含本知识版本 source；original_quote 额外包含精确 parse-child source。unit_kind 区分派生知识与原文摘录；source.kind 不授予逐字资格。known_at 不早于各祖先，policy 继承完整祖先中最严格类别，效力始终遍历祖先。旧知识或祖先 head 被改变/撤回后，向量缓存仍可保存但新用拒绝，重新发布祖先不能复活旧 publication_basis。

## 3. 拟新增六张表（字段级草案，审后落约束 DDL）

表全部在 retrieval schema，新表不依赖“原文 parse/chunk”假身份。外键引用已冻结表，新增表之间的组合外键限制 profile/version 混接。

| 表 | 核心列与约束 |
| --- | --- |
| knowledge_indexes | id uuid PK；knowledge_version_id FK；profile_id FK embedding_profiles；operation_id FK operations；input_manifest jsonb；input_digest sha256；chunk_policy_digest/lexical_dictionary_digest sha256；requested_head_revision bigint>0；created_by FK members；created_at。可变结果列 state(queued/ready/failed/superseded)、expected_unit_count、published_unit_count、unit_manifest_digest、error_code、completed_at；ready 要求两 count 相等且>0，完整 manifest digest 非空；UNIQUE(id,knowledge_version_id,profile_id)。input_manifest 冻结完整 content/source_refs/publication_basis、来源权威和模型/切块身份，不来自用户任意 JSON。|
| knowledge_index_heads | (knowledge_version_id,profile_id) PK；revision>0；desired_index_id、ready_index_id（可空）。两 ID 用包含 version/profile 的组合 FK，不能指向别版/别 profile。API 排新任务递增 revision+desired；Worker 只在 desired ID/revision 与冻结值一致时采用 ready，旧 ready 在同版/profile 的新任务期间仍保留。新知识版本自行建 head，绝不沿用旧版。|
| knowledge_units | id uuid PK；index_id FK；ordinal>=0；source_id UNIQUE FK source_nodes；unit_kind 枚举；ref_ordinal original_quote 必须>=0、knowledge_text 必须空；span_start>=0/span_end>start；text_content 非空；text_digest；token_count 1..1200；lexemes text；lex_tsv STORED simple FTS；provenance jsonb（批准定位，不扩段）；UNIQUE(index_id,ordinal)、UNIQUE(index_id,id)。单元/文本不可更新，发布时重算切片/digest/来源映射。unit自身source head只有同一完整发布事务才published；head/有效窗/完整祖先及精确knowledge/parse父关系都纳入独立资格核验，index ready不授予unit资格。|
| knowledge_unit_embeddings | index_id、unit_id、profile_id、embedding vector(1024)，PK(unit_id,profile_id)，FK(index_id,unit_id)→units，FK(index_id,profile_id)→indexes 的相应 UNIQUE；向量有限、非零、归一化范数误差<0.001。不同 profile 不混算。|
| knowledge_retrieval_traces | id uuid PK；bid_id FK；actor_id FK；purpose 白名单(assist/authoring/compliance/history)；query_digest；profile_id；policy_digest；frozen_scope jsonb；scope_digest；selected_units jsonb（各次真正提供的单元与原件摘录）；route/observed_reranker_identity jsonb；created_at。只追加；不存原始 query 于日志，不给普通 Worker 读写该表。|
| knowledge_unit_references | citation_id PK/FK retrieval.citations；trace_id FK；unit_id FK；knowledge_version_id FK；approved_quote text；unit_span_start/end；reference_manifest jsonb；created_at。只追加，仅 API 允许登记。citation.source_id 使用真实 unit.source_id；原文证据以 evidence_id 指向真实证据，chunk_id=null；知识文本可选一条批准原件依据以满足既有引用表约束，但 reference_manifest 明示这是派生知识、该依据不等于这句逐字原文。|

基础索引：units(index_id,ordinal)、GIN(lex_tsv)、embedding(index_id,profile_id)、trace(bid_id,created_at,id)、reference(trace_id/unit_id)。首批在资格允许的小集合内执行 exact vector distance，暂不新增 HNSW 或通过全库近似候选再过滤。允许集超起始容量边界明确要求缩小检索/返回 gap，不扩大范围补召回。

SQL 必须包含 JSON 对象/状态计数检查、组合 UNIQUE/FK、有限/范数校验与新表最小 grant。不可 ALTER 原文 chunks、扩大 formal_score_context、改已冻结目标 SQL。预计 profile_id vector norm 需要的纯 pgvector函数能力沿用已审初始化 grants，无新 SECURITY DEFINER 函数。

## 4. 权限与索引执行

新空库初始化后：API SELECT 新六表；INSERT indexes/heads/traces/references，UPDATE heads 的 desired_index_id/revision（允许首次插入）。Worker SELECT indexes/heads/units/embeddings；INSERT units/embeddings；UPDATE indexes 仅结果列，UPDATE heads 仅 ready_index_id。Worker 对索引 input_manifest/input_digest/version/profile、trace/reference、项目工程/主体表均无新 UPDATE/INSERT/SELECT 特权。unit 来源的 source_nodes/heads/lineage 使用既有受限索引能力；不授 commands SELECT 或 owner。API 另仅能 UPDATE indexes.state/error_code/completed_at：协调器在锁内核操作终态失败且index仍queued时登记失败metadata，不能将其设ready、改count/manifest或向量正文；一般失败通过真实operations状态投影，不伪造ready。API 权限不包含改 unit/embedding/索引结果正文；无 DELETE。旧基线 `GRANT ... ALL TABLES` 不自动覆盖将来创建的新表，必须逐表逐列明示。

API-role 协调器先在锁外获取并校验本地 runtime identity；短 RC 事务按 origin bid→图锁→item/head 顺序读取当前 published version、完整 publication_basis 与原文 refs，拒失效/未知，冻结 input_manifest，写命令/operation/索引输入及 desired revision，一事务入 Outbox。协调器只扫描已发布的有效版本，缺索引补排；失败明确保留操作状态，手工重试须新 generation，不无限重复收费或热下载权重。

普通 Worker prepare 阶段只读取受控输入并关闭连接；tokenizer/lexical/embedding 全在无 DB 连接和图锁时完成。发布处由已有 engine 的 generation/fence/receipt 事务调用：origin bid lock→图锁→有序 source heads→index head；重核当前仍是 published_version、publication_basis 完全一致、runtime identity/profile/dictionary、全部 envelope/ref 身份、各 span/token/vector/digest、expected/published counts，且 desired index/revision 与冻结值一致。任何失败全部回滚，无半 ready；迟到任务不能抢新头。相同输入重放结果必须逐字段/摘要一致，不能用 ON CONFLICT DO NOTHING 掩盖不同正文/向量。

## 5. 搜索分为五个短阶段，模型工作不跨 DB 事务

A. API 验登录/CSRF与 request schema；project 必填，purpose 白名单，不接受 allowed_source_ids/manifest/formal。先只读查询完成的同键命令；重放先走下述 E 的全量资格/头核验。新的请求在短 RC 事务调用 D02 eligible_knowledge_sources，锁后使用数据库当前时刻；冻结适用知识版本、content/publication_basis、ready index/manifest、完整来源图与当前项目主体/region/type/bid/material/settings/fact heads及匹配事实决定基准 digest。最多 500 个 eligible versions、5000个实际允许unit，超限明示 KNOWLEDGE_SCOPE_TOO_LARGE，要求用可选knowledge_ids缩小条目范围。knowledge_ids只作server eligible集合的交集，不能新增来源/绕过资格。各unit必须自身head已published、当前有效、known_at非未来且祖先完整有效，并精确包含该知识source与original_quote对应parse-child的父关系；其权威清单及unit manifest一并冻结，candidate/withdrawn unit即使index ready也排除。事务结束关闭连接。freeze 结果只由服务端构造。

B. 本地 query tokenize/lexical/embedding，无数据库连接；固定同 profile/词典，1024 维有限值归一化。缺 runtime 诚实 503，不伪造语义命中。查无 eligible 或无完整 ready index 时可以直接返回 empty/partial+明确 gap，不拿未经批准资料凑满。

C. 再开短只读事务，在冻结 allowed index/unit 集合内执行 dense exact top50、PG FTS top50，RRF k=60 前20，读取被供给文本；关闭连接。原件候选文本来自 original_quote.unit.text_content，始终只在批准摘录之内。

D. 本地 rerank(query,候选单元) 在无连接时运行；严格校验 scores 个数和有限值，记录实际 reranker identity。不可用显式退到 RRF并在 route/gap 标记。最终≤8条，不调用生成模型。返回不足保留不足，不扩大证据包。

E. 新 RC 事务通过下述统一 assert_trace_scope_current 守卫，锁 target bid→图锁→有序源头，在新的 DB clock 时间重新调用 D02 check_reference（每一已选知识版本），同时比较冻结的上下文/事实决定依据与全 eligible/version/index/unit/source scope。输入/事实确认/head/期限有变返回 KNOWLEDGE_SCOPE_CHANGED 409，客户端重新查；不能只重新签字旧 manifest。再重核每个 unit 及原文 provenance 仍属于所冻结版本、ready index、批准 envelope；追加 trace 与完成的幂等 Command，事务提交后返回。模型期间不持有未完成 Command 的锁；并发同键可能重复本地 CPU 计算，但最后 Command 唯一，败者只在重新核验首次响应仍有效后重放。失败/崩溃前未提交 Command，重试可恢复，没有悬挂“操作进行中”。不产生付费重复调用。

统一端口 `assert_trace_scope_current(c,stored_scope,bid,purpose)` 用于四条路径：首次检索结果发布、检索同键重放、从trace首次引用、引用同键重放。拒autocommit/非RC；锁 target bid→图→有序unit及原来源祖先heads，核trace实际绑定bid/purpose（客户端不可换项目），重新计算D资格并比较原冻结 eligible集合、知识版本/content/publication_basis、ready index IDs、index input/unit manifest digest、每一个当时允许/供给unit的kind/ref/span/text digest/source身份、unit自身head/status/有效窗/known_at/完整祖先清单及精确knowledge/parse父关系。原冻结当前主体/region/type/phase/archived/bid/material/settings/fact head及所有匹配fact IDs/decision head revision/decision_basis digest也必须相等；D check_reference当前仍applicable是必要但非充分条件。

同一知识版本I1被I2重索引替换ready，即使knowledge source未改，也拒旧trace新引用；事实基准改变后值恢复相同且重新confirmed，也因原事实/确认基准身份不同而拒旧trace新引用。candidate/withdrawn/过期unit不可检索或引用，不能借index ready绕过。所有重放使用原trace完整scope与实际供给，不重新计算新的digest给旧命中授权。评估时刻不参与“当前等价”比较，但期限以当前DB clock单独核验；原trace evaluated_at保留不改。历史GET仅显示失效告警，保持原文字与证据，不调用新用端口制造授权。

## 6. 实际端点与引用输出（新增 actual，目标契约保持历史）

* POST `/bids/{bid_id}/knowledge-retrievals`：`{query:1..10000字,purpose:白名单,limit:1..8,knowledge_ids?:最多100个条目UUID（仅缩小）}`。登录/Origin/CSRF/Idempotency-Key；响应 `{trace_id,profile_id,policy_digest,coverage:partial|unavailable,hits,gaps,route}`。不宣称全面阅读。
* hit：`{unit_id,unit_kind,knowledge_id,knowledge_version_id,knowledge_source_id,unit_source_id,index_id,text,span_start,span_end,offset_basis,rank,reference_only:true,project_fact_created:false,provenance}`。knowledge_text provenance 是批准依据列表，明确 derived；original_quote 是一个批准的 parse/page/evidence/ref/excerpt（都是真身份），没有伪 chunk_id。供给标识是 unit_id/trace_id，不能作为已应用引用 ACK。
* GET `/knowledge-retrievals/{trace_id}`：历史只读显示当时实际供给内容、profile/route/原件清单与当前适用性告警；不将旧记录标成新用已授权。
* POST `/knowledge-retrievals/{trace_id}/references`：只允许本次实际提供 unit_id、逐字子 quote、target_kind/target_id/applicability_reason；API 必须先调用与检索最终提交相同的 assert_trace_scope_current（完整原scope/index/unit/事实基准，不仅check_reference），然后再核当前项目可写+真实对象归属+quote在该供给unit内；引用同键重放也先走同一完整守卫。生成真实 retrieval.citations 与 unit_references，source_id=unit source，evidence_id=已批准原件证据，chunk_id=null。知识文本与原文摘录分别标 derived/original，不能用摘要结论伪装原页逐字引文。引用同键重放再次核效力。
* GET `/knowledge-unit-references/{citation_id}`：准确还原当时 quote、unit文本、span/provenance/知识版本、原件定位与当前失效告警。既有影响查询遍历知识 source 的派生 unit→citation，可继续按在办/已提交/归档/结束分组；不改旧报告。原 D02 `/knowledge-references` 不冒充能解新 unit 协议。
* GET `/knowledge/{id}/indexes`、POST `/knowledge/{id}/indexes`（后者仅成员明确重试当前发布版，受CSRF/幂等）：读实际 ready/queued/failed/superseded profile/operation/count/gaps，或排新 generation。无模板商店/伪进度，无公开任意 source/profile 输入。

引用只登记参考，不调用事实写入口、不写 rule_set/正式评分/研究资格。自动索引不会将候选来源变 published；无索引或摘要质量不足诚实可见。前端先仍可用 D02 原关键词维护目录，语义入口待 actual 与实链验收后启用。

## 7. 验收清单与停止条件

审查后只用独立新库建立新表与最小 LOGIN，绝不给现有 release DB 运行增量 DDL。测试证明 Worker 不读主体工程表、不改可信输入/trace，API 不改向量正文；新 FK/profile/count/向量检查真 PG 拒绝非法数据。执行真实 Kafka fence/旧 generation 迟到/并发新索引/重放内容差异，以及中断后完整重试、不将半索引设 ready。

首批必须包括：摘要与quote身份分离；批准短语所在原段含禁用尾句时，embedding/rerank/命中/引用均不包含尾句；两个ref同文不同原件不串页；旧parse/祖先撤回/重新发布不复活；别公司、范围不明、过期、无confirmed事实、确认basis变更、同值异主体/单位/类型均拒；模型期间改变事实/撤回源使最终采用及同键重放拒；同版重索引I1→I2、事实基准变更后同值恢复均使旧trace首次/同键引用拒；unit自身candidate/withdrawn/到期或父关系不符即使index ready也拒；模型阶段 instrument 断言无活动连接/图锁；dense/FTS/RRF/rerank与profile错配/无模型/重排降级可追踪；历史quote与原件字节保持可还原；形式用途/allowed manifest 客户端注入拒。

用合成向量可验证 SQL/范围/权限，必须明确标机制夹具；再用现有实际 BGE 1024/中文词法/重排做小规模合成领域文本检索并保留真实 runtime identity/weights SHA/trace，不能将其称真实公司内容质量验收。无新付费模型调用，无 Worker 提权，无外部发布。完整公司知识效果、召回金标、生产容量、扫描/表格理解仍需独立产品与用户验收。
