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

转换只处理服务端自己生成的无外部关系 OOXML。子进程只接收白名单运行变量，不继承数据库、模型或 OSS 密钥；每次独立工作目录、超时杀整个进程组、输出大小上限。当前宿主机不能创建 bwrap 网络命名空间，故开发实际 PDF 会带 `RENDER_SANDBOX_NOT_ATTESTED`，不宣称已满足生产非 root、无网、只读根文件系统、资源配额容器要求。生产部署需另行提供受控 wrapper 并独立验收。存在真实占位符时保留正文并在各格式 gap 明示 `PLACEHOLDER_CONTENT_PRESENT`。

## 开发验证

`RUN_AUTHORING_RENDER=1 .venv/bin/pytest tests/test_authoring.py -q` 使用真实本机 LibreOffice 和中文字体；仅检查当前已支持子集的实际 Word ZIP 和 PDF 中文/英文文本，不代表所有 Word 格式保真。

`RUN_AUTHORING_INTEGRATION=1 .venv/bin/python deploy/run.py --env-file .local/backend-checks.env -- .venv/bin/pytest tests/test_authoring_integration.py -q` 使用真实 PG 非 owner API/Worker 和原开发 S3，验证并发 CAS、幂等、快照、非法来源、预览后祖先撤回、事实变更、受控生成端口、实际登录 HTTP 保存/恢复/受理构建及 Word/PDF 分别登记。该专用数据库不启动 Kafka 消费者，不与开发环境争抢同一 broker 的消费组。团队独立 QA 还需在专用 broker 和真实 HTTP/Worker 上跑完整长路径。

没有调用收费模型、没有处理用户私有标书、没有修改旧工程或冻结 SQL，没有承诺生产能力已通过。

验证候选、真实合成产物和 JUnit 证据在 `/workspace/tender-docs/04-验证与测试/authoring-development-checks`。本包 `qa/archive-map.json` 记录历史候选 02 快照和证据的归档路径及迁移前后 SHA；历史候选 JSON 原字节保留，不把旧观察改成新实现已通过。
