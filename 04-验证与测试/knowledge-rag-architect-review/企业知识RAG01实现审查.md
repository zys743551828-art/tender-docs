# 企业知识RAG实现审查

结论：在本次模块边界内通过，正常调用路径阻塞0；保留一项P2数据完整性加固观察。此结论允许继续集成与独立QA，不代表整套工作台或正式评分验收。项目索引05的独立增量结论见[项目索引05增量复核](../rag-architect-review/项目索引05增量复核.md)。

## 输入与执行身份

候选为[rag-candidate-01.json](../knowledge-development-checks/rag-candidate-01.json)，SHA256 `4671b3d9cd24afa3429f388c76bd02d4ee88c53c0ac6869bb06976c7aff24a15`。逐一核验22份冻结源、32份依赖，并在执行结束后再次核验。实际加载rag_api、rag_search、rag_repository、rag_tasks、bridge、knowledge_routes均来自冻结副本；非当前移动源码。记录及全部证据摘要见[implementation01-review-record.json](implementation01-review-record.json)。

架构师在自建全新PG17+pgvector隔离库初始化已审SQL；业务测试只用真实非owner API/Worker LOGIN。对象端口显式内存，业务原文和事实为合成夹具，含真实合成PDF字节。没有访问既有业务库，没有启动消费者，没有收费或云对象请求。真实本地模型一组经授权部署包装器只消费runtime URL/token，未输出或保存秘密；随后仍使用自建数据库和内存对象端口，未使用私有profile的数据库。

## 验证结果与实际覆盖

共42项产品机制检查通过：首组39项（冻结纯机制/集成37项、架构师RC边界2项），重排期间来源撤回与身份重新确认2项，真实本地BGE及重排1项。首组跳过真实模型/Kafka/OSS三个专项，其中模型另行独立执行；Kafka和OSS本轮未重跑。畸形trace反例另计，不混入通过项。

- 真实受限PG验证六表组合约束、API输入列与Worker结果列授权、向量1024维/有限值/Float32回读、跨profile/版本拒绝、发布回滚、旧fence与迟到代次拒绝。API不具备写ready结果的入口，Worker不能写trace、引用，未增加工程、公司、commands的访问授权。
- 检索首次提交、同key重放、首次引用和引用重放重核完整scope：项目身份、全部匹配事实及确认basis、知识版本与ready索引、unit manifest、单元自身及祖先head/用途/效力窗/精确父关系。相同值恢复、同UUID身份重新确认、同版重索引均不能使旧trace获得新用资格。历史读取保留原evaluated_at和原文，不补造当前资格。
- 最终连接、引用和重放的RR事务均拒绝，无新增trace、Command或引用。非RC的底层异常在部分路径为ValueError，而非统一领域错误；这是配置错误时的错误呈现问题，实证未出现失效来源发布。
- 模型阶段没有业务数据库连接或图锁。独立重排窗口探针在远端计算阶段撤回原件根或重新确认目标身份，最终提交均拒绝且无trace/Command。其他允许数据先入库再失效的用例核验旧trace不能重放或新引用。
- 真实本地BAAI/bge-m3与bge-reranker-v2-m3执行tokenize、lexical、embed、rerank；观察每次RPC持有业务连接数为0，稠密向量1024维。实际直接engine acquire/compute/finish加登录ASGI完成索引、融合检索和引用，重复请求不再发模型RPC，effect一次、model_calls为0。此项没有Kafka传输，业务文本为合成资料，不能据此声明企业检索精度。
- `knowledge_text`与`original_quote`保持类型和offset基准区分，输入来自批准知识文本或批准摘录，不扩成原件整段。原文摘录逐字及物理页证据绑定；引用为参考，不自动写项目事实，formal用途拒绝。

[机制JUnit](implementation01-independent-junit.xml)、[重排窗口JUnit](implementation01-rerank-window-junit.xml)、[真实本地模型JUnit](implementation01-actual-local-model-junit.xml)和[模型观察明细](implementation01-actual-local-model/actual-local-model-result.json)构成实际执行证据。第一次自写RR探针对异常类型预期错误的日志另行保留；只调整自有断言后复验通过，未改候选源码。

## P2：畸形服务端trace的selected内容未在首次引用统一校验

定位：[rag_search.py:362](../knowledge-development-checks/rag01-inputs/packages/knowledge/rag_search.py:362)的cite_unit从selected_units取文本并核quote；[trace_row:337](../knowledge-development-checks/rag01-inputs/packages/knowledge/rag_search.py:337)只核冻结scope摘要。[rag_api.py:23](../knowledge-development-checks/rag01-inputs/packages/knowledge/rag_api.py:23)的检索重放另有selected全记录属于allowed units的比较，[save_trace:281](../knowledge-development-checks/rag01-inputs/packages/knowledge/rag_search.py:281)也做此比较，但首次引用未共用。

最小反例使用API角色直接INSERT一条畸形trace：保留真实冻结scope/digest、unit/version和来源身份，仅把selected_units中的text替换为未批准句子，然后引用该句。首次引用成功并写入citation。证据为[观察记录](implementation01-selected-manifest-observation.json)与[反例JUnit](implementation01-selected-manifest-junit.xml)。

这不是公开用户可提交的trace，未发现HTTP写入此畸形对象的路径，普通Worker也没有trace写权；正常save_trace会拒绝该selected。故本项按持久化数据完整性加固记录，不归类为正常业务越权或本模块集成阻塞。历史读同样使用selected内容，应共用完整验证以缩小服务端未来错误写入的影响。

建议将selected manifest的唯一性与完整记录归属核验抽为统一读取守卫，供检索重放、首次引用、引用重放和历史读共用。验收：上述直接API畸形夹具在引用前明确拒绝，0citation；合法trace首次引用与同key重放保持成功，未批准内容不能由历史返回为合法供给。Owner若修复应提供新冻结候选，本报告不把未发生的修复算作通过。

## 范围外

未独立重跑本候选的Kafka与native OSS专项；候选自测仅作已有开发证据，不折算为架构师执行。未验证真实企业标注集检索质量、规模/ANN容量、前端、集中部署、上传解析即时可读窗口或生产沙箱。本结论不替代A1必要范围核对、正式评分干净输入/完整相关章节/三判断，也不授任何模型正式资格。
