# 显式 Mock 助手浏览器设施 01

本目录只提供测试设施，不能作为真实模型效果、真实 Kafka 或原生 OSS 验收证据。

首批固定输入为 `task-drafts-development-checks/candidate03/candidate.json` 列出的 317 个源文件。最终预览用 `rebind.py` 新建独立 candidate04 绑定及证据目录，保留 03 配置、对象、会话与原证据；04 修复任务 DraftView 与编制 DraftView 的契约同名覆盖。启动前逐文件核对 SHA256，业务模块只从相应固定根加载。API 预览显式挂载任务草案 router 并扩展实际 OpenAPI；不修改冻结源文件或生产 main。

使用 PM 单独初始化的新库及两个新账号，Origin、端口和对象目录均独立。每个业务子进程只有一个非 owner LOGIN DSN：`api`、`publisher`、`seed` 为 API 角色，`worker` 为 Worker 角色。启动器只向子进程传递正向白名单配置，拒绝非回环数据库、owner DSN、模型密钥及云存储凭据。成员口令文件只供浏览器登录，业务子进程只读另行登记的成员 UUID。

对象端口显式换成本地合成夹具，返回 `X-Assistant-Fixture: explicit-mock-no-provider-no-kafka-local-storage`。真实原件／私有生产资料不放入此目录。API 没有额外的控制 HTTP 路由，控制文件由本地 CLI 写入。

默认 `hold`：Worker 不执行。验收者显式选择 `answer`、`knowledge_action` 或 `unknown`，以及 `global`／`project` 范围后，Worker 只读取登记成员的对应冻结 Outbox，调用原有 handler；不启动 Kafka consumer、不改 Outbox 为已发送。`unknown` 由 MockTransport 抛出明确超时，真实账本仍须阻止同逻辑意图再次调用。取消用例先保持 hold，经真实 API 请求取消，再选择 answer 让 handler 核对取消状态；应产生终态且不进入 Mock。

Worker 的模型端口仅为 `httpx.MockTransport`，默认 HTTP/HTTPS transport 与非回环 socket 出站一律拒绝。返回正文明确标注“显式离线合成机制回答”。`knowledge_action` 必须由真实 source chooser 选择的原文及明确目标项目产生，候选发布、Action 确认/拒绝/重复消费均走原 API 和真实数据库。Worker 不发布业务答案；独立 API publisher 调原 coordinator，核对最终来源并持久发布。

第一批验收 global 单条回答、刷新恢复、可信来源、知识候选 Action 一次消费、拒绝、取消、unknown。第二批补 project 同协议。合成 PDF 由实际 PDFium 提取文本再建立明确的夹具解析记录，不宣称经过原生资料上传、Kafka 解析、RAG 向量入库或真实业务核验。

未配置资源时仅执行设施自身的无网络测试；不得复用 live 8808、其他 QA 账号或真实模型密钥。
