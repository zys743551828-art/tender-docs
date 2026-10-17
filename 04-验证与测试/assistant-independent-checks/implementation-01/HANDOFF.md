# 完整助手 / A2 执行实现 01 交接

候选以本目录 candidate.json、input-archive.json 和 frozen-tree 原字节为准。自测从 frozen-tree 执行，避免同时开发的主后端改变验收输入。原知识 RAG01/02、D03、A205、工作会话01和主后端历史候选保持不变。

## 已实现与接口

21 个自有输入：packages/assistant 全模块/README/已审 SQL、assistant_routes、A2 patches 最小守卫、3 个自有测试；152 个保全的保守依赖闭包及 1 个公开合成 PDF 夹具。所有源/依赖都有实际副本与 SHA，非仅观测摘要。独立 FastAPI 注册 21 个实际操作（原项目会话 6 + 新执行 15），actual-openapi/scope 随证据归档；不表示共享 main 已注册。

- 项目沿用同一主会话、消息和 core operation；global 是真实 null bid 的成员私有主会话/run/call/Outbox/events，不造假项目。关闭/隐藏与项目阶段独立，关闭不取消在途任务，恢复仍返回同一历史。
- 服务端来源发现供 @ 选择，固定具体版本、原页或真实知识 trace/unit；项目范围、来源/祖先/parse 与实际 quote 守卫。结构记忆仅提供依据仍有效的当前 confirmed 事实，准确保留主体/范围/值/单位及权威摘要；规则/旧问题明确限量与原版本，均不冒充完整评审或新问题已解决。
- 受控模型输出包括带来源只读回答、独立问题建议和服务端白名单工具候选。显式 confirm/reject 按同一 action_consumptions 全球一次消费，跨成员、跨入口共享真实结果；stale/expired/failed 不写领域。没有从“好的”文字直接执行的路径。
- A2 复用 freeze_generation_plan → core 账本 purpose=authoring → accept_generated_proposal；模型先产候选，同一原 patch Action 人工确认后才追加章节修订。选区外文本/格式不变；原页 evidence allowlist 保留到应用后的 source_refs/citations。生成/应用重核项目主体和当前事实基准；native A2 apply 对助手关联卡执行同一守卫，拒绝后不能换入口应用。
- 模型与对象网络在事务外，冻结输出 token 与调用身份；reserved/unknown/failed 不自动补发。global 逻辑 intent/profile advisory 锁先于 run/fence，全表 unresolved 查重；旧 fence 结果不能发布，已成功结果按已存原 blob 复用。取消只请求真实状态，不伪造终态。
- API coordinator 最终重读完整项目/来源资格再持久化消息或 Action，API 不锁无 UPDATE 权的 append-only jobs/outputs/publications。Worker 不新增公司、工程、commands、聊天、Action 或 publication 权限。base Worker 原有 bid_profiles 当前头读取能力保持；不能把它误报为本模块新增身份权限。

## 给 backend07 的精确 hook

`packages.assistant.tasks.compute(message, lease)` / `publish(c, message, outcome)`：

| handler_version | step_key | topic | kind |
| --- | --- | --- | --- |
| assistant/1 | assistant-answer | tender.task.assistant.v1 | assistant-answer |
| authoring-generate/1 | authoring-generate | tender.task.assistant.v1 | authoring-generate |

payload_ref 为 assistant_jobs.id；TaskEnvelope input_digest 为 sha({id: job.id, digest: job.input_digest})。prepare 只短事务读冻结输入/来源头；compute 调共享 model_calls 固定 assistant 或 authoring purpose；publish 仅写 assistant_outputs 并绑定实际已入账响应字节。随后 API `packages.assistant.coordinator.publish/tick` 做最终业务资格和消息/卡片物化。authoring 不授 Worker writing_plans/commands 读取权。

backend07 请统一新增普通 handler 白名单、task_topic、compute/finish 分派及 Kafka assistant topic 消费注册；不启动消费者。assistant/1 是归档项目允许的只读任务，应加入 lifecycle work_error 豁免；authoring-generate/1 仍守写入生命周期。global 独立 `global_runtime`/`transport`，不改 core operation 非空约束或普通 engine 成为全局 DAG。

本候选固定 backend05 engine SHA f8de4159850ff1ebe7c045f79ca2ea3796761964a4c58191720994867b4cc02b，来源路径见 input-archive 对应 observed_input_path。project/生成机制用该真实 acquire/finish + 测试显式两端口 adapter，未声称共享 engine 已正式注册。backend07 实际接线、所有 producer 生命周期矩阵、共享 API8806、真实 Kafka/OSS、前端和模型效果需独立组合验证。

## SQL与执行

SQL01 SHA 30104431617fea8798bd21955d9dc8f349b70b1f6d25006a27c7705aecb3b8f9 / 协议01 SHA 264e9f2cb52b6e54db9c1268043184c69a3b48a59f31fa6f9c17a0eb137dd5b9 均未改。PM 已在全新 assistant01 checks 库初始化；本次业务测试仅真实非 owner API/Worker。私有 profile 不归档、不输出口令/DSN，未修表、补授权或修改旧 release。global 预算为冻结单次输出 token/唯一调用，未把它称作货币硬上限。

在 frozen-tree 目录执行，使用现有虚拟环境及明确非 owner profile：

```text
RUN_ASSISTANT_INTEGRATION=1 RUN_WORK_CONVERSATIONS=1 RUN_AUTHORING_INTEGRATION=1 /workspace/tender-workbench/.venv/bin/python /workspace/tender-workbench/deploy/run.py --env-file /workspace/tender-workbench/.local/assistant01-checks.env -- /workspace/tender-workbench/.venv/bin/pytest tests/test_assistant_integration.py tests/test_work_conversations.py tests/test_authoring_integration.py -k 'not real_worker_build and not real_authenticated_http and not actual_build_http' -q --junitxml=../evidence/real-pg-junit.xml
```

模型为显式 httpx.MockTransport；高频对象端口为内存夹具。没有实际模型费用、Kafka 进程、OSS、本轮 Word/PDF 转换、真实企业准确率或生产发布。Outbox relay/consumer 的测试是显式 transport 夹具，不冒真实 Kafka 验收。实际统计以 candidate 和原始 XML 为准。

首次预冻结错误断言（Worker 不可读 bid_profiles）及第二次捕获移动 backend07 WIP 阻止归档只读助手的失败均原样保留在相邻 implementation01-preflight-01 / -02；没有改成通过。正式01固定稳定 engine 原字节后重新执行，不复用失败轮次当作结论。
