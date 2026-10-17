# 成熟 AI 系统与技术组件调研

文档编号：RES-TECH-001  
类型：全新系统选型调研、证据与适用性判断

## 1. 调研问题、范围与方法

以 [AI 投标工作台 PRD](../02-业务与产品/AI投标工作台PRD.md) 为业务输入，从零选择技术架构。现有代码、数据库、框架和部署方式不作为必须沿用的条件。比较三条路线：平台二次开发、Agent 框架主导、领域服务加持久工作流。目标技术方案见[全新系统技术方案](../05-技术与设计/AI投标工作台全新系统技术方案.md)。

通用组件调研读取了28个官方仓库，保存成功取得的77份文档、源码及许可文件的固定提交与内容摘要。来源包括完整应用平台、工作流、AI界面、RAG、解析、搜索、评测和观测。执行路线与相似产品采用独立专项清单；直接行业产品及云厂商的14份网页原文见[相似产品技术核查](相似AI产品技术路线核查.md)，不混入通用组件仓库统计。未调用付费产品或供应商演示服务。

证据分层：E1为官方自述与README，可证明公开能力描述；E2为具体文档与源码，可检查机制和限制；E3为本环境可重复实验，只支持实验覆盖的机制。README上的性能或质量宣传不作为本产品的验收结果。源码快照取调研时HEAD，不代表建议直接部署开发分支；运行实验另外锁定发布包版本。

## 2. 选择标准

### 2.1 必须满足的条件

- 项目、实际提交稿、正式成绩和知识版本有明确主记录，不能只保存在聊天／向量库中。
- 浏览器、Worker重启、重试和人工等待不丢业务决定；恢复后重新核验权限、对象版本和适用性。
- 可把普通建议、正式评分和成绩复盘分开构建上下文，隔离事后信息及派生知识。
- 每个结论能定位原件；评分覆盖、模型验收和在线统计分别表达。
- 内部少量成员可以维护；新增基础设施必须有直接用途和恢复方案。
- 没有真实成绩配对时可以完成基础工作，但不能宣称评分精度已验证。

### 2.2 比较方法

分别比较业务一致性与恢复、数据／模型质量、维护总成本、交互整合和后续扩展；按交付阶段判断需求，不把项目存续时长等同于工作流等待。各路线采用“可直接提供／需要定制／尚未验证”描述，不把未实测能力转换成精确评分。统一比较负载与资料以后，才进行性能和费用对照。执行器完整路线及公开产品对照见[专项复核](AI执行与工作流选型复核.md)与[相似产品技术核查](相似AI产品技术路线核查.md)。

## 3. 完整平台

| 方案 | 已读资料与公开能力 | 本产品可以借鉴或利用 | 需要额外解决的问题 | 结论 |
| --- | --- | --- | --- | --- |
| Dify，SRC-01 | README、许可、检索方法和数据集模型；可视工作流、RAG、模型接入、工具与LLMOps | 工作流调试、知识接入、工具结果与应用API | 项目轮次、实际稿／成绩配对、版本闸门及独立评分实验仍需本产品域模型；把业务数据再同步进平台增加一致性边界 | 平台路线候选，参考其产品模式；不选作全系统业务主库 |
| RAGFlow，SRC-02 | README与许可；复杂文档、模板分块、引用、知识编译、多轮检索 | 分块可视化、证据定位、检索质量工作台 | 整体部署和文档模型需适配；没有取得本公司材料上的解析及正式评分对照证据 | 强RAG候选，可在统一评测集对比；本次未部署 |
| FastGPT，SRC-03 | README与许可；知识问答、可视工作流和工具编排 | 中文团队使用方式、知识维护与工作流体验 | 成绩核验、事前／事后隔离、模型发布及业务审计需要明确领域层 | 快速知识应用路线候选；本次未部署 |

平台可以用于原型、知识应用或独立检索服务。采用平台并不自动取得与正式技术评分接近的模型；这仍需要真实配对、冻结协议和独立测试。没有证据据此宣称平台无法实现某功能，本次结论只表示现成资料没有证明可直接满足全部投标业务约束。

许可实际读取：Dify和FastGPT采用附加条件的Apache体系许可，涉及多租户或前端标识等条件，不能简写成无附加条件Apache-2.0。公司内部使用不因这一点自动被排除；若引入其代码或界面，需要按选定发布版本核对条款。该项不作为拒绝平台路线的主要理由。

## 4. 工作流与 Agent 编排

| 方案 | 已核实机制 | 能解决什么 | 不能由它单独保证 | 选择 |
| --- | --- | --- | --- | --- |
| Temporal Python SDK／CLI，SRC-05、26 | Workflow历史、确定性约束、Activity重试、信号／等待；真实开发服务实测 | 长任务、批处理、人工等待、进程恢复、超时取消 | 外部数据库写入恰好一次、业务授权、知识适用性、评分正确性 | 跨步骤持久执行候选；正式生产部署与收益另验 |
| LangGraph，SRC-04 | 图状态、checkpoint、interrupt；源码明确中断节点恢复可能重执行 | 多步骤Agent、工具循环、推理状态和可暂停图 | 节点内业务副作用不重复；checkpoint不是业务事实；恢复不自动重新授权 | 区分开源库加任务服务和官方Agent Server两条完整路线，详见专项复核 |
| Haystack，SRC-06 | Pipeline组件、检索、路由、过滤、流式运行及扩展点 | 可组合RAG和生成链路 | 项目／知识生命周期、标签隔离及实际提交登记 | 组件化设计参考；首版无需为少量明确步骤引入完整框架 |
| LlamaIndex，SRC-07 | ingestion pipeline源码提供缓存、文档管理与向量写入；README保留OSS工具并说明公司重点转向解析产品 | 连接器、摄取与文档问答 | 当前项目事实、正式成绩准入及全通道实验隔离 | 作为集成候选，采用前按具体模块评估；不把整个套件作为必须依赖 |
| 自建PostgreSQL任务执行器 | 数据库行锁／唯一约束机制可验证，队列、租约、重试、取消、升级和恢复需要自行实现 | 少量固定任务时基础设施较少 | 不会因为用了数据库就自动具备完整持久工作流 | 与Temporal比较后，不作为全新系统首选；不是因为现有实现而保留 |

两个实测结论影响架构：Temporal的Activity在业务事务提交后故障会重试；LangGraph中断恢复会再次执行中断之前的节点代码。未保护的计数都出现两次，增加数据库幂等约束后才保留一次。因此正式决定放在领域事务中，编排仅携带稳定操作ID并调用该事务。

Temporal的代价是增加服务端与历史存储维护；其价值取决于实际跨步骤等待和恢复。成熟队列与官方Agent Server同样纳入完整路线比较，不能只比较Temporal与完全自写执行器。切换要通过同样的中断、重复和升级验证，任务调度主记录保持明确。

## 5. AI界面

| 方案 | 证据与适用点 | 取舍 |
| --- | --- | --- |
| assistant-ui，SRC-08 | React组合组件、自定义runtime、工具UI、人工确认；npm元数据声明React18／19兼容 | 推荐验证其消息、输入区、滚动和工具渲染组件；业务卡片与确认结果由本系统服务端提供，不使用托管会话作为业务主库 |
| Vercel AI SDK，SRC-09 | 聊天流、消息结构、工具调用与工具结果文档 | 可借鉴typed message parts；若选择其前端传输需实现协议适配，不为此额外建立Node业务后端 |
| React自建工作区 | 对话、资料、证据及比较布局需要领域交互 | 保留定制空间；不重新实现全部基础聊天可访问性和滚动细节 |

推荐React19＋TypeScript＋Vite的内部SPA，统一调用Python API。Next.js的SSR和全栈能力是成熟选择，但本产品没有SEO需求，也不需要两套业务服务，因此不以它作为默认前提。assistant-ui适配尚未完成软件实测，不能将“包声明兼容”写成工作台已集成。

## 6. 文档解析与证据

| 方案 | 已取得证据 | 本项目评测重点 | 当前判断 |
| --- | --- | --- | --- |
| Docling，SRC-10 | 统一DoclingDocument、布局／表格／OCR、离线模型配置、来源文档；代码MIT，模型另有许可 | 中文条款、合并表格、物理页、坐标、图表保留与模型资源 | 优先验证的结构化解析候选，尚未运行模型，不能认定质量胜出 |
| PaddleOCR，SRC-12 | 官方README与Apache许可，OCR／文档处理能力 | 扫描页、中文表格、方向、识别置信与页码映射 | OCR候选；按真实扫描子集比较，不默认OCR所有文字层页 |
| MinerU，SRC-11 | 官方README与实际LICENSE.md，布局／图表／内容提取 | 中文复杂标书、GPU／CPU成本、版本和页级出处 | 强候选，未实测；当前快照为带附加条件的Apache体系许可，不沿用网上旧许可判断 |
| Unstructured，SRC-21 | 元素分块、分区与部署说明、许可 | 文档元素映射、OCR与依赖规模 | 连接器／分区候选，需统一证据契约后对照 |
| PDFium／pdfplumber等文字与渲染工具 | 标准文字层／页渲染方法；仅作为独立底层通道比较 | 原页一致性、旋转坐标、文字漏页、抽取和渲染不一致 | 可以提供快速文字通道及原页核验，不等于已理解图纸和复杂表格 |

先制定统一的页与证据契约，再在相同资料上比较解析器：条款、分值／单位、表格关系、图页内容、原页定位、失败覆盖、延迟和资源。PDF页码以物理页为准，印刷页码另存。文字可搜索不代表图表理解完整；所有候选都必须暴露未解析范围。

本次未下载OCR／布局模型权重，未给出解析质量排名。解析器选择属于实装前门槛，不以框架自带演示样例替代公司标书验证。

## 7. 检索与索引

| 方案 | 成熟能力与限制 | 本产品取舍 |
| --- | --- | --- |
| PostgreSQL＋pgvector，SRC-18、23 | 关系过滤、事务、精确向量与HNSW；官方说明ANN先取候选后过滤可能少返回，提供迭代扫描 | 首选。业务、版本、引用和检索元数据保持一个事务域；小集合优先精确检索，ANN需与精确基线比较 |
| Qdrant，SRC-24 | payload过滤、混合检索、专用向量服务 | 向量规模或隔离需求超出数据库实测预算时再比较；多一个索引同步和恢复边界 |
| OpenSearch，SRC-25 | 成熟搜索与聚合生态；混合能力需要具体插件与配置核验 | 词法检索复杂度、规模或搜索运营需求显著增加时优先比较；当前未运行插件，不声称已验证中文效果 |
| Milvus，SRC-13 | 专用向量数据库及多检索能力 | 同样是规模化候选；重新选型后不因过去列入部署就继续保留 |
| FlagEmbedding／BGE，SRC-22 | 向量和重排工具及模型接入 | 中文embedding与reranker候选，模型权重许可、部署和实际收益需逐个验证 |

首选路径：结构化过滤 → 中文词法／编号精确召回，按证据增加向量召回 → RRF融合 → 可选重排 → 主库复核版本、用途和时效 → 构建引用包。PostgreSQL内置ts_rank不称作BM25；中文分词需单独实现并固定版本，技术词／数字／单位另做精确通道。

实测pgvector使用5000条合成向量和1%筛选命中，固定HNSW计划暴露候选不足，再验证迭代扫描与精确基线。该实验只证明机制与过滤行为，不代表标书检索质量或生产吞吐。检索质量需要人工相关性标注，报告召回、原页准确性及不适用内容混入率。

## 8. 评分学习与评测

scikit-learn（SRC-16）的Pipeline、GroupKFold和时间划分方法是成熟基础；MAPIE（SRC-17）提供区间与风险控制工具。它们不提供投标分数的通用真值，必须以本公司的实际提交稿和正式技术成绩建立监督。

推荐先比较均值／中位数／评分办法分组基线、规则逐项判断、线性／保序／岭回归，使用训练折内的预处理与调参。逐项标签缺失时不能把总分复制给各项。只有复杂模型在固定独立项目上持续胜出，才增加梯度提升或微调。

合成实测显示，近重复组分散到随机划分时误差远低于整组留出；这验证评测程序容易受相关样本影响，不是产品精度。正式方案采用项目／模板／重复正文连通分组，加时间留出，并将派生知识与锚点纳入相同隔离。

Conformal区间需要校准数据与适用假设。项目相关和时间漂移会破坏简单交换性假设；不得只引用“分布无关”就承诺90%覆盖。有限校准组过少时，所需次序统计量可能超出样本数，应拒绝有限窄区间，不能硬夹为最大残差。

## 9. 追踪、模型与推理服务

| 方案 | 能力 | 边界与取舍 |
| --- | --- | --- |
| MLflow，SRC-14 | 实验、评测、模型版本、别名与追踪API | 推荐用于实验管理；业务路由固定具体模型摘要和验收记录，不把可变alias当发布授权 |
| Langfuse，SRC-15 | LLM tracing、提示与评测生态 | 若LLM诊断体验显著优于已有观测，作为备选；避免首个交付同时部署两套追踪平台。MIT核心与EE目录分开核对 |
| OpenTelemetry GenAI，SRC-20 | GenAI链路语义与可观测约定 | 作为通用链路层候选；约定演进需固定版本，默认不记录原件全文和标签到外部观测服务 |
| vLLM，SRC-27 | 自托管模型推理生态 | GPU资源和数据条件允许时比较；模型权重、并发和工具调用兼容须另验 |
| DeepSeek V3，SRC-28 | 官方模型说明、代码与权重许可 | 模型候选参考，不能把开源模型README等同于当前云API能力或正式评分效果 |

模型服务通过供应商适配器接入，先验证JSON／工具输出、中文证据、上下文限制、稳定性、延迟和费用，再选择业务模型。模型型号、权重／服务配置和提示版本记录到每轮输入。本次没有配置或调用真实模型，因此没有模型质量、费用或精度排名。

## 10. 从调研到架构的结论

推荐总体为独立领域服务＋明确的后台执行机制＋PostgreSQL17／pgvector＋私有对象存储，前端使用React组合工作区和可替换聊天组件。首版固定任务优先对照成熟队列与可替换AI模块，持久工作流和官方AI运行时按需求及运行成本选择；执行器不在现阶段定死。检索与解析在统一契约下可替换；评分训练与业务发布隔离。

选择依据是业务事实有明确事务归属、运行机制有可验证恢复协议、小团队比较完整维护成本、评分遵守成熟实验方法。Redis在队列或Agent Server路线中按实际职责采用，不与Kafka、专用向量库、第二运行时或图数据库一并默认引入。

尚未验证：完整平台的真实运行；中文embedding／重排收益；解析模型质量；真实技术分误差；生产Temporal、对象存储直传、完整容量与恢复。推荐不是这些能力已经验收的声明。关键实验与反对意见见[技术方案验证与自审](../04-验证与测试/全新技术方案验证与自审.md)。

## 11. 官方来源索引

下表链接固定到实际读取的提交。成功与失败获取记录、时间和SHA-256见[official-sources.json](../04-验证与测试/architecture-checks/official-sources.json)。固定提交用于研究可追溯，不是部署开发分支的建议。

| 来源 | 官方仓库固定快照 | 已取得资料 |
| --- | --- | --- |
| SRC-01 Dify | [1e311fa39d74](https://github.com/langgenius/dify/tree/1e311fa39d74ff572d9a359f2c9c26c2dcb4126c) | README.md、LICENSE、api/core/rag/retrieval/retrieval_methods.py、api/models/dataset.py |
| SRC-02 RAGFlow | [fede451994fa](https://github.com/infiniflow/ragflow/tree/fede451994fa43dc64a4c9ec375071ea1d91e65d) | README.md、LICENSE |
| SRC-03 FastGPT | [b996c0c0db7f](https://github.com/labring/FastGPT/tree/b996c0c0db7f9bfeffa6259ccd465342f92413a8) | README.md、LICENSE |
| SRC-04 LangGraph | [e77a5cb08c1f](https://github.com/langchain-ai/langgraph/tree/e77a5cb08c1f3ff306d997dd76fc7169afdab5ea) | README.md、LICENSE、libs/checkpoint-postgres/README.md、libs/langgraph/langgraph/types.py、libs/checkpoint/README.md、libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py |
| SRC-05 Temporal Python SDK | [346d4dcd0863](https://github.com/temporalio/sdk-python/tree/346d4dcd0863657c3a33283c952a296762059339) | README.md、LICENSE |
| SRC-06 Haystack | [aca9fc25d9d2](https://github.com/deepset-ai/haystack/tree/aca9fc25d9d20816facdb9bd347d7eae7636d4b2) | README.md、LICENSE、haystack/components/retrievers/in_memory/bm25_retriever.py、haystack/core/pipeline/base.py |
| SRC-07 LlamaIndex | [619d74664e84](https://github.com/run-llama/llama_index/tree/619d74664e8404612038bc15a1a40c686037277c) | README.md、LICENSE、llama-index-core/llama_index/core/ingestion/pipeline.py |
| SRC-08 assistant-ui | [d420f4248753](https://github.com/assistant-ui/assistant-ui/tree/d420f4248753f44352f102f498ce2d542945af55) | README.md、LICENSE |
| SRC-09 Vercel AI SDK | [8a5233b15b1e](https://github.com/vercel/ai/tree/8a5233b15b1e69573d63faee6cd0014559252ee4) | README.md、LICENSE、content/docs/03-ai-sdk-core/15-tools-and-tool-calling.mdx、content/docs/04-ai-sdk-ui/02-chatbot.mdx |
| SRC-10 Docling | [5f4509767647](https://github.com/docling-project/docling/tree/5f4509767647721a8ec011ce6a3a6a39b19bb1f0) | README.md、LICENSE、docs/usage/advanced_options.md、docs/concepts/docling_document.md |
| SRC-11 MinerU | [b7f3dd0c5f6e](https://github.com/opendatalab/MinerU/tree/b7f3dd0c5f6e6d66a298239ffd02b328b873918e) | README.md、LICENSE.md |
| SRC-12 PaddleOCR | [dab3fe353790](https://github.com/PaddlePaddle/PaddleOCR/tree/dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf) | README.md、LICENSE |
| SRC-13 Milvus | [0ebf9f2d1d24](https://github.com/milvus-io/milvus/tree/0ebf9f2d1d24275b36de3b81afb55e374b656f92) | README.md、LICENSE |
| SRC-14 MLflow | [2e0e0457887b](https://github.com/mlflow/mlflow/tree/2e0e0457887bd4560105ca9ad226a2da73c4f6c9) | README.md、LICENSE.txt、mlflow/tracking/client.py |
| SRC-15 Langfuse | [857186a4cc6f](https://github.com/langfuse/langfuse/tree/857186a4cc6fba661c8d26eee1222ab2d667b8ba) | README.md、LICENSE |
| SRC-16 scikit-learn | [d0e22aee7bbe](https://github.com/scikit-learn/scikit-learn/tree/d0e22aee7bbef7b310a2f08227c214ff6df9a526) | doc/common_pitfalls.rst、doc/modules/cross_validation.rst、COPYING |
| SRC-17 MAPIE | [2c678f523f38](https://github.com/scikit-learn-contrib/MAPIE/tree/2c678f523f3857a2d4290995b25f476cb94c0cb2) | LICENSE、README.md、mapie/regression/regression.py、mapie/regression/quantile_regression.py |
| SRC-18 PostgreSQL | [86848d19bdbb](https://github.com/postgres/postgres/tree/86848d19bdbbf0daac6ca1b92a8885179cae8db5) | doc/src/sgml/mvcc.sgml、COPYRIGHT |
| SRC-19 Kafka | [f7a6dfe07692](https://github.com/apache/kafka/tree/f7a6dfe076929c3b761bfb9fbb97f786ff9503e8) | clients/src/main/java/org/apache/kafka/clients/consumer/KafkaConsumer.java、clients/src/main/java/org/apache/kafka/clients/producer/KafkaProducer.java、LICENSE |
| SRC-20 OpenTelemetry GenAI | [331541c4057c](https://github.com/open-telemetry/semantic-conventions/tree/331541c4057c53d133c32efadaa7b656636eda77) | docs/gen-ai/README.md、docs/gen-ai/gen-ai-spans.md、LICENSE |
| SRC-21 Unstructured | [d4008b99a12f](https://github.com/Unstructured-IO/unstructured/tree/d4008b99a12f2f417f3270711bd37d89d0d7f1be) | README.md、LICENSE.md |
| SRC-22 FlagEmbedding | [fd1a2bdf6948](https://github.com/FlagOpen/FlagEmbedding/tree/fd1a2bdf69488ffebe0327999d4400d8c8058a0b) | README.md、LICENSE |
| SRC-23 pgvector | [f37c13f68b57](https://github.com/pgvector/pgvector/tree/f37c13f68b57d2c3472b2214fbcff699d6d34876) | README.md、LICENSE、src/hnswscan.c |
| SRC-24 Qdrant | [016542aa5deb](https://github.com/qdrant/qdrant/tree/016542aa5deb6c66380bb137badf73d54f742bde) | README.md、LICENSE |
| SRC-25 OpenSearch | [feaa13b2f4bd](https://github.com/opensearch-project/OpenSearch/tree/feaa13b2f4bdb2e6c680e2446eb1f2896186ec9a) | README.md、LICENSE.txt |
| SRC-26 Temporal CLI | [4d3598e31381](https://github.com/temporalio/cli/tree/4d3598e313812b21fdecf9b3053c720e6326c046) | README.md、LICENSE |
| SRC-27 vLLM | [074c83a09a0b](https://github.com/vllm-project/vllm/tree/074c83a09a0b872cec92e244d91adf2b3c55e41b) | README.md、LICENSE |
| SRC-28 DeepSeek V3 | [9b4e9788e4a3](https://github.com/deepseek-ai/DeepSeek-V3/tree/9b4e9788e4a3a731f7567338ed15d3ec549ce03b) | README.md、LICENSE-CODE、LICENSE-MODEL |
