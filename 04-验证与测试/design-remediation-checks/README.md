# 设计整改独立验证

**独立验证通过，可以进入实现。** 结论绑定技术候选02与最终产品输入；没有执行新应用，不授予业务试点、正式模型或生产运行资格。

本目录由测试角色独立维护，区分整改前反例、整改后设计契约／数据库机制和待执行的应用验收。检查只使用合成记录；不修改既有评审证据与历史结果。

## 1. 检查边界

| 发现 | 独立预期 | 执行层次 |
| --- | --- | --- |
| A01 | 非owner、非superuser的实际LOGIN账号能够按既定锁序领取和完成；研究费用与调用账本同时提交／回滚；重复、取消、过期租约及迟到fence不产生新付费或发布权；标签、人工决定和业务模型路由仍隔离 | 真实隔离PostgreSQL执行合成事务，不是应用Worker |
| A02 | frozen_at变化不改变内容身份，nonce只改变执行身份；有业务效力的as_of、来源和版本变化改变内容身份；明确规范化；不同入口可在数据库锁和唯一约束下共用运行 | 独立固定向量／契约与真实PG并发机制，不是已调用业务API |
| A03 | 两个基于同一head的提交仅一个先成功；失败者保留核验字节，通过显式可幂等命令重新绑定后再做CAS；跨bid、角色、过期及已提交对象拒绝 | 契约与真实PG合成提交协议，不是浏览器上传 |
| PPR-01／02 | 关闭检测不消除已确认关键问题；范围覆盖、已识别行执行、人工确认及评分覆盖分层；纯评分缺口与核心失败分别处理 | 先核对规格，应用验收待执行 |
| U01／02 | 下载用途可辨认；DOCX转换失败后可就地补PDF且明确评审对象 | 先核对目标规格，真实浏览器／用户验收待执行 |

## 2. 整改前基线

`baseline-inputs/`固定架构评审反例实际执行的SQL、Schema、API与规约字节；`baseline-inputs.json`登记摘要。`baseline-probe_architect.py`和`baseline-probe_contract_paths.py`由原反例脚本仅改输入／输出位置得到，原脚本亦保存在基线目录中。

已复现：5个Worker合法路径的42501权限反例；两个合法manifest仅frozen_at不同而全文摘要不同；BatchCommit无法接收重新选择文件目标的字段。详情见[角色基线](baseline-results/role-counterexamples.json)与[契约基线](baseline-results/contract-counterexamples.json)。反例成功复现不代表缺陷已修复。

## 3. 应用与用户验收

[应用验收任务](application-acceptance.json)列出PPR-AC-01—06及U01—U05的任务、预期和所需环境，状态均为`pending_application`，实际软件执行数为0。它们不由字符串匹配或数据库探针替代；正式模型精度另依赖真实官方标签与独立留出验证。

所有新执行结果应记录执行脚本摘要及实际执行输入摘要。冻结后主文件变化，结果只对冻结输入有效，不能手改摘要让历史结果绑定当前文件。

## 4. 最终结论与证据

[最终身份核对](final-result.json)核验技术候选02的25份源文件及9份结果；输入清单SHA-256为`d9e53659504a3f5dcb527266582495294de738fc92c036c97987e669d12175e4`。产品人工复核绑定最终PRD与交互，不将旧产品阅读结果改绑新文件。

| 结果 | 实际证明 | 实际数量 |
| --- | --- | --- |
| [Worker LOGIN](final-worker-login-result.json) | 实际非owner LOGIN完成规定行锁、合成acquire/finish及研究付费预留；失败整体回滚、同key复用、并发预算、generation/fence/owner/lease/取消拒绝；审计/Outbox/SSE/产物权限与越权隔离 | 42项 |
| [业务时点](score-asof-result.json) | 评分LOGIN按as_of筛选而非frozen_at；known_at与有效截止边界、未来祖先及当前撤回继续生效 | 5组 |
| [身份与上传并发](final-identity-upload-result.json) | 真实PG中共用执行身份、nonce独立、版本竞争、显式绑定链及二次CAS可实现 | 21项 |
| [身份与恢复契约](final-identity-contract-result.json) | 独立计算固定摘要向量、时间与集合规范化、显式上传绑定和人工补漏契约 | 36项 |
| [契约回归](final-contract-regression-result.json) | Schema/OpenAPI一致、合法/非法形状、HTTP示例、54项需求落点及本地引用 | 380项 |
| [保留契约回归](final-retained-contract-result.json) | 原十项保留功能的契约与禁止形状 | 80项 |
| [基础PG回归](final-database-regression-result.json) | 来源/祖先、合成向量与词法过滤、评分用途隔离、租约及模型路由约束 | 27项 |
| [新增PG及人工来源约束](final-rule-origin-database-result.json) | 版本/事实/矩阵/预算原检查及人工规则自根、跨bid、不可变更正记录；空库实际建104表 | 30项 |
| [产品规格独立核对](final-product-spec-result.json) | 开关与已知关键风险、四层覆盖、三状态交付、下载用途、DOCX恢复、人工补漏及重提取保留语义明确 | 人工规格核对；软件0次 |

A03的`qa.commit_upload`与`qa.rebind`为测试侧合成SQL驱动，证明给定DDL支持所述原子与CAS协议；跨bid/角色/过期拒绝仍须由真实领域用例实现并重新验收。A02三个入口同样为测试连接模拟，没有调用RequestReview API。A01的锁与研究调用预留使用设计提供的实际数据库函数，acquire/finish由测试事务驱动，没有运行业务Worker。

本轮额外发现并实际复现了[研究调用NULL身份比较反例](nullable-call-counterexample.json)：普通调用的空研究关联曾被错误复用。候选02以`IS DISTINCT FROM`修正，最终Worker检查已加入对应拒绝用例。架构复审发现的业务as_of被审计时刻替代，也已纳入上述5组独立回归。原失败和中间候选结果保留其原输入，不作为最终通过依据。

原检查器的ReviewManifest、RuleView及SQL夹具与新增必填字段不一致；本目录`regression_*.py`只适配这些夹具、字段位置和表数，不改历史检查器或历史结果，也不删原有断言。最终检查均绑定实际执行的SQL/Schema字节与脚本身份。`final-02-*-inputs`、`final-asof-inputs`和`final-product-inputs`保存冻结输入。

## 5. 复现与后续验收

Python依赖使用既定验证虚拟环境；PG探针使用已缓存固定digest镜像与隔离Docker。先运行`probe_worker_login.py`生成最终SQL快照，两个SQL回归脚本读取该快照；其余脚本按各自命令行帮助指定`--snapshot`和`--output`。`probe_score_asof.py`直接输出本目录结果。完成后执行：

```bash
python verify_final.py
```

验证器检查最终源清单、结果摘要、实际输入身份和待执行用例状态，不能通过手改历史hash将失败或旧结果变成当前通过。新源码实现后重新执行实际API/Worker、对象存储、浏览器及产品/架构/用户review；13条应用与用户任务保持待执行。真实OCR/Embedding/检索效果、正式评分精度、生产恢复及多节点故障均未验证，本目录不声称已经通过。
