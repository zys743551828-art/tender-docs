# RAG与评分实现规约

本文固定首批算法工程的输入、输出、缓存、模型profile和失败路径。预测精度、训练划分与验收量尺引用[评分方案](../评分预测训练与验收方案.md)，不在本文另设精度标准。

工程断言的引用／数值核验、独立影子比较、研究批次离线预览与预算恢复按[保留功能实现规约](保留功能实现规约.md)第6、9、10节执行。正式评分仍须完整相关章节和三份有效独立判断；影子结果或批量成功计数不授予业务模型发布资格。

## 1. 首批组件与可替换边界

| 能力 | 工程基线 | 固定输入输出 | 验收与降级 |
| --- | --- | --- | --- |
| PDF渲染与原页 | PDFium适配器 | 原字节digest→页图、尺寸、旋转、页号 | 坐标不可靠时整页展示，不猜高亮 |
| 布局与文字结构 | Docling适配器 | 页→标题树、段落、表格、块范围、缺口 | 真实扫描与表格未过基准，相关页标partial |
| 扫描页OCR | PaddleOCR适配器 | 页图→文字、框、质量与模型digest | 不对可靠文字层重复OCR；原图始终保留 |
| Dense Embedding | BAAI/bge-m3，1024维，余弦，L2归一化 | 文本批→1024维向量与token／耗时 | 作为可落地首批profile，不宣称公司资料效果已优；换模型须新profile |
| Reranker | BAAI/bge-reranker-v2-m3 | query＋候选对→排序值 | 端口默认具备；开关由质量和延迟验收决定，故障可回退RRF并记录实际路线 |
| 中文词法 | 固定版本分词器＋市政词典，PG simple FTS | 分词字符串→tsvector／tsquery | 条款号、工程名、单位和专有名词另保留精确匹配 |
| 生成／逐项判断 | GeneratePort／JudgeStructuredPort | 冻结profile＋Schema＋资料包→结构化结果 | 供应商由运行配置绑定，不配置就明确未配置，不返回模拟成功 |

模型和解析器均固定具体包锁、权重revision／SHA、词典、预处理及字体摘要后运行。BGE模型名称与维度有[官方模型卡](https://huggingface.co/BAAI/bge-m3)依据，重排见[官方模型卡](https://huggingface.co/BAAI/bge-reranker-v2-m3)；这些公开能力不等于真实市政资料已验证。模型资产从供应商／内网仓取得后校验摘要，不在请求过程中下载任意main或执行远端自定义代码。

首批选择dense通道，不同时引入BGE sparse和ColBERT运行服务。1024维是本profile的具体决定，不能混入其他维度；其他模型接入另建表／索引及对应适配，不原地改embedding_profile。

## 2. 解析与章节树

1. 校验原件、加密与安全状态，渲染页图，登记物理页总数。
2. 各页独立选择可靠文字层或OCR，输出parse方法和页级质量。
3. 依据标题、目录、字号及原页顺序建立章节树；目录推断与真实标题冲突保留缺口。
4. 表格存行列、合并、单位与原图；跨页表仅在表头与结构可验证时连接。
5. 图页保留image ref及可核验视觉观察；没有视觉理解不能用OCR文字冒充已读全部图。
6. 将实际读取页、失败页、未理解图页与覆盖范围写manifest，落库后发布parse.completed，经Outbox触发索引。

EvidenceBlock的text_content是引用原文；改写摘要另建source_node并连lineage，不能把摘要当逐字引文。bbox采用归一化旋转后的页面point坐标，范围验证0≤x0<x1≤width、0≤y0<y1≤height；不可定位时bbox=null。

## 3. 分块、Embedding与索引

分块起始参数：目标800个该Embedding tokenizer token，硬上限1200，重叠120；按标题／段落／表格边界，极短相邻段落可合并但不跨不相干章节。保留chapter_path、页范围与evidence_ids。token由实际tokenizer计算，不用中文字符数当token数；资料高敏感时不调用外网token计数服务。

长表按表头＋行组切块，每块重复表头并保留源行范围，单元格超长另作明确子块；图页只对可引用的说明／观察建文字向量，原图引用随chunk保留。每份parse完成后先构造完整chunk清单，验证ordinal连续、原页存在和digest，再批量Embedding。

Embedding批次起始16条、单批总token受profile上限控制。失败仅重试缺失条目，`(chunk_id,profile_id)`唯一。写库前验证1024维、有限值和非零范数并归一化；向量内容与chunk digest组成结果身份。整份资料索引状态从预期chunk数量与成功数量推导，不以“存在任意一条embedding”判断全部可用。

[retrieval.sql](retrieval.sql)提供chunks、embedding_profiles、embeddings和索引。固定profile同维度但方法不同仍禁止混算距离。首批小允许集走精确检索，HNSW在过滤后候选不足时回退允许集内的精确排序，并在trace标明路线；不扩大来源范围换召回率。

## 4. 服务端用途与混合检索

| 输入上下文 | 允许来源 | 排除条件 |
| --- | --- | --- |
| 当前正文证据 | 目标正文、相关生效招标补遗 | 其他项目正文不能成为本项目已响应证据 |
| 章节建议／一般问答 | 当前资料、已发布且适用公司知识、允许的历史参考 | 候选、失效、其他企业事实、未明确允许的事后成绩 |
| 历史查证 | 用户明确的历史档案与已核验结果 | 返回历史身份，不转为本项目事实或事前预测输入 |
| 正式评分 | 目标正文、当时生效要求、事前有效事实、受控方法数据 | 聊天、标签、评委意见、事后知识、禁止组与其派生祖先 |
| 实验锚点 | 本fold训练来源或独立固定开发锚点 | 测试组、未来标签、同组／近重复及其派生内容 |

allowlist由领域服务生成后传RetrievalPort，公开请求不能传allowed_manifest或formal_score。来源检查遍历source_ancestors，任一祖先未发布、已停用、未来才已知或落在禁止组即排除。[eligible_chunks](retrieval.sql)是这一检查的可执行基础，工程适用条件及实验完整协议仍由服务端按manifest进一步缩小。

精确SQL路径使用绑定参数：

~~~sql
WITH allowed AS MATERIALIZED (
 SELECT * FROM retrieval.eligible_chunks(%(sources)s,%(as_of)s,%(excluded_groups)s,%(formal)s)
)
SELECT c.id,c.source_id,c.parse_id,c.physical_page,e.embedding <=> %(query_vector)s::vector AS distance
FROM allowed c JOIN retrieval.embeddings e ON e.chunk_id=c.id
WHERE e.profile_id=%(profile_id)s
ORDER BY distance,c.id LIMIT %(candidate_limit)s;
~~~

query_vector在服务端用相同profile生成并做维度校验。词法分支对allowed集合的lex_tsv使用`plainto_tsquery('simple',query_lexemes)`并ts_rank_cd排序。两路各50候选起步，按`sum(1/(60+rank))`作RRF，前20进入可配置reranker，最终至多8个证据包；这些是起始配置，不是效果结论。

检索返回source、parse、页、块、距离／排名与trace ID；上下文组装携带引用ID与逐字摘录，模型输出引用必须属于本次允许且实际提供的包。引用不存在、页不一致或主张超出证据时，结果降为不支持或无法判断，不能仅改写citation来补齐。

## 5. 正式评分任务图

~~~text
freeze_review
 ├─ prepare_rubric ─ map_full_chapters ─ build_chapter_packages
 ├─ core_compliance_checks
 └─ 对每个评分项：judge(item,1)、judge(item,2)、judge(item,3)
      └─ validate_evidence ─ build_features ─ qualified_predict
所有分支完成／明确失败后 assemble_review_projection
~~~

阶段计划预先登记全部必需子项。每个replicate使用同一冻结章节包和方法，但不接收其他replicate输出。重试仍是相同replicate，缓存命中必须带replicate及全部方法身份。

ChapterPackage包含规则／档位／例外、完整相关章节、必要跨章引用、表格与图页、源范围、已读／未读及method digest。模型上下文预算明确预留系统、规则、输出；超过时按可追溯子节分包并逐包判断，最后跨节汇总。任一关键子包未读不能标完整；摘要只能辅助导航，不替代实际子包覆盖。

模型结构化输出固定ItemJudgment：档位ID、要点覆盖、针对性、具体性、证据与缺口。档位ID必须来自当前rubric；伪造ID、非法引文、缺少必读范围或截断输出为invalid。至少三份有效判断才进入完整方法特征，否则按方法协议拒绝该项或只给部分判断，不能默认为0。

## 6. 特征、监督模型与发布

FeatureBuilder固定feature_schema_digest。起始特征包含各档位频次、三次分歧、要点覆盖、有效／不支持证据比、针对性／具体性结构化量、图表与读取缺口；不直接用字数、项目名出现次数冒充质量。特征计算线上与离线共用纯函数与同一方法包，缺失值和量尺预处理只在训练内拟合。

标签只从有效实交与经核验官方技术分配对导出。Pipeline先对比常量、量尺基线、原始判断、岭回归等简单模型；项目／模板／近重复连通组整体留出并满足时间边界，全部标准化与锚点重算按fold执行。训练清单保留每个来源和取得时刻，不能先全库生成带标签特征再随机切分。

模型产物包括Pipeline、feature schema、环境锁、训练／校准／测试manifest、适用范围、完整评价与失败覆盖。只加载本系统受控存储、摘要核验且人工通过验收的产物；不加载用户上传的任意pickle。发布命令检验acceptance与model匹配、影子观察、部署可读和rollback对象，原子切换route。GET /model-routes/{route_key}在尚未发布时返回revision=0、model_id=null与可条件写入的ETag；首次发布expected_route_revision=0，事务按该route_key唯一插入，其他并发发布失败为412。首次发布显式传rollback_model_id=null，固定回退为正式预测不可用；已有旧模型时只允许回退到仍合格且适用的模型。回退使用ModelRollback，显式target_model_id=null时将route的model／acceptance同时置空并递增revision，不删除路由，也不伪装为首次未发布。

未获资格时返回raw_uncalibrated或unavailable，隐藏预测时不序列化value／interval。区间、排序和技术入围概率分别验收，不能由三次LLM输出波动直接计算。真实精度验收仍以评分主记录为唯一口径。

## 7. 缓存、失效与质量验收

缓存键至少包含用途、原件／parse、规则／事实、chunk/package manifest、profile、提示、模型、replicate、检索策略与fold／锚点digest。不得跨正式评分与聊天共享含成绩的模型缓存。停用后立即通过权威head禁止新应用，Kafka影响事件更新列表与索引，但删除缓存不是停用成立条件。

真实资料验收分四层：解析页与块正确性；检索Recall@k、排序与引用正确性；逐项判断档位／证据与缺口；正式技术分独立误差与拒绝覆盖。各层报告自己的成功、失败和未读范围。合成向量只能验证SQL距离、过滤和权限，不作为Embedding或RAG效果证据。

## 覆盖与冻结身份约束

formal_score_context经score_context_chunks严格用review_inputs.as_of调用eligible_chunks；frozen_at只作冻结审计，禁止用其代替业务评估时点。

正式评分与检索as_of由[详细设计第3.2节](../AI投标工作台详细技术设计.md#32-输入修订与摘要)统一冻结；不得用每次请求时间重算内容身份，也不得排除业务有效时间。检索所用来源/祖先head及效力窗进入证据清单。ReviewCoverage的source_scope、core_extraction、judgment和scoring分开，人工确认另列；完整阅读是输入范围证据，不证明核心条款没有漏提，漏提率依赖独立标注报告。纯评分未就绪不改变核心核对执行分母，仍不得给未经资格验收的正式预测。已知关键风险跨轮保留与开关语义执行[保留功能规约第5.1节](保留功能实现规约.md#51-覆盖分层与已知关键风险)。
