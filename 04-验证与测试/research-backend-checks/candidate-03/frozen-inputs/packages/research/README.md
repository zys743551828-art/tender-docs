# 历史资料与评分研究后端

本模块建设新系统的历史资料、实交与成绩配对及受控研究执行。历史文件先通过系统上传核验，批次只引用本系统的原件身份；没有旧系统迁移或模型资格承接。

## 实际能力

- 历史资料预检查、逐记录异常、显式确认、跨批次相同实交内容复用。确认只形成`offline_fact`和待核验成绩，不补造标准提交授权或当时预测，不自动准入训练。
- 成绩绑定实际提交。未知、综合总分、真实技术零分分别保留；追加更正移动有效头，旧记录可读，不能退回旧的已核验分掩盖当前冲突。
- `outcome-context`从服务器预填项目、轮次、主体和实交稿身份。核验需对应原件证据、聚合口径、公布与取得时间、生效招标补遗；部署主体占位名称不取得正式核验资格。
- 样本准入重新检查当前提交头、当前成绩头、人工核验、技术分和满分。相同项目及原件摘要自动形成连通组；模板、近重复来源由有依据的人工核对归组。自动近重复识别未实现。未经归组核对不得冻结独立实验；组改变暂停受影响批次。
- 冻结数据和划分清单，检查整个相关组及时间边界，锚点默认关闭。标签只在监督数据清单中；实际模型请求单独从规则和完整章节白名单组装。
- 首个章节方法保守读取冻结正文全文，包括全部物理页和证据。解析、表格或图页存在缺口则拒绝完整评分；超过当前生成端口字符限制则明确拒绝。可追溯子节分包与跨节汇总尚未实现，不静默截断。
- 离线价格预览、独立研究批次、逐样本和整轮重复计划、并发与RPM/TPM调度、失败项有限恢复、取消、过期租约收口。成功项不会随恢复重做。
- 同一章节包三次独立调用、三个调用账本身份、独立原始响应、逐字引文和档位校验。每项整轮失败如实保存，不能用零分或旧输出补齐重复数。

## API及数据边界

入口在`apps/api/research_routes.py`；主API通过`include_router`集成。实际OpenAPI补充函数为`packages.research.contracts.extend_openapi`，只修正已经注册的路径，不增加未实现路径。源文件中的实际路径优先于任何历史记录。

使用已冻结的`outcomes/outcome_heads/samples/experiments/experiment_previews/research_runs/research_run_items/research_run_attempts/chapter_packages`表。核验和人工归组使用追加式`decisions`及`decision_heads`，不修改冻结DDL。研究结果保存在独立研究产物，不写业务评分、模型路由或官方成绩。

API使用独立API LOGIN；执行使用`RESEARCH_WORKER_DATABASE_DSN`的非owner研究LOGIN。该角色不能读取`outcomes`。普通Worker的领取和对账必须排除`research/1`处理器。

## 研究运行配置

价格快照通过`RESEARCH_PRICE_CONFIG`注入，只接受以下字段：`profile_digest/currency/input_per_million/output_per_million/max_output_tokens/context_tokens`。模型配置身份唯一来自`packages.research.profile.descriptor()/digest()`。当前配置是远端`deepseek-flash`别名、官方版本观察及尚未验证不可变权重的明确状态；不是已获业务资格的模型。

预算按冻结的峰时、未命中缓存输入价格作保守上界，不猜测缓存命中或优惠时段。成功请求的usage按价格估算记为`usage_price_estimate`，不冒充供应商确认账单；是否已计费未知的请求保留预留费用并暂停，必须先对账，不能自动补发。确认usage估算超出预算如实记入并暂停，不篡改账目。

CLI为`python -m packages.research.worker schedule|consume|reconcile`。`schedule/consume`只有显式配置`RESEARCH_MODEL_EXECUTION_ENABLED=1`才运行；确认研究批次本身不会偷偷构造模型请求。部署需要三个专用进程，另复用同一数据库的Outbox relay。topic为`tender.task.research.v1`，group为`tender-research-worker-v1`，handler为`research/1`，step为`research-judgment`。不得在机制测试库启动Kafka消费者，因为测试库可能复用开发broker配置。真实环境须使用同一隔离数据库与broker，不能混用另一个数据库的任务。

`RESEARCH_RPM/RESEARCH_TPM`是应用调度上限，须根据账号能力部署；它们不是已验证的供应商配额。默认保守上限会在预留之前等待调度窗口，未发请求的调度等待不算供应商重试。已发请求没有隐式重试。执行进程仅在受控预留成功后调用已有DeepSeek生成端口，不存在独立绕账本请求。

## 验证与未完成范围

开发自测位于`tests/test_research_core.py`和`tests/test_research_integration.py`。后者显式设置`RUN_RESEARCH_INTEGRATION=1`，使用独立真实PG、真实会话/CSRF和受限研究角色；原件使用显式内存存储、模型使用原生客户端配`httpx.MockTransport`，不发送外部收费请求。合成资料和合成账单只验证机制。

本模块不宣称真实样本、真实准确率、真实用户试用、生产容量或模型业务发布通过。统计模型拟合、完整独立评测及人工业务发布仍需要后续实现与真实资料、冻结协议、观察和回退证据。当前正式总分、区间与模型发布资格始终为`unavailable`；没有标签不伪造精度，也不阻止合格的核心核对和实交登记。

## 候选02：来源资格与远端I/O

候选01的价格参数可低估实际执行上界、撤回来源后仍可执行两个缺陷由独立架构评审复现；01证据保持原样。02严格要求价格配置的上下文和输出token参数与唯一模型descriptor一致，不允许相同digest搭配较小预算参数。

新环境需从初始化基线安装`deploy/sql/research-source-qualification.sql`。它新增API受控的两张只含身份、摘要、head和时间的追加式映射表；研究角色没有扫描或写权限。受限资格函数从实际operation/step/generation/fence/owner以及run/preview/sample/submission/review推导全部章节，检查精确parse与evidence、完整祖先、当前head、生效范围及typed as_of。只允许READ COMMITTED短事务，拒绝旧快照；返回bool/reason及绑定摘要，不返回任意源正文或官方标签。缺少新基础时能力显示未配置，执行/冻结返回`RESEARCH_AUTHORITY_NOT_CONFIGURED`（503、不可自动重试），不热补既有运行数据库。

API构造来源身份复用RAG的canonical original/parse helper。预留和成功发布均在自己的短事务中重核服务器绑定来源，图锁保留到本次effect提交；模型调用、对象存储GET/PUT均在数据库事务结束后执行。章节、数据集、原始响应与研究结果先stage实际不可变字节，再短事务复核并登记。资料变化产生的未引用stage对象不成为有效结果，后续只可按独立对象清理规则回收。

02新增合成反例覆盖：token配置低估、正文根撤回后零新请求、结果stage期间撤回后拒绝发布并保留三次已知调用账、精确parse错配、Worker无映射表权限、错误章节身份、REPEATABLE READ拒绝，以及API/研究账号在manifest读写和模型回调期间无idle-in-transaction。测试使用全新独立PG数据库与非owner角色，不启动共享Kafka配置的消费者/relay/coordinator。正式模型资格仍不可用。

## 候选03：准入归组与实交冻结身份

独立架构评审进一步复现：新资料准入能改变已有相关组，候选02仅人工归组路径暂停批次，准入路径未暂停。03不覆盖02证据；准入与人工归组统一重建连通组，并按旧、新组影响闭包暂停批次。预览新增API-only追加式`research_preview_samples`映射，冻结完整样本集合、组身份、人工归组决定与实交身份；Worker不能扫描或写入。

所有研究数据库事务先取得统一组协调锁。`lock_research_run_scope`受限函数从服务器已冻结的preview映射推导全部项目，按bid排序取业务锁；调度、领取、预留、结果提交及对账均在操作/run锁之前取得该范围。资格函数重核完整预览集合，不能仅靠`run.state`或当前item；当前项目之外的身份更正也不会在资格与effect提交之间穿过。模型、GET、PUT均在连接之外。已执行请求的usage照实结算，身份或来源变化不抹掉已知账单。

正式准入要求真实实交`evidence.identity`有明确confirmed快照，且其可信身份authority、完整关联及当前明确身份仍一致；历史`at_time`仅是审计观察，不单独授予样本资格。没有冻结身份的资料仍可登记、保留未知/待核验成绩，不能拿当前profile反推原实交。当前实现暂不从仅含身份的旧review root远端补读证据；仅root有身份而实交evidence缺失时保持unavailable，后续可增加独立字节核验预取流程。身份接口使用独立identity候选02（动作时钟与单调epoch）的原样snapshot，不改其字段或重命名工程ID。

03验证必须在再次架构静读通过的新初始化环境执行，不向02库或release库热补SQL。软件反例通过仍不代表真实标签、统计模型精度或正式发布资格。
