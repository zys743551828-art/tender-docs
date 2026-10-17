# 助手与编制执行实现

本模块把持久讨论、受控模型任务和真实领域写入分开。项目助手使用已有项目主会话及 core operations/model_calls；全局助手使用真实空 bid 的独立会话、run、调用账本、Outbox 和事件，不创建假项目。聊天、回答、建议及派生内容均非正式评分输入。

## 实际接口

所有路径以 `/api/v1` 开头，实际字段及封闭校验见 `protocol.py` / `actual.py`，由原 `contracts.extend_openapi` 集中导出。实际路由注册决定接口范围，不以目标契约冒充已部署。

- 现有项目主会话及消息 6 个操作保留；`GET /bids/{bid}/conversations/main` 返回同一主会话。新增 `POST /bids/{bid}/conversations/{conversation}/turns` 接受 `AssistantTurnRequest` 并返回 202 `AssistantJob`。
- `POST /assistant/global-conversations/main {}` 创建或恢复同一成员的全局主会话记录，GET 读取；关闭/隐藏需单独 `/state` 明确变更，不因来消息或完成任务自动恢复。关闭不等于取消任务，也不等于关闭项目。
- `GET /assistant/conversations?scope=project&bid_id=...` 或 `scope=global` 发现实际会话；全局记录、消息、任务只对创建成员可读。
- `GET /assistant/global-conversations/{id}/messages?before=...` 持久分页；`/events?after=N` 真实递增序列游标，不伪造已执行或完成回答事件。
- `GET /assistant/sources?kind=project|document|section|knowledge_unit&bid_id=...&trace_id=...&physical_page=N` 返回可选择的可信 `reference` 和不可选原因。`@` 应绑定此对象，不让用户填 UUID、不从文字猜目标。知识必须绑定真实检索 trace 中供给的 unit；原页可单独选择。项目助手拒绝其他项目的直接原件，全局可明确选择项目和资料，但只有明确选择的项目才是可提议写入的目标。
- 新 turn 必须提交当前会话强 ETag / CSRF / Idempotency-Key，正文含 `client_message_id,text,source_selection`；可有明确当前 `section_id` 和 `max_tokens`（默认 2048，上限 4096）。`source_selection` 必须使用原 `reference` 的 version 摘要；引用变动返回 412，保留用户输入。相同消息身份重放返回原任务，不自动再次调用；相同未知调用改 nonce 也拒绝新用。
- `GET /assistant/jobs/{id}` / `GET /assistant/jobs?scope=...&bid_id=...` 返回真实 operation、调用/计费状态、候选及 API 发布状态。受理、模型成功、消息发布、领域执行是不同状态。`POST .../{id}/cancel {}` 先返回取消请求，直到 Worker 实际取消才显示 terminal。
- 回答的 citations 保留具体来源版本/解析/物理页/证据，知识派生摘要与原件 quote 明确区分。原页 quote 必须完全位于真实 evidence 文本，服务端定位包装不是可引用原文；失败或缺失原页不能提供给模型。问题建议是 `assistant_suggestion`，有独立稳定 ID，不能当作正式 finding 或已解决问题。
- `GET /assistant/actions/{action}` 读取真实预览和强 ETag；`POST .../resolve {decision:"confirm"|"reject"}` 只接受明确按钮。工具仅服务端白名单 `review.request` / `authoring.generate` / `knowledge.candidate` / 原生成补丁的 `authoring.apply_patch`。普通“好的”文字没有执行权限。

## Action 与生成

Action 的目标、参数与预览由 API 从冻结可信上下文构造；模型不能传任意领域请求或任意 UUID。确认时在同事务重核当前来源/祖先、项目主体、事实依据、结构与生命周期，才调用实际领域服务。跨成员、跨入口仅全局一次消费；拒绝、过期、stale、失败均如实保留。聊天关闭/隐藏不影响其他成员看到项目 Action；全局私有讨论正文不对其他成员开放。

`POST /bids/{bid}/authoring/generations` 使用 draft 强 ETag，固定 `section_id,base_section_revision,instruction,source_version_ids`。选区模式还必须同时固定 `node_id,selection_start,selection_end`；位置按 Python Unicode 字符索引（前端须转换 UTF-16 偏移），不得直接拿 DOM UTF-16 偏移提交。服务端只把模型返回的替换文字拼入冻结节点，保留选区外文本/格式和稳定节点 ID。不同选区是不同 intent。

生成复用 `freeze_generation_plan → model_calls(purpose=authoring) → accept_generated_proposal`，额外冻结已确认事实正文、主体权威及结构记忆。实际原件 evidence allowlist 限制引用；不把历史事实自动复制为当前工程事实。成功只产原 A2 同一个 Action，预览来源 attrs 与实际应用一致。确认才追加 draft/section revision；原 A2 native apply 与助手入口检查相同生成上下文及同一 action_consumptions，拒绝后不能换入口应用。不引入第二张确认卡或第二次模型调用。

结构记忆每类最多展示 100 项，并明确 `coverage=partial`、缺项和完整摘要；未确认、待定、驳回、依据失效的事实不进入可信事实。旧 finding 标出所属正文/资料批次，不能宣称新稿已重新评审。归档/已关闭项目可以只读问答，写工具与生成拒绝。

## 执行与权限

项目普通 Worker hooks：`tasks.compute(message,lease)` / `tasks.publish(c,message,outcome)`。处理器 `assistant/1` 对应 step `assistant-answer`，`authoring-generate/1` 对应 step `authoring-generate`；topic 为 `tender.task.assistant.v1`。`payload_ref=assistant_jobs.id`，任务 input_digest 为 `sha({id:job.id,digest:job.input_digest})`。主后端集中注册 engine/main；本目录不改共享入口。

全局 `global_runtime` 为独立单次 run；reserve 顺序是逻辑 intent/profile advisory 锁 → run/fence/租约 → 全局 unresolved 检查 → 唯一账本。单 run 仅一次预留；unknown/reserved/失败不会自动重发。取消或失租后的迟到响应仍保留实际费用，但不产生可用候选。模型响应 blob 摘要、实际账本和输出内容精确绑定。

数据库短事务关闭后才做模型/对象网络；调用前重核 Worker 可读来源头，结果采用时 API 重新读取完整业务资格。Worker 不获得公司/工程/commands/聊天/Action/发布或预算输入写权限。`coordinator` 使用 API 角色，仅锁真实 bid、共享来源图、可更新的全局会话及专用 publication advisory 锁，不对 append-only jobs/outputs/publications FOR UPDATE；不做模型 I/O。

SQL `sql/assistant.sql` 只用于已静审后的全新隔离初始化，运行时不修表/授权。缺少能力明确 503。本候选未热改旧库。

进程入口（均显式启动；本次机制验收未启动真实 consumer）：

```text
python -m packages.assistant.coordinator --once
python -m packages.assistant.transport relay --once
python -m packages.assistant.transport worker --once
python -m packages.assistant.transport recover
```

global relay/consumer 只使用 `tender.task.assistant-global.v1`，Worker 配置单独 `ASSISTANT_GLOBAL_CONSUMER_GROUP`；project 复用主执行器。全局 relay 有 Outbox claim/ack，恢复只重新投递过期任务，由原账本决定是否可调用，不授权新的 paid attempt。不得拿 checks DB 启动旧 release 全 topic 消费者。

## 验证边界

`tests/test_assistant.py` 为纯校验；`RUN_ASSISTANT_INTEGRATION=1` 才使用 PM fresh assistant01 非 owner PG + ASGI + 显式 `httpx.MockTransport`，对象端口为内存夹具。模拟返回不是模型效果；本批不宣称真实 Kafka、OSS、付费模型、企业准确率、前端或完整产品验收。旧 work-conversations 5 项在新 actual 范围下回归。具体最终 source/dependency 原始字节及验证结果以 docs 中独立候选为准。
