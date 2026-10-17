# 助手运行与动作：具体协议01（待静审，不执行DDL）

写范围 packages/assistant、apps/api/assistant_routes.py、自有测试/SQL及 authoring generation 接线；主后端统一 main/exporter/engine/shared model_calls。产品依据 remaining-work-protocols《助手生命周期任务与Word实施交接》第1/2/5。项目会话沿PM工作会话01原对象、消息、状态与主会话ID，不新造副本。关闭/隐藏与项目phase独立；关闭不取消在途任务。global无bid，不改core ops非空约束。

## 具体存储与权限

SQL packages/assistant/sql/assistant.sql 新10表，必须 reviewed base roles.sql 之后仅fresh初始化：

| 表 | API | 普通Worker |
| --- | --- | --- |
| assistant_global_conversations | 真实成员私有主会话，SELECT/指定创建列/状态修订游标列 | 无 |
| assistant_global_messages | SELECT/INSERT，追加不改旧消息，global会话＋成员组合FK | 无 |
| assistant_jobs | API INSERT不可变受控输入，project answer/authoring绑定真实core op；global answer有明确null bid、无core op | 仅SELECT，不改输入/actor/来源 |
| assistant_global_runs | INSERT受控id/会话/actor/command/input/max_tokens列；仅UPDATE cancel_requested_at | SELECT及state/fence/owner/lease/终态列UPDATE；不能INSERT/改输入/预算/actor |
| assistant_global_calls | SELECT | 每run唯一一行预留，INSERT仅身份/预算列；UPDATE账本结果/usage/response，不能改身份/预算 |
| assistant_global_outbox | INSERT仅受控消息列，SELECT | SELECT、仅claim/lease/ack/次数列UPDATE |
| assistant_global_events | SELECT/INSERT追加持久序号事件 | 无 |
| assistant_outputs | SELECT，无INSERT/UPDATE | INSERT已核schema输出，追加不可变；两种账本FK正好选一 |
| assistant_publications | API最终重核后INSERT一次消息/Action引用或stale/failed，追加不改 | 无 |
| assistant_action_links | API INSERT链接列（默认pending）；终态/修订列UPDATE有有限状态trigger | 无 |

所有新表REVOKE PUBLIC及能力角色旧宽授权后精确grant，不新增Worker工程/公司/commands/会话/正式决定写能力。不声明所有跨表关系已由FK证明：project既有messages和model_calls无对应组合UNIQUE，故API/Worker各自必须检查project message确属于同会话/bid、call.operation_id精确对应job；publication同样检查消息与job对应。global新增表能组合FK的均组合绑定。Action真实target_bid从既有proposed_actions.bid_id唯一读取，不再冗余一个可不一致bid字段。

## 消息、来源与记忆

全局私有主会话按成员服务端确定稳定ID；global消息/事件bid永远null，不造假工程。项目主会话共享成员原记录。统一发起turn请求含client_message_id、text、server source refs与当前章节；旧仅记录消息入口仍可用但诚实无模型操作。新turn重用同一project messages记录，不重新存一份项目聊天。消息client ID与创建人、内容/来源精确冲突判409；同一消息已有job时同键/新键重放均返回同一真实操作，禁止重复计费。状态不active拒新发消息，但此前已排任务可完成并在恢复后读到。

SourceRef={kind,id,version,trace_id?}只由来源选择器返回；version是服务端当前完整对象/来源头摘要，不让用户填UUID。project只能冻结该明确项目的结构记忆，不扫描其所有历史原件；document只冻结所选版本真实解析/证据，预算超限明确拒绝不默截；section固定章节/修订与来源；knowledge_unit必须来自真实RAG trace实际供给且复用知识02trace/完整当前scope守卫，派生正文与原件quote出处严格分离，不扩大批准excerpt。全局未选来源/项目时仅有限问答，不隐继上个项目附件。选择器的项目/文件元数据发现不等于授权全库正文进模型。

项目结构记忆从当前有效identity/head、当前稿、规则、已confirmed且依据仍精确的事实、真实问题/决定/引用生成；未确认/失效项列缺口，不把原始fact_head存在当确认。冻结身份authority、bid/material/settings/draft/rule/fact与决定基准、source完整祖先/head、章节/实际RAG trace；模型后API最终采用逐项完整重核。归档项目可只读问答；写Action调用PM lifecycle.assert_work_writable。消息、模板、原件指令都只是数据，不能增工具/用途或进入正式评分。

## 项目与global运行

项目answer handler=assistant/1，authoring handler=authoring-generate/1；均普通core operation/Outbox/Kafka，compute(message,lease)关读连接后调用既有model_calls.generate(purpose='assistant'|'authoring')，publish(c,message,outcome)在现engine当前fence/receipt/effect事务只落不可变assistant_outputs。API coordinator才最终重核/输出真正消息/Action。Worker不读commands拿actor，actor由API冻结。

global仅单次受控answer，无通用DAG。global_run.id=job.id，core operation_id为null；独立固定逻辑topic tender.task.assistant-global.v1，payload固定event_id/run_id/input_digest/type与DB精确比对。global Worker独立acquire→短事务reserve→关连接同一DeepSeek Provider一次调用→账本记录→当前fence finish，租约heartbeat独立短事务。重复投递/有效租约不再计算；过期领取增fence，旧fence绝不发布。取消先记cancel_requested_at，真实终态才cancelled；已在途远端调用不能声称取消费用，结果可记账但不再发布消息/Action。API不具有state/fence/result列权限，不能把请求取消写成真实取消。

global每run唯一call，token预算1..8192（默认2048），在claim当前执行权下预留；同一逻辑intent_digest+request/profile的reserved/unknown在新run也拒再发，client ID/nonce不构成新收费授权。intent独立于消息ID/会话修订，绑定问题/明确范围/来源身份/用途。remote不跨DB事务/项目锁。调用succeeded且字节核验正确可复用；failed/reserved/unknown不会由普通重试器再发。新同输入run只在显式新消息/结构化重试且无未核计费时才可新预留，关闭/断线/后台重启不授权第二次模型。每run一调用及硬token上限不冒称币种真实价格预算；usage/billing_state均账本明确记录。仅MockTransport测试，不调实际收费端点。

Outbox独立claim/lease/ack，正式topic只有此global任务；实现可有CLI但本批不启动一般consumer/全局relay。真Kafka如后续测试仅PM批准隔离topic/group和明确自有事件，与知识RAG同范围，未执行全局部署。global事件通过API coordinator按会话锁单调序号追加，仅状态/资源引用，不放正文/签名URL；可after游标补读。project沿原stream/audit。

## 来源问答、问题建议与工具提案

Provider输出严格answer/citations/issue_suggestions/tool_proposals/gaps；citations只能引用实际冻结source_key，quote逐字属于提供文本。问题建议有出处但不冒称真实review finding、已解决或已确认事实。模型输出工具白名单仅review.request、authoring.generate、knowledge.candidate，完整参数schema/目标必须来自明确选定目标项目及冻结来源；任意工具名/正式评分/发布/提交/模型上线均拒。真实章节生成产物可登记既有authoring.apply_patch卡：沿原patch action_id，不造第二次确认。

API从已验证输出构造结构化差异/影响、request_digest、base revisions/source manifest/有效期限，插既有proposed_actions及assistant_action_links。pending时明确按钮confirm/reject才可消费，不看“好的”文本。所有成员以action_id唯一action_consumptions作为全局一次receipt；先锁真实目标bid，再来源图/动作行，既有receipt返原结果（不因actor或幂等键不同再做一次）。拒绝也写不执行领域的最终决定receipt；native authoring apply看到已消费也必须拒，后续把“已应用”泛化为“已有处理结果”避免误称。

confirm同TX重核payload digest、来源/项目/章节基础、生命周期和expires；异常保存stale/expired/failed与原输入，不重写提案/不自动重模型。合法后同TX调用既有review queue_intent／知识create_candidate／generation freeze+enqueue，或authoring.apply_proposal（该函数自身写原action_consumptions，外层不重复写）；记录真实领域资源/operation。executed表示明确命令已受理，任务状态另读真实operation，不冒称评审/生成已完成。拒绝不删问题/正文/任务；关闭会话不取消任何任务。

## A2生成与外部接线

authoring请求调用既有freeze_generation_plan写writing_plan，再把完整plan冻结到assistant_jobs给普通Worker读，避免给Worker writing_plans/commands业务读取。模型固定用途authoring，输出schema匹配accept_generated_proposal；API coordinator在草稿/规则/事实/来源仍一致时才调用该端口产生原有patch Action，失败/旧稿保留候选无自动应用。client明确应用按钮复用原apply/同一action_id，不能在助手再加一次口头确认。超输入限明确失败，不截断章节声称完整。

主开发最小接线：生成purpose白名单已由其统一实现；待稳定后注册两project handler与actual contracts extender。全局自有模块/运输不大改core engine。共享main/exporter/engine/生命周期代码不在本Owner并发写范围。

验收必须覆盖：私有global无fakebid、原项目主会话沿用、来源发现/版本与权限/失效、已确认事实身份、模型无事务、三种费用未核状态、global/project取消与迟到fence、跨成员/跨入口一次Action、拒绝/过期/stale、不运行假工具、真实领域返回、原章节生成端口与人工修改412、历史消息恢复及formal隔离。SQL静审仅允许fresh初始化，不是实现或生产验收。
