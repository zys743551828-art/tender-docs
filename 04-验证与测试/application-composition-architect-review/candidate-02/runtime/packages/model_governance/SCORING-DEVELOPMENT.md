# 专用完整评分判断执行器：软件候选01

本片实现独立评分输入、任务账本、完整章节三判断和影子快照；尚未接入共享main或启用消费者。正式模型资格、实际收费执行和数值业务预测均未开放。

## 实际接口

实际router为`apps.api.scoring_routes.router`，实际OpenAPI扩展为`packages.model_governance.scoring_contracts.extend_openapi`。共11操作：能力、项目上下文、冻结输入／读取输入、创建任务／读取任务／暂停恢复取消、读取结果、影子上下文／保存影子／读取影子。写命令使用会话、CSRF、强If-Match、幂等。HTTP没有模型发送或认证颁发端点。

当前输入只能从服务器实际冻结的当前review或已封口HistoricalInput构建。原始root从对象存储在事务外读取，UTF8原字节、SHA和长度绑定review_inputs实际manifest blob；SQL核完整身份、完整规则行／complete coverage、材料／原件／最新解析／1..N页与证据以及typed原冻结来源闭包。后加规则、仍published但修订变化或客户端自哈希root均不能洗成原review资格。事实集、未支持图纸和BOQ保守拒绝，不静默漏读。

历史分支保留真实原实交字节与submitted_at，当前创建retrospective输入，不补造当年review，不回填created/obtained/known。已结束或归档项目只经已确认HistoricalInput权威创建独立研究产物，不重开或移动原项目／实交／current_review。普通current_review冻结、预留和发布始终受关闭、归档守卫。

## 专用用途与费用

新SQL为`deploy/sql/score-execution.sql`草案F，SHA为`28e9a2f5042e29078c24a20d2191ada9935034b745627e8b1a5c865b487ec5be`。必须架构审查后完整新建数据库，不热补旧检查库或运行库。

评分子进程只接收权限600的单键`MODEL_SCORE_DATABASE_DSN`文件，以及固定语言环境和`WORKBENCH_ENV_FILE=/dev/null`。实际LOGIN非super/bypass且无design schema USAGE，不持桶／provider／API／owner凭据。只可通过拥有的job、generation、fence、owner租约RPC取得干净章节，不能读取root、官方标签、证明、训练导出或任意call表。

`scoring_runtime.execute_job`没有显式transport时在claim前拒绝；当前所有实际执行均由固定测试辅助子进程显式注入原生DeepSeekClient+MockTransport，未发真实模型。每个必要完整包同一descriptor／prompt／schema独立3请求，均不含前次答案。复用的方法SHA为`276a2c953aa86d935d46aabba432f7503ab6ce8081a9375b490ecb2172fd4d48`，其formal_scoring_eligible=false不变；执行账本另记purpose=score，不伪造research run。

先预留后发送，max_output=4096/context=1000000严格匹配固定profile；保守峰时cache-miss上界。真实返回content/data/usage/响应ID／模型／finish_reason持久化，用量估价不冒充实际账单。unknown保留reserve且暂停，不换nonce重发；not_sent显式0估价但也不自动恢复。已发送请求即使来源撤回或任务取消仍按原call拥有身份结算，原代次不得发布旧成功。

独立不可变call_bindings只允许将精确request/profile／完整包的原成功实际call绑定当前章节。全局profile+包digest+replicate隔离未知计费，输入ID／任务ID／命令ID和必要集扩张都不能绕过。成功复用不新增发送、预留或费用；源与租约仍在当前预留／发布事务复核。所有HTTP与对象IO均不跨业务锁事务。

## 影子与限制

影子快照复用同一评分结果和已登记惰性JSON模型，排序锁住评分及模型相关全部bid后核源、模型和shadow route CAS。模型实际字节IO在事务外，结果以新不可变快照保存；暂停路由不能重算或覆盖旧快照，不能重放为仍有效。保存不发额外模型，不更改业务路由。

本SQL技术总满分仍强制null／未核验，因此features与预测都显式unavailable，影子数值也为null。独立TechnicalScaleAuthority仅另有未fresh草案／应用准备，不属于本候选通过范围。不能取官方标签、条款之和、最高已见分或默认100补分母。

没有正式模型certification发行能力，没有真实标签质量／精度验收、未来留出或线上20配对结论。合成StageA训练／registry／shadow只验证软件数学、角色和恢复。历史训练分组／四时点新模式、真实历史标签适配、预先冻结业务验收协议与certification仍待独立实现，不因本片或已有proof/registry完成而转绿。

## 验证证据

新fresh环境02加载16精确SQL、7独立限定LOGIN，无provider key、owner／CPU DSN在business profile，无Kafka。F真实PG／登录ASGI／独立用途子进程17项全部通过，纯软件43项通过；ruff、format、mypy、bandit通过。对象均memory，PDF使用真实本地CPU解析，所有模型为原生显式Mock。原E的6过4失败、42702别名冲突、原SQL字节和最小F修复均独立保留。

17项覆盖当前／历史完整发布、缓存0新费用、等价与扩大范围unknown隔离、规则partial/conflict双层拒、自哈希root拒、旧root效力头变更拒、score六类越权／RR、计算中来源撤回／取消与晚到用量、归档历史独立研究、真实合成StageA CPU模型→登记／shadow route→同结果持久影子／幂等／路由暂停后的历史保留。未执行真实Kafka、原生OSS或真实付费模型；这些门禁保持独立。
