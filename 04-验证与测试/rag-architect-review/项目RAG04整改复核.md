# 项目RAG04整改复核

结论：本次项目检索API增量通过，RAG03-RR01闭合，剩余阻塞0。仅放行本冻结模块，不覆盖知识RAG、索引整链零连接、真实召回质量、正式评分或生产。

输入candidate-04 SHA256 `d6057de89b507423f1849478d2e19e0c4f9bc1200ab1e8789b741a36ad73398c`，逐一核验32源。相对03只改变apps/api/rag_routes.py与tests/test_rag_staged_api.py，其余30源摘要一致。03原撤回后200反例、脚本与阻塞报告均保留。

`assert_fresh_authority`（冻结rag_routes.py:37）拒绝autocommit和非READ COMMITTED；首次读取Command前（137）、replay入口（90）、最终写连接第一步（180）均调用。没有读取后晚SET隔离；初始完整scope快照仍故意使用RR只读，commit之后重新设置LOCAL search_path。故非RC不能先取得旧快照再等图锁发布，正常RC下的bid→graph最终检查保持。

架构师在全新隔离PG17+pgvector、受限非owner API/Worker、真实ASGI和显式合成loopback HTTP下独立7项通过：沿用03默认RC的3项（含旧键完整祖先head、模型HTTP闭连接/并发幂等/效力候选排除、独立body头竞争），另4项分别强制首次连接RR、仅最终连接RR、直接replay helper RR和autocommit。首次新键/旧键RR均在模型RPC前409；仅final RR虽已计算但不增加Command/trace；直接helper和auto亦拒绝。角色及源代码未用owner执行业务。

另只读核对独立QA noninvasive-connection-probe/result.json：其13项包含真实BGE HTTP、真正RC图锁等待后撤回→409且0新增Command/trace，以及首次/最终RR边界。QA证据摘要独立绑定于candidate04-review-record.json。架构师自己的7项没有实际BGE推理；两者分别记录，不能合并成我执行的真实模型用例。

本次未读私有配置、未收费、未启消费者、未发OSS请求；没有重跑RAG02真实原件链或业务质量。沿用prepare_index仍在读取连接内tokenize/lexical，未持图锁或fenced发布锁，本次不把endpoint零连接结论推广到索引。证据与脚本摘要见candidate04-review-record.json。
