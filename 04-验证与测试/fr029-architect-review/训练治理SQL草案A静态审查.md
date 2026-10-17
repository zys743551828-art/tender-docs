# 训练治理SQL草案A静态审查

结论：需先修正，当前不批准新库初始化。仅静读、未执行SQL；以下为具体控制流／身份映射反例，不冒称真实PG探针。输入SHA256 `c476f8877f9d9fa9def79476175a43be29c00f2bb8d539baa0d2148ad59cb38b`，按Owner保全的draft-A原字节核验，副本observed-model-governance-A.sql。当前工作树B已移动，本报告未审B。

## MG01：assert_lease锁后未重读当前job

assert_lease首次SELECT job无锁，在current_cohort可能等待全部bid/图锁之后，只有PERFORM锁住当前running行，仍使用最初job.generation核lease并RETURN旧job。触发调度：读取generation1→等待来源锁→API将该job generation改2且保持running（当前列授权允许）→解除等待→当前行仍running→旧lease.generation1与旧局部job.generation1相等，旧fence被误认有效。claim_job本身有锁后重新SELECT，assert_lease没有。

影响：read_training/read_final/open_final/finish_job依赖此函数，旧执行代次可能读取或提交。建议current_cohort后SELECT…FOR UPDATE INTO job，重新核kind/用途/状态，以真正当前generation比lease。验收：cohort等待期间并发换代后，全部旧token/fence读取／finish拒绝，合法新代可执行；续租不改变执行身份。

## MG02：current_cohort未实现协议承诺的精确parse/evidence及完整关联守卫

成员及source_bindings只核来源头，未绑定原件/parse/page/evidence；函数不检查研究03已要求的最新parse和证据对应关系。同原件新增latest parse，而原root和旧parse child头保持published/原revision时，当前条件仍成立，旧解析产物继续被判current。成员关联也仅比bidder_id/round_id，未复核研究03完整association投影（工程、地区、专业、标段／轮次等）；相同UUID的关联内容变化无法识别。

影响：训练/评测可能继续消费被当前解释或身份上下文替代的研究产物，与协议“沿用研究03精确parse/evidence资格”不一致。建议绑定API-only chapter authority/cp或最小typed映射，复用不依赖活跃研究operation的纯资格核验，重查真实原件/parse/物理页/evidence/全祖先及完整identity authority/association；不扩大CPU角色原表或对象能力。

验收：新parse、旧child挂新evidence、跨页／跨项目证据及同UUID关联变化均使cohort失效，合法原清单通过。若允许旧解析作历史训练，应先明确用途语义并调整协议，不能由实现默默降级。

## 静态有效范围

三个用途组NOLOGIN、score零业务权限，train/eval无表读写及bucket key、只经owned lease RPC；函数固定search_path且PUBLIC撤销。RC、91143→全bid固定序→graph方向合理。exposures.created_xact xid8加read_final拒当前顶层事务，在MVCC下要求先提交暴露再读标签：打开后同事务回滚不能先取标签。全局sample/group/project/body/template/near唯一键防换实验/manifest重开。以上仍须fresh库LOGIN、并发、回滚实证。

API-only export须可信用途分类，不能仅以purpose字段证明JSON内无final标签。合成来源不可改、模型/报告/训练结果绑定、已提交曝光异常后不清除、取消／租约恢复均需实证。当前无正式发布函数或score权限，不授业务资格。Owner已接受两项静态问题，准备新B；A源与本结论保留，不热补既有库。
