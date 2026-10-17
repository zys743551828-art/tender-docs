# 相似AI产品技术路线核查

本文按投标响应、施工文档、企业知识工作台和有证据的研究流程选择相似产品，核查能够取得的技术资料。选取表示流程有参考价值，不构成市场领先排名。执行框架与完整运行路线比较见[AI执行与工作流选型复核](AI执行与工作流选型复核.md)，业务边界以[工作台PRD](../02-业务与产品/AI投标工作台PRD.md)为准。

## 1. 已取得的资料与研究缺口

证据分为两组：4个官方仓库的26份固定提交源码、配置、README和许可，见[源码清单](../04-验证与测试/architecture-checks/product-architecture-sources.json)；GovDash、AutogenAI、Trunk Tools、Document Crunch、Glean及微软、Google Cloud发布的14份网页原文，见[官网清单](../04-验证与测试/architecture-checks/product-web-sources.json)。网页于2026-10-16核实，清单记录机构、地址、页面日期依据、取得时间、原文SHA-256、核查主题及未证实范围。网页可能变化，内容摘要固定的是取得时的证据，不代表永久不变的产品规格。

源码用于核对公开模块的具体机制；官网、技术文档和云厂商客户文章属于公开披露，不能当作独立产品验收。未运行这些产品、调用真实模型或验证其生产表现；厂商的质量、效率和安全声明不转化为本项目效果承诺。

直接行业资料已可取得，但具体后台任务框架、完整恢复语义、部署总成本以及中国市政技术分预测能力仍有证据缺口。使用AWS基础设施不证明采用Bedrock，出现Agent不证明采用LangGraph，知识图谱也不证明采用某一图数据库。搜索摘要可能滞后于正文；技术判断以清单绑定的原文为据。

## 2. 可核查产品及路线

| 对象 | 与本产品相似的部分 | 核实到的技术路线 | 结论边界 |
| --- | --- | --- | --- |
| Onyx | 企业知识、对话、Agent、项目上传文件、引用与动作 | Python／FastAPI依赖、Celery后台任务；标准部署包含PostgreSQL、Redis、检索及对象存储组件；知识获取、处理、用户文件和清理分队列 | 证明这些公开模块的结构，不证明所有推理流程都用Celery，也不证明其拥有投标评分能力 |
| Refly | AI工作区、可视流程、可复用技能、执行产物和多模型提供方配置 | NestJS业务服务、Prisma／PostgreSQL、BullMQ任务队列与Redis、SkillEngine；数据模型单独记录WorkflowExecution和带版本的ActionResult | 不因依赖LangChain core就断言用了LangGraph；不假定公开版与商业线上部署完全相同 |
| Open Deep Research | 多步骤检索、研究、资料处理和报告形成 | LangGraph的StateGraph、研究与工具节点、配置化模型端口及独立评测入口；提供开发服务和平台部署指引 | 是公开参考实现，不能当作同规模企业产品的生产可靠性证据；研究报告基准不是官方技术评分基准 |
| Glean | 企业知识、对话、检索与Agent工具 | 当前文档披露索引／实时／混合检索、权限同步、Enterprise Graph和模型管理；旧MCP仓库只证明历史客户端调用方式 | 旧仓库已归档；公开文档不提供完整执行器、生产恢复或中国市政评分证据 |
| GovDash | 招标分析、响应矩阵、提案及Agent运行记录 | 当前官网披露按任务选模型、语义＋全文检索、来源关联、评测和人工复核；微软案例披露OCR及Azure OpenAI接入 | 2024年云厂商案例有时间边界；未披露具体任务队列或持久执行框架 |
| AutogenAI | 企业内容库、RFP检索与响应生成 | 按段或标题切分、embedding语义匹配、检索证据后生成并展示来源 | 提案写作的片段检索不证明满足本项目完整章节评分要求；未披露具体执行器和向量库 |
| Trunk Tools | 图纸、规范、RFI及跨文档施工审查 | Cortex披露施工本体、知识图谱、图纸视觉层和预制Agent | 训练与效果数字为厂商自述；未取得模型、训练集或编排框架验收证据 |
| Document Crunch | 施工文档风险识别、来源引用与项目行动 | CrunchAI、Project Assist与平台工作流分层；安全页披露部分AWS存储及密钥设施 | 不从AWS使用推定Bedrock；未披露底层模型或执行器 |

### 2.1 Onyx：知识产品依然有独立后台工程

官方README描述连接数据、元信息与访问条件后建立可检索表示，提供对话、研究、动作和模型提供方选择。质量及成本宣传只按厂商自述记录。

后台README与Celery代码把拉取、处理、用户文件、清理和监测划分为不同任务与Worker，另有协调、心跳和孤立任务处理。这说明使用成熟队列仍需产品自己的状态管理和故障处理，不能认为接上Celery即可省略运行工程。

标准版具有索引和后台组件；Lite模式被明确描述为不具备文档索引的轻量聊天体验，不能用Lite的资源声明代表完整RAG产品。许可区分MIT主体和EE目录，不能将所有企业功能当作自由复用内容。

### 2.2 Refly：画布、AI执行与业务记录相互配合

WorkflowModule注册BullMQ队列，Processor调用WorkflowService；Prisma schema分别记录执行身份、状态、结果与版本。前端画布和自然语言创建流程属于交互方式，后台仍需明确的API、队列和业务记录。

对本产品的启发是让用户用简单表达发起任务，复杂状态留在后台；无需把开发用的节点画布直接交给投标人员。当前投标、历史和知识的正式事实仍应由业务层管理。

本次实际读取的Refly许可对公司／组织商业使用有授权条件，不能简写为无附加条件Apache许可。这里只借鉴已核实的架构模式，不据此批准代码复用或部署。

### 2.3 Open Deep Research：图适合推理，评测仍独立

源码确认StateGraph、研究／工具流程及配置化调用；README具有独立评测入口。它支持将长研究拆为可检查步骤，体现可编排AI与评测分离的价值。

本项目可以借鉴有界检索、证据清单、结构化结果和独立评测。正式评分仍按完整章节输入、三次有效判断、真实成绩监督和防泄漏划分执行；不能把研究摘要替换成评分项完整阅读，也不能把外部研究基准成绩当作预测技术分的准确率。

### 2.4 Glean：当前检索机制与用途限制分别核对

旧MCP仓库的参数schema、SDK调用和文档读取工具只说明历史集成方式，README明确归档并推荐托管远程MCP。现行机制以[连接器文档](https://docs.glean.com/connectors/connectors-power-glean)（WEB-GLEAN-01）为据：内容、元信息和权限进入租户索引及Enterprise Graph；回答可结合索引、实时获取和混合模式，检索内容按权限过滤。不同连接器的实现不同，不能将产品级模式当成全部来源都支持同样的新鲜度。

[内容限制文档](https://docs.glean.com/administration/assistant/configuration/content-restrictions)（WEB-GLEAN-02）明确，用户可查看的内容仍可排除在Assistant和Agents的模型输入之外，配置生效可能需要最多一小时。这支持将“可访问”与“允许用于AI”分别控制；不能据此声称即时撤回已经满足本项目要求。[数据流入口](https://docs.glean.com/security/architecture/data-flow)（WEB-GLEAN-03）区分查询、接入和处理；[模型文档](https://docs.glean.com/administration/llms)（WEB-GLEAN-04）说明模型可用性受部署模式、供应商和密钥方式约束。以上都是产品文档证据，没有执行跨源权限、撤回或故障测试。

[Google Cloud客户文章](https://cloud.google.com/blog/products/data-analytics/glean-uses-bigquery-and-google-ai-to-enhance-enterprise-search)（WEB-GLEAN-05，2023-03-01）披露Dataflow处理与增强内容、GKE承载搜索索引、BigQuery分析及Vertex AI训练。它是特定时期的工程案例，不能代表当前所有部署或推断AI任务调度框架；本项目也不因对方使用这些服务就引入同等规模设施。

### 2.5 GovDash：提案工作流、检索和运行记录形成产品

[官方技术说明](https://www.govdash.com/blog/how-govdash-combines-llms-rag-and-agentic-ai-for-mission-success)（WEB-GOV-01）披露按速度、推理和写作任务选择模型；取得正文列出OpenAI、Google Gemini、xAI Grok，以及可选Anthropic。检索结合语义与全文匹配，生成内容关联要求和来源，自动评测覆盖工具调用、引文与回归。Agent有运行状态和产物记录，提交前保留人工复核。模型流量经Azure、AWS、Google Cloud企业端点处理属于厂商声明；未独立核验数据保留或供应商故障切换。

[微软客户案例](https://www.microsoft.com/en-us/startups/blog/govdash-modernizing-government-contract-workflows-ai/)（WEB-GOV-02，2024-06-18）具体介绍Azure Document Intelligence OCR、后续提示处理、Azure OpenAI和跨区域限流分配。该案例支持“解析、模型接入与业务流程共同建设”的判断，不能把2024年的Azure描述当作当前仅用单一云或单一模型的证明。两份资料均未披露Temporal、LangGraph、Celery或完整恢复协议。

### 2.6 AutogenAI：检索证据后生成，评分覆盖另有要求

[embedding说明](https://autogenai.com/apac/blog/how-do-embeddings-work-find-content-for-your-rfps/)（WEB-AUTO-01，2026-02-25）描述按段落／标题切分、生成向量表示、按RFP要求检索已批准内容，再生成响应并展示来源。它明确只取相关片段；这对提案起草有参考价值，不能替代本项目评分项完整章节包和覆盖清单，也不能将厂商“提高中标率”的说法当作真实评分证据。

[产品流程说明](https://autogenai.com/blog/winning-with-autogenai/)（WEB-AUTO-02，2025-11-05）称检索和处理在客户环境进行，再将相关文本和提示送去生成。因此“客户环境”不能理解为没有模型出口。[DPA](https://autogenai.com/dpa/)（WEB-AUTO-03）的分包方表按地区列出基础设施和模型服务；具体客户使用哪一路仍取决于合同及配置，不能凭列表推定每次调用经过全部供应商。这些资料没有公开具体向量数据库、队列或执行框架。

### 2.7 Trunk Tools：施工语义关系与图纸视觉是独立工程

[Cortex技术说明](https://trunktools.com/blog/trunk-tools-builds-cortex-purpose-built-ai/)（WEB-TRUNK-01，2026-06-22）和[平台页](https://trunktools.com/platform/)（WEB-TRUNK-02）描述连接规范、送审资料、RFI、图纸与进度的施工本体及知识图谱，视觉层处理图纸符号、空间关系、跨图引用和修订。不同工具基于同一项目关系处理任务，Agent之间可衔接产物。

对本项目可借鉴的是文档对象、补遗关系和图页证据需要明确表达，不能把工程图纸等同于OCR文本。厂商自述的标注规模、视觉指标和任务用时未独立复现；未取得模型权重、测试集、图数据库选型或后台执行器证据。现有样本量也不足以支持照搬其专用模型训练路线。

### 2.8 Document Crunch：风险解释、行动和平台流程分层

[平台发布说明](https://www.documentcrunch.com/news/document-crunch-launches-constructions-first-project-level-ai-risk-intelligence-platform)（WEB-DC-01，2026-06-09）将产品分成CrunchAI风险与来源解释、Project Assist跨文档分析及产物生成、平台标准与工作流三层。其项目文档集合包含合同、规范、批注和补遗，支持“风险可能存在于文档关系中”的产品判断；专有处理方法未披露到可重现算法。

[安全页](https://www.documentcrunch.com/security)（WEB-DC-02）披露S3、AWS负载均衡、KMS、Secrets Manager及Parameter Store等设施。这能证明厂商公开采用的部分云设施，不能证明使用Bedrock、某家基础模型或持久工作流。安全声明与真实运行、风险识别准确率分别验收。

## 3. 可迁入本产品的设计模式

| 模式 | 我们的落点 | 对执行器选择的影响 |
| --- | --- | --- |
| 产品业务层与AI流程分开 | 项目、实交稿、成绩、知识发布和验收是正式对象 | 不能用聊天记录、图状态或向量库代替业务主记录 |
| 文档接入和索引后台化 | 原件→解析证据→索引／章节包，保留质量缺口 | 成熟任务队列是实际可行路线，应与持久工作流公平比较 |
| 稳定工具／服务边界 | 查询、评审、改稿和登记经过类型化接口 | AI界面不绑定具体编排器，所有正式操作经过领域核验 |
| 分用途组织上下文 | 普通建议、正式评分、成绩复盘分别组装 | 全文、知识、历史与缓存都需要用途和来源边界 |
| 评测独立于日常会话 | 冻结输入、方法、标签与独立项目测试 | 换框架不等于精度提升，多模型方案仍按用户要求暂缓 |
| 访问权限与AI用途分别控制 | 成绩、评委意见及其派生摘要即使成员能读，也不能进入正式预测输入；检索、工具和缓存共用用途核验 | Glean提供产品级对照，本项目仍须验证端到端防泄漏和即时失效 |
| 多文档关系与图页证据显式保存 | 补遗覆盖、跨章引用、图页版本与覆盖缺口可定位 | Trunk Tools及Document Crunch支持该需求，不构成立即引入图数据库或训练专用视觉模型的理由 |

这些是所核查模块中能够提炼的模式，不是“全行业都采用某一种框架”的统计结论。Onyx和Refly提供成熟队列路线的现实对照，Open Deep Research提供LangGraph的推理编排参考；直接行业资料强化了证据、领域关系、受控模型调用和人工复核的必要性，没有提供本项目必须采用或必须排除Temporal的证据。

## 4. 对当前方案的判断

应先确定业务事实、证据、评分方法和任务接口，再决定执行底座。项目可能持续数月，人工决定可以保存为业务状态、确认后触发新任务；这不自动要求一条工作流始终等待。若只凭项目生命周期长就引入持久工作流，会高估Temporal的必要性。

对于首版少量固定AI流程，领域API＋成熟任务队列＋普通Python或有界LangGraph是应优先验证的轻量路线；生产上不优先完全自写PG队列。Python体系可以用Celery作队列候选，LangGraph承担需要分支和补查的局部图。队列重投、并发恢复、幂等与checkpoint衔接仍需按统一故障协议验收，不能照抄其他产品部署就认定成熟。

如果运行中明确需要跨任务持久等待、统一定时、复杂取消／补偿和运行历史重放，再比较Temporal带来的收益与维护成本；需要完整AI运行时且接受其部署和授权条件时，LangGraph Agent Server同样是完整候选。产品相似只提供判断依据，不能替代实际负载和故障验证。

直接行业披露可以支持业务与AI模块边界，尚不能决定具体执行器。正式架构主规格中的Temporal实现作为候选展开阅读；采用门槛以本节和执行路线复核共同约束，未验证能力不进入发布承诺。多模型路由是他方实现事实，不构成本项目启用多模型或增加资料出口的决定。

首个可验收交付仍是独立评分纵向链路：先核验10—20份实际提交正文、生效招标及补遗、技术分和出处，再形成完整章节包、三次结构化判断、可追溯特征与独立评测。现有案例库只有1例正文与用户报送成绩关联，官方出处待核验，不能开始真实精度验收。框架故障对照使用固定模型响应单独推进；行业产品数量和营销指标不进入评分验收结论。

## 5. 官方源码索引

| 来源 | 固定快照与关键文件 |
| --- | --- |
| PR-ONYX | [官方仓库](https://github.com/onyx-dot-app/onyx/tree/308ad098e4316ad9c3b7b1e2699fef7953b1a06f)；README、LICENSE、两个Compose文件、Python依赖、后台README与Celery基类 |
| PR-REFLY | [官方仓库](https://github.com/refly-ai/refly/tree/71f8b875c8751e881776749d8ed7a74383b65fb0)；README、LICENSE、API依赖、Prisma、Workflow模块及Processor、SkillEngine和部署配置 |
| PR-ODR | [官方参考实现](https://github.com/langchain-ai/open_deep_research/tree/1b7d2e80db9faa586165c60e09096dbbfd483a64)；README、LICENSE、pyproject、langgraph.json和研究图源码 |
| PR-GLEAN | [已归档官方仓库快照](https://github.com/gleanwork/mcp-server/tree/2cc472396bf642bc377f7546adc7bec7a3bbc090)；README、LICENSE、MCP依赖、搜索工具与SDK客户端 |

官网来源编号WEB-GOV、WEB-AUTO、WEB-TRUNK、WEB-DC、WEB-GLEAN分别对应第2.5、2.6、2.7、2.8、2.4节；逐页身份与未证实范围见[官网清单](../04-验证与测试/architecture-checks/product-web-sources.json)。没有明确发布日期的页面保留未知，修改日期不冒充首次发表日期。源码清单中的早期访问失败只作为历史观察，不代表当前不可访问。
