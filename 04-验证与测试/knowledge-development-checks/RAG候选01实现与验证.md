# 企业知识 RAG 候选 01：实现与开发验证

本候选是 D03 可靠复用之上的独立知识语义检索实现。状态：开发完成，等待独立架构/QA；不表示整个产品、前端真实知识RAG链路或生产部署已完成。D01/D02/D03、A205 及失败反例原文件保持。

## 最终行为

API 为当前项目短事务冻结 confirmed 身份 authority、事实决定与依据、知识版本/适用条件/期限、来源完整祖先、ready index 及单元实际 manifest。连接关闭后才做 query 向量/词法与重排，最终再完整重核并写命令/trace。普通 Worker 短读受控输入后关闭连接再计算，当前 engine fence/effect 事务完整发布新代；未扩工程/公司/commands 权限。旧代迟到、来源变动、不完整向量或读回不一致均不发布局部成功。

knowledge_text 派生正文与 original_quote 原件批准摘录各自分块、单元/来源 ID、span、digest、出处，绝不把知识摘要伪装原文 parse/chunk，批准摘录之外的同段尾文不会进入模型。1024 float32 cosine 向量固定身份，Top50 dense + Top50 FTS → RRF60 → 实际前20 rerank → 至多8。重排故障明确回退融合顺序；模型不可用明确503。索引数量上限、项目资格上限、query token 上限整项校验，不补假命中。

检索首次/同键重放、引用首次/同键重放共用完整 scope 守卫。重索引替代、unit 自己 candidate/撤回/到期、来源祖先变动、事实依据改后值恢复、身份同 UUID 新 authority 均不能借旧 trace 再用。旧 evaluated_at、实际供给与引用字节保留，当前告警另列，原件仍可还原。引用不创建项目事实、规则或正式评分输入，formal_score 不开放。

实际协议在 rag_contracts 独立扩展，共 5 路径 6 方法；索引 POST 必须给当前 knowledge_version_id，202 只表示受理，GET indexes 返回真实代状态与失败原因。API 协调器可限定知识条目，只冻结/排 Outbox；并不在业务事务里跑模型或 relay。新表或相应角色 SELECT 不可用明确503，不自建/补权/换 owner。

## 验证与证据

最终主套件 48 通过：15 新 RAG 纯单测 +8 原 D 闭合协议回归 +25 真实 PG/HTTP 集成（其中包含实际本地模型、隔离 Kafka、原生 OSS 三条不同范围用例）。另旧 backend checks 库只读缺表探针 1 通过，总计49项；schema-capability.xml 是主套件内同一能力负例的预跑，不重复计数。

四项质量门禁通过：Ruff、format、mypy15源、Bandit。完整输出 quality.json。运行前后22源和32依赖字节一致，完整清单 baseline-integrity.json；冻结副本逐字节重核，不放生产 packages。pytest.xml 中第三方 Starlette/OSS datetime 弃用提示不影响断言，未隐去。

- 真 PG：API/Worker 真实非owner LOGIN，六表输入/结果列授权、Worker 无业务身份/commands/trace 能力；API不能写 ready，Worker不能改受控输入。空 ready、错误向量维度/范数、跨profile/版本FK拒绝；单元 source 不可变列也由现有 append-only trigger 阻止修改。非法直接祖先在使用守卫被拒。
- 故障：旧 fence finish 无效果、新 fence 可发布；错 bid 任务拒绝；部分向量发布回滚、真实 operation failed 和错误代码可读、新一代可恢复；来源在模型阶段撤回阻止最终 trace 且没有未完成 Command；无模型无假 trace/命中。
- 真实模型：实际本地 BGE-M3 与 BGE-reranker，明确 runtime/weights/preprocessing 身份。记录实际调用输入 digest、1024维与关闭连接观察；非公司质量样本。高频存储/失效机制使用显式 fixture vectors 与内存对象，均不冒称实际模型效果。
- Kafka：仅自有临时 topic/group，复制一条精确自有已提交 Outbox 协议消息两次，实际 engine执行1、重复忽略1、effect1；精确删除该 topic，不改既有 topic。没有全局 relay、没有19095旧全topic普通consumer；该用例不能证明正式全局部署。结果绑定实际 Outbox/event/op/topic/group，见 isolated-kafka-result.json。
- 原生 OSS：本候选用例使用用户已授权 bucket 的既有独立 prefix，精确自有合成PDF key PUT/GET，SHA一致、实际 PDFium 原页命中。撤回后新引用拒绝、旧引用/原件字节还原，真实登录历史和下载grant创建通过；不记录/外部抓取签名URL，不扫桶/改policy/CORS、不删除有引用原件。该证据按最终用例计数；开发预跑也曾对同一已知内容键重复验证，不声称整个开发仅一次对象请求。

模型全部本地，付费模型调用0。测试生成的合成工程/历史索引/引用留在独立 checks 库，不以清理为由改历史事实。部分预跑曾因测试观察器导入缺失或测试企图 UPDATE append-only source 被 DB 拒绝，修正自有测试后最终全部通过；不把这些预跑失败改写成产品通过，也未绕过触发器。

## 固定依赖与运行边界

主后端04：a1-candidate-04.json SHA eb5266493f8106fb2da16bbc5508a64dff8aae09264b134e2fe22696217431f7，72源最后检查仍一致；engine SHA288405a99611de3ec8e0962d6f3492f108b3499c455d974d45149648d8f02e45，已含 knowledge-index/1 注册。项目索引05依赖候选 SHA8dbb86c0203cdecc51b29c24d5d5ee91b4d2d5e066cc67ccc18b05a5396b21fb，indexing 当前362451119c67f935ac2b0a530278a53fb75daa2ad95ceaf8b8e52601701c56ad；该05当时仅开发9项通过，独立复验待办，不能借已审检索04外推通过。身份02/runtime/fact_sets 与D03原冻结依赖一致。

SQL SHA51c98cf1aea5364b418747161e86bb374b9b3d44222d67891ec18e27bc200003，静态审通过后仅 PM fresh 初始化 tender_checks_knowledge_rag；本实现未执行 DDL、未热改旧release/研究库、未用owner做业务。六个新表的明确列授权属于新 schema，不宣称“无新表授权”；普通Worker业务身份能力没有增加。旧缺表探针只 SET TRANSACTION READ ONLY 并查元数据；没有修复旧库。

实现复验命令见 live README。私有env路径 .local/knowledge_rag-checks.env，不打包任何私有配置/口令/令牌；schema-absent 读探针单独以 .local/backend-checks.env 和 RUN_KNOWLEDGE_RAG_SCHEMA_ABSENT=1 跑。不要启动普通全topic消费者或全局relay来复验该候选。

待独立确认：架构/QA真实反例与安全边界、真实公司语义质量、规模/ANN性能、前端整合与集中实际协议部署。前端另发现解析 complete 到原页可选之间等待向量索引发布来源的窗口；该跨模块问题已交 PM/parse Owner，知识 GET 没有偷偷发布来源。候选证明从有效已发布可读来源开始的知识路径，不把 upload→parse→即时出处全路径算已通过。
