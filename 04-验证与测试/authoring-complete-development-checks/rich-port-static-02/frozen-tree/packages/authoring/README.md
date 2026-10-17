# 章节编制与正文构建实现

这是新工程的实际模块，目标主记录仍在 tender-docs。它提供可持久保存和恢复的正文草稿、来源有守卫的建议预览及应用、固定快照的真实 Word/PDF 构建。独立前端、Kafka 长路径及生产渲染容器仍由相应团队接线验收，不能把这里的模块测试当作完整 A2 验收。

## API 与内容

主 API 注册 `apps.api.authoring.router`，实际 OpenAPI 生成器在全部目标定义复制之后调用 `packages.authoring.contracts.extend_openapi(actual)`。目标已有路由为创建草稿、读取草稿、PATCH 保存、读取章节、创建构建及读取构建；新增实际路由还提供能力查询、草稿列表、添加章节、历史修订游标、恢复旧修订、草稿快照、结构化建议预览与确认应用。新增接口由实际契约定义，不改冻结目标文件。

先读 `GET /api/v1/authoring/capabilities` 取得 `schema_digest` 和内置模板 ID。正文为严格 ProseMirror `doc`：根 `attrs` 有 `title, ordinal, rule_ids, source_refs`；内容支持段落、标题、粗体、斜体、换行、原生 Word 有序/无序列表。非文本节点须有唯一、稳定的 `attrs.id`。图片、表格、外链、任意 HTML、Word 原件无损往返不属于当前接收范围，服务端直接拒绝相应结构。实际 OpenAPI 描述父子节点白名单。

保存遵循目标 `SaveDraft`：`base_draft_revision`、`changes[{section_id,base_section_revision,schema_digest,content}]`，并同时携带强 `If-Match` 与 `Idempotency-Key`。先锁 bid，再锁 draft/section。批量变化整体提交；同一章节重复、重复目录 ordinal、失效要求、非法来源等会整体回滚。响应 `DraftSaved` 含已落库快照，后续构建使用这个确认修订。恢复旧章节也会追加新修订，不改写原有内容；失效的旧引用不能借恢复再次应用。列表有稳定游标，不依赖前端缓存找历史。

## 引用与建议

引用含 `source_id, source_revision, evidence_id, document_version_id`。服务端检查真实 evidence→page→parse→document→bid，并通过 RAG 共用的 `parse_source_id` 严格绑定当前解析子来源，原件根只作祖先；旧解析子来源不能挂最新解析证据。来源须属于当前资料集合/当前正文、最新解析、全部祖先 head 已发布且在有效窗口、事前内容用途。当前编制端仅接已在本项目资料中的 document 来源及 document 祖先；知识适用性服务接入之前不把仅有 published 标志的企业知识放行。来源图使用既有图互斥锁和按 ID 排序的祖先 head 行锁，撤回先提交则本次应用失败。引用登记用途为 `authoring`，不会升级正式评分资格。

预览持久化稳定节点 ID、节点及整章 before 摘要、基础章节/草稿修订、规则与事实 head 身份、精确来源版本。应用会重新查权威来源及上下文，只追加修订；过期、已消费、上下文变更或范围漂移明确失败。预览/应用不把问题标记为已复检解决。

受控模型接线端口是 `generation.freeze_generation_plan(c,bid,ProposalRequest)` 和 `accept_generated_proposal(c,bid,writing_plan_id,generated)`。前者冻结 writing_plans 与可提供证据，只从唯一的当前解析子来源取内容，按唯一 evidence 计 500 块限额，原件根和旧解析不会重复扩充提示。超过范围明确拒绝、不会静默截断；后者只接受冻结证据内的引用并创建待确认提案。它们不执行任何供应商调用或重试。主开发须通过既有 operation / model_calls / 预算账本执行结构化生成，之后调用接收端口；本目录没有把测试建议冒充真实模型输出。当前 export 不依赖 Worker 读取 writing_plans；未来模型执行可由 API 冻结受控 blob/manifest 显式提供输入，无需泛授 Worker 全表能力。

## Worker 构建接线

1. API 在数据库事务外执行 `configured_render_identity(formats)`；`freeze_build(c,bid,BuildRequest,authenticated_actor,renderer_identity)` 校验已保存修订与来源并固化 build_inputs。模板、正文快照、生成器源摘要、实际字体摘要、LibreOffice 版本/启动器摘要和选项分别固定。
2. API 使用 `enqueue_plan`，`handler_version=export/1`、`step_key=build-document`、`topic=tender.task.export.v1`；`payload_ref` 为 build input ID。不能发到 document topic 冒充导出任务。
3. Worker 在短只读事务中 `prepare_build(c,bid,input_id)`，退出连接后 `compute_prepared_build(prepared)`。后者生成真实 OOXML ZIP，LibreOffice 实际转换 PDF，PDFium 校验页数；包括转换器返回 0 但产物截断所引起的具体 `PdfiumError`，均只记该 PDF 格式失败，DOCX 独立保留。对象写入现有私有存储并回读 SHA。网络/渲染阶段没有数据库行锁。
4. 在 engine.finish 已核验 fence/generation 的事务中调用 `publish_build(c,bid,build_id,computed)`。发起人由 API 冻结 manifest 提供，普通 Worker 不需要 commands SELECT、更不借 API/owner。产物登记为 `bid_body`，每种格式独立 build_outputs、document_versions、逻辑文件/版本。完整转换链入审计及返回值，Worker还应将返回值写 effect_receipt。Word/PDF 成功时关系是对应实际字节的 `controlled_render`；各格式失败分别显示，Word成功不代表PDF或对应检查成功。
5. 此模块不移动当前正文或评审指针。构建输入的 `follow_up_context` 固定当时的资料、正文、草稿、规则、事实及 bid/material/settings revision；`enabled` 仅由本次明确的 `follow_up=review` 决定，项目自动开关不能将 `none` 升级。API 控制面须比较冻结上下文与当前头，并额外要求构建 draft 就是当前 draft、修订未变，再以成功的冻结 PDF 调用统一 RequestReview。历史构建没有这个上下文、历史 draft 构建或任何相关头/编辑已改变时保留产物，等待用户明确选稿，不让旧构建覆盖后续编辑。普通用户仍通过人工完成正式决定和实际提交。

GET `/builds/{id}` 现在以同一只读快照返回产物与 `follow_up`，复用 API 控制面的 `packages.domain.build_followups.follow_up_view`，不会在读取时采用正文。`requested` 仅来自本次明确请求；state 为 not_requested/pending/adopted/needs_selection/failed。adopted 仅表示成功 PDF 已采用为正文、解析与统一核对请求已原子登记，不能展示成“核对完成”。adopted_document_version_id、parse_operation_id、request_id 是真实登记身份；review_id 只有实际 review.frozen 审计与 review_runs 同时存在才非空。构建格式成功与后续采用失败是两个独立结果，均应向用户保留。实际 BuildView 增加严格必填 BuildFollowUpView；BuildPage 应复用同一 helper/形状。none 始终 not_requested，其他状态不能携带虚构 review_id；历史缺可信上下文明确 needs_selection。

旧文本构建使用 `packages.word_intake.renderer` 的受控转换端口及其固定 profile；双模板文本构建使用下述独立的可信生成 PAGE 端口，复用精确 Word03 沙箱。旧宿主机 `soffice` 包装器不再由 Worker 路径调用。配置缺失、旧协议/profile、转换失败均保留真实 DOCX 与 PDF 独立失败。外来 Word 安全端口仍由解析 Owner 审验，本批不得将已阻断的 Word01/02 作为安全依赖。转换 coverage 只有 complete 才将 Word/PDF 关系登记 verified；partial/failed 的字节、缺口与对应资格分别保留。正文存在真实占位符时，格式 gap 明示 `PLACEHOLDER_CONTENT_PRESENT`，不把系统提示写入正文。

## 开发验证

`RUN_AUTHORING_RENDER=1 .venv/bin/pytest tests/test_authoring.py -q` 使用真实本机 LibreOffice 和中文字体；仅检查当前已支持子集的实际 Word ZIP 和 PDF 中文/英文文本，不代表所有 Word 格式保真。

`RUN_AUTHORING_INTEGRATION=1 .venv/bin/python deploy/run.py --env-file .local/backend-checks.env -- .venv/bin/pytest tests/test_authoring_integration.py -q` 使用真实 PG 非 owner API/Worker 和原开发 S3，验证并发 CAS、幂等、快照、非法来源、预览后祖先撤回、事实变更、受控生成端口、实际登录 HTTP 保存/恢复/受理构建及 Word/PDF 分别登记。该专用数据库不启动 Kafka 消费者，不与开发环境争抢同一 broker 的消费组。团队独立 QA 还需在专用 broker 和真实 HTTP/Worker 上跑完整长路径。

没有调用收费模型、没有处理用户私有标书、没有修改旧工程或冻结 SQL，没有承诺生产能力已通过。

验证候选、真实合成产物和 JUnit 证据在 `/workspace/tender-docs/04-验证与测试/authoring-development-checks`。本包 `qa/archive-map.json` 记录历史候选 02 快照和证据的归档路径及迁移前后 SHA；历史候选 JSON 原字节保留，不把旧观察改成新实现已通过。

## 完整 A2 第一切片：树、计划、双模板（新增独立 router）

集成需另外注册 `apps.api.authoring_structure.router`，实际契约在旧 `contracts.extend_openapi` 之后调用 `structure_contracts.extend_openapi`；不修改目标契约。须仅在包含已审 `sql/structure.sql` 的全新数据库使用。当前独立 fresh 为 authoring_complete01；普通 Worker 没有这五张新表的读取或写入权，全部树/计划/双模板实际材料随原有 core draft_snapshots 完整冻结。

- `GET /bids/{bid}/drafts/{draft}/structure` 返回当前完整树（含删除墓碑）、精确正文修订/来源/计划、目录与版式版本、当前快照/结构版本、计划页预算和未知实际页范围。草稿 ETag 与既有人工保存相同。
- `POST .../structure/preview` 接 `AuthoringTreeChangeRequest`；支持 add/move/rename/set_type/delete/restore/copy。parent 是真实 section_id、同批前序 client_key 或 null。预览返回真实新增身份、前后树、删除影响；不修改正文。复制重新分配所有章节与 PM 节点 ID，不继承计划 ready 或人工决定。
- `POST /bids/{bid}/authoring-structure-actions/{action}/resolve` 接 confirm/reject。确认要求同一草稿 ETag，重新核对版本、上下文和来源祖先；同一 Action 跨成员/跨命令只有一个 receipt。拒绝/旧 receipt/历史读取在项目结束后仍允许，新确认拒绝。
- `PUT .../sections/{section}/plan` 接 `AuthoringPlanSave`。同时 CAS 草稿与当前计划版本，追加不可变计划和草稿快照；state 是人工编写状态，不等同核对通过，pending_facts 永不变为确认事实。页预算为计划值。requirement 依据须当前有据确认且 refs 对应该要求；template 依据须精确匹配当前目录项的明确页预算。手工预算不伪称来自要求。
- `GET /authoring/templates` 与 `/authoring/template-versions/{version}` 返回真实模板；`POST /authoring/template-families` 建新族，`POST /authoring/template-families/{family}/versions` 用独立强 ETag/CAS 追加。目录 outline 与版式 layout 分开不可变，内置族不可改，另存企业族。
- `POST .../directory-template/preview` 提供显式 matches 与明确删除项；同族 key 稳定复用章节 ID，未匹配人工内容保留，已人工改名不会被模板静默覆盖。确认复用上述 Action。
- `POST .../style-template/preview` 用严格本地字体/A4/边距等配置，手工已确认要求的有据字段优先于企业模板；冲突报告明确 project_requirement_wins。未判明条款范围仍列出，不冒称语义自动检查完成。有当前要求时先返回 requires_format_review 和 review_digest，第二次携带完全一致 format_review_ack 才生成候选。
- `GET .../structure/history` 提供服务端游标历史；`POST .../structure/restore-preview` 选择真实 structure_version_id，先比较后恢复成新修订，后来章节保留可恢复墓碑，不改写旧快照。

所有新写接口仍须 CSRF、Idempotency-Key；草稿相关须强 If-Match。旧人工保存/建议/生成入口仍可使用，但不得改已删除章，不得通过正文 ordinal 旁路树顺序。正文内容仍为原严格文本 PM 子集，图片/表格/Word 内容导入另切片，不能把 Word 接收预检文字当无损导入。

结构草稿构建必须显式提交 `AuthoringStructuredBuildRequest` 中目录和版式两个版本，且都与已保存 draft_revision 快照相等。manifest 冻结完整树/计划/两个模板/格式证明，Worker 从原 core 快照读；发布再次检查正文、计划和格式依据。实际 DOCX 支持层级标题、静态目录、字体字号、边距、封面、页眉页脚文本及列表/强调。可信生成正文的页脚页码由下述独立端口实际转换并逐页核验；未配置或未完整核验时明确 `PAGE_CORRESPONDENCE_UNVERIFIED`。实际章节页范围明确 unknown/null，绝不将计划页数冒为实测。PDF 产物与 Word 完整对应资格分别显示。该切片不是完整 A2 通过。


## 可信生成正文 PAGE 专用转换

双模板构建由服务器冻结 `writer_kind=generated_text/2` 与精确生成器摘要；客户端不能提交或切换转换权限。`generated_docx` 仅生成一个规范页脚 PAGE 字段，`generated_verification` 从完整冻结快照重新生成并逐字节比对，再检查全部 OOXML 部件、关系、标签和属性白名单。动态目录、外链、复杂域、任意 Word 包或缓存字样不能借此入口取得资格。外来 Word 仍沿 Word03 原有规则拒绝全部动态域。

运维通过私有 `AUTHORING_GENERATED_RENDER_PROFILE_PATH` 安装 `generated_renderer.profile_for(approved_word03_base)` 的精确配置；缺失时保留真实 DOCX，PDF 独立失败，不回退宿主机转换。配置绑定受控 Docker、镜像、字体、选项、Word03 三个已审文件、生成器与验证器源码；移动固定运行根后需重新生成对应安装配置，不能借修改摘要绕过核验。转换在数据库连接关闭后进行；只复用已审网络禁用、非 root、限额沙箱。

PDFium 按真实每页几何分别校验页眉、页脚和物理页码 1..N，比较完整正文文本摘要，保留正文中的所有数字及原生列表序号。缺字、边界重叠、对应不完整为 partial，超时或无有效 PDF 为 failed；只有完整对应才登记 verified。静态目录的章节页码和各章实测页范围仍未知，`TOC_PAGE_NUMBERS_UNKNOWN` / `ACTUAL_SECTION_PAGE_RANGES_UNKNOWN` 不会由页脚 PAGE 成功消除。页预算仍为计划。

`RUN_AUTHORING_GENERATED_INTEGRATION=1` 的专属测试仅在独立 authoring_complete01 非 owner 数据库与明确批准的本地 Docker 执行：真实 export/1 engine、ASGI 取消、迟到 fence、编辑抢头保护及最终来源撤回。对象存储为显式内存端口；requests/httpx 真实 HTTP 和模型供应商均硬阻断，未启动 Kafka 或收费模型。这些机制测试不代表真实 OSS/Kafka/前端或全部 A2 验收。真实多页产物与首失败原样保存在 authoring-complete-development-checks；动态字段规则和正文数字不靠测试适配器放宽。
