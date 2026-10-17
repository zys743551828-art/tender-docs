# 助手独立意图与聚合预算SQL静审

结论：**SQL02存在两项明确RPC约束缺口，暂不fresh执行；当前有据身份的调用方守卫也需闭合。** 输入SHA256 `ea53f1fe28afefea9fc81c0ad0b71f6c3ef8c9e804994521d71f2aef64b34403`。本轮只读精确SQL及产品协议，使用轻量纯逻辑反模型，没有运行DDL、数据库或收费调用。没有把静态反模型称作已走通实际API/PG的漏洞。

## AB01 / P1模块阻塞：空核清参数可跳过校验并清零预留

定位 [intent-budget.sql](/workspace/tender-docs/04-验证与测试/assistant-budget-development-checks/sql-protocol02/intent-budget.sql:316)。核清RPC没有显式拒绝decision、amount、proof_reference和proof_sha为NULL。SQL OR/AND含NULL会得到UNKNOWN，PL/pgSQL的IF只对TRUE执行拒绝。

触发：已有unknown/sent预留，native执行终态；传decision=NULL、amount=0、proof_reference=NULL、proof_sha=NULL。非法条件为UNKNOWN，校验跳过；第328行CASE落ELSE settled，charged_minor=0、settled_at非空且manual=false，表级约束允许。事件JSON也允许null证明。该能力只授管理员组，未声称普通API越权；问题是受限核清入口允许没有完整可信决定/证据的核清。

修正：所有必填参数显式IS NULL拒绝，状态和金额组合完整校验，证明引用/摘要有明确边界；原native记录和审计保留。验收：对每个必填参数的NULL独立调用拒绝且预留/原账本/事件不变，合法三类核清正向仍执行。轻纯证据 [countermodels.py](countermodels.py) 与 [结果](countermodels.json)。

## AB02 / P1模块阻塞：预留价格与实际native输出上限/方法未绑定

定位同文件第211—225行。dispatch验证同job/native call存在、running/live lease/cancel和预算政策，但未读取native profile_digest/max_tokens，也未把它们与冻结价格provider/model及reserved output_upper比较。global call只通过原表FK约束与run.max_tokens一致，不能证明等于job.input_manifest.request.max_tokens。

关系反模型：同job/call合法，按2048输出上限预留，native run/call实际8192；其余本函数条件均成立。合法参数下可得到预留2148 minor、原native实际最坏8292 minor，而政策限额5000；预留阶段通过，dispatch没有一项表达式能拒这个上限差异。相同结构也缺少“flash价格不能授权pro方法”的数据库关系。该反模型说明受控RPC自身未保证不超预留，未证明当前Python正常路径会构造这种不一致。

修正：服务器冻结执行profile/descriptor、模型与最大输出，派发时把精确native call的profile/max_tokens/逻辑步骤及当前执行身份与该冻结值和价格版本核对；不可只靠调用方自报或事后usage超界才追账。检查job.scope/bid与目标scope/bid同一身份，并确认receipt属于当前代次/步骤；正常业务层已有的fence守卫应在同一短事务闭合。验收：同job的错误profile、模型价格、输出上限或错误step native call在任何发送前拒绝，正常精确匹配只派发一次；未知费用不因新profile重发。

## 需闭合的当前身份权威条件

同文件第95—118行的assistant_check_target校验当前identity绑定、fact_head、confirmed决定和值/证据ID集合，但未读取当前source_heads、精确依据资格、人工确认时basis/等价沿用映射。静态读集反模型见 [观察](authority-readset-observation.json)：保持这些查询读取的事实/头/决定/值不变，只撤回某事实证据的源，该函数结果不会变化。不能把“当前head且confirmed”描述为已经核过“当前有据”。

若这一层由Python承担，必须在API受理和Worker发送授权的同一短事务中重核完整fact identity/live evidence_basis/decision basis、精确来源与祖先，并与被冻结证明一致。独立新目标还须与真实job的bid/scope绑定。该守卫的实现/契约在本轮SQL中未给出，不应在静审后直接开启独立付费能力。不要为补检查随意增加Worker表权限；可用服务器冻结映射与最小受限资格桥。新增bid/图锁需先统一锁序，避免原engine bid/op路径与90129预算串行锁反向等待。

## 已读的有效机制与后续验收

90129全局事务锁串行预留/派发/结算/核清，coarse目标+用途使同目标文本改写/换profile不直接解锁；section复制摘要及finding沿用根/规则人工根有额外保守关联。预算obligation对held/sent/unknown使用原reserved上限，跨窗口继续计入；多币种冲突拒绝，政策新修订/窗口改变在派发前拒绝。不能由这些静态判断声称同一问题已具通用语义去重。

observe_usage会先保存实际usage事件，再尝试结算，终态/已核清也保留观察；冲突恢复unknown+sticky manual，普通settle不自动清flag。not_sent释放有明确native或无native且执行终态依据。仍须真实测试并发扣三桶、迟到usage冲突、核清后新hold与旧hold并存、超冻结上限时全局余量是否继续可信；当前保留原最坏hold不等于供应商支出硬封顶。

权限用途分离：API只能插入逻辑意图并调reserve/settle；Worker只派发/观察/结算；专用NOLOGIN管理员持价格/政策及受限核清能力，无普通业务表直读写。所有新增函数撤销PUBLIC执行并固定search_path。fresh时需核该已存在角色确实NOLOGIN且无危险flag/外部成员，独立LOGIN仅其用途组、私有单DSN，业务环境剔除；不能因CREATE ROLE IF NOT EXISTS而假定已有同名角色安全。

新稿静审后才能由PM全新检查库初始化；后续非owner实PG还应验证NULL、价格/native精确关系、身份来源撤回、角色越权、币种、并发、旧无上限预留拒付费和迟到usage持久性。当前未审核最终Python发送接线、actual接口、前端可达性或账单对账。
