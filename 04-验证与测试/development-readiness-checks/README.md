# 开发准入检查

**可以开始全新系统开发。** 产品主流程、目标接口、初始化数据结构、固定任务恢复和首批组件已具备开工依据；开发顺序为T0底座→最小PDF评审闭环，RAG与受控评分研究在底座后并行。没有旧系统迁移或兼容工作。

这里的准入指可以编写和联调新系统代码，不是正式上线或正式模型发布许可。新系统业务服务、界面、真实模型效果及生产运行仍待实现与验收。

## 1. 唯一实现入口

| 主记录 | 开发读取内容 |
| --- | --- |
| [工作台PRD](../../02-业务与产品/AI投标工作台PRD.md) | 用户路径、风险优先、规则确认后置、评分与实际提交闭环 |
| [详细技术设计](../../05-技术与设计/AI投标工作台详细技术设计.md) | 模块、用途隔离、事务、Kafka和运行边界 |
| [详细协议](../../05-技术与设计/detailed-design/README.md) | 127个Schema、84条路径91个操作、101表的全新建库材料 |
| [保留功能实现规约](../../05-技术与设计/detailed-design/保留功能实现规约.md) | D01—D10的请求序列、边界、事务及验收条件 |
| [GitHub功能对照](../../02-业务与产品/GitHub实现功能保留对照.md) | 固定实现基线及保留依据；当时识别的差距由本目录追踪落地 |

## 2. 十项细节的设计落点

下表的“设计已落地”指协议与规约齐备，不代表业务实现通过；应用代码验收均待开发。

| 待办 | 机器契约／数据落点 | 实现规约章节 | 设计状态 |
| --- | --- | --- | --- |
| D01 分片与恢复 | UploadPartGrant／Receipt、UploadPartsView、authorizations／abort；upload_transfers／parts | 第1节 | 设计已落地 |
| D02 逻辑文件版本 | UploadTarget、DocumentView；document_files／file_versions | 第2节 | 设计已落地 |
| D03 规则确认及沿用 | BatchRuleConfirmation、RuleCarryoverView；rule_carryovers、decision_heads | 第3节 | 设计已落地 |
| D04 事实更正撤回 | FactChangeRequest、withdrawals；fact_heads、fact_set_changes | 第4节 | 设计已落地 |
| D05 逐行执行状态 | MatrixRow.check_status／error_code；matrix_checks | 第5节 | 设计已落地 |
| D06 工程断言核验 | ValidationDiagnostic、diagnostics分页；validation_diagnostics | 第6节 | 设计已落地 |
| D07 报价曲线版本 | PriceRange／Point、PricingPlanPage、版本GET；pricing_simulations／plans | 第7节 | 设计已落地 |
| D08 风险闭环走势 | QualificationMetrics、RoundTrendPage；冻结review／submission及决定 | 第8节 | 设计已落地 |
| D09 影子对照 | ShadowComparisonRequest／View、比较入口；shadow_comparisons | 第9节 | 设计已落地 |
| D10 批量实验控制 | ResearchPreview／Run／Item、恢复／取消；experiment_previews、research_runs／items／attempts | 第10节 | 设计已落地 |

## 3. 开发顺序与完成标准

1. **T0底座**：新工程目录、包锁、契约生成、空库初始化、登录、统一命令、ETag、操作查询、Outbox与Kafka消费者。退出条件是质量门禁及实际事务、重放、失联、取消回归通过。
2. **最小PDF闭环**：建项目→批次上传招标与正文→自动解析评审→风险矩阵与原页→上传修改稿复检→标准提交登记；不依赖完整编辑器。D01—D06随这条路径实现及回归。
3. **RAG和评分研究并行**：解析分块、Embedding、向量＋词法、重排、引用失效；完整章节和三次独立判断；受控样本、费用预览、续跑和影子比较。正式评分禁止读取聊天与事后标签。
4. **报价与业务复盘、编辑器与知识维护**：按模块独立交付；不因辅助模块齐备推定评分精度通过。

开发使用合成夹具和明确标注的测试适配器可以推进；正式路径未配置模型／存储时必须显示未配置，不返回脚本化成功。真实存储与代表性文档应在最小闭环联调期尽早接入。

## 4. 检查证据与边界

结果文件分别记录输入摘要、检查范围与软件执行边界；历史implementation-design-checks及feature-preservation-checks的结果保留原样，不将旧统计改成当前通过。

| 检查入口 | 证明的范围 |
| --- | --- |
| `../detailed-design-checks/check_contracts.py` | JSON Schema、OpenAPI一致性、合法／非法夹具、54项需求落点与本地链接 |
| `check_retained_design.py` | 十项保留行为的新增协议夹具、必要端点、条件头和设计落点 |
| `../implementation-design-checks/probe_database.py` | 在真实PG＋pgvector执行当前全新建库及原有隔离、租约等机制 |
| `probe_retained_sql.py` | 当前新增版本外键、不可变更正记录、逐行状态、预算预留及研究角色边界 |
| `../prd-checks/check_prd.py` | 产品文档与用户后续修改链；软件场景执行数仍为0 |

运行命令及实际结果在[result.json](result.json)登记。这些检查不覆盖真实浏览器上传、业务API、实际Worker、真实OCR／Embedding／RAG效果、评分精度、多节点Kafka或生产存储。

已执行范围见各结果：契约359项、新增协议80项、原机制PG检查27项、新增PG约束与预算检查22项；产品文档核对通过，软件场景执行仍为0。PG检查包含并发预算预留只允许一个成功、取消后禁止新增预留，以及真实超额账单可以记录并暂停；不代表实际模型计费已经联调。

在本目录复现，Python须安装implementation-design-checks的锁定验证依赖；两个probe需要已缓存的固定digest镜像和Docker：

```bash
python ../detailed-design-checks/check_contracts.py --output contract-result.json
python check_retained_design.py --output retained-contract-result.json
python ../implementation-design-checks/probe_database.py --output database-result.json
python probe_retained_sql.py --output retained-database-result.json
python ../prd-checks/check_prd.py --check-review-rounds --output product-result.json
python verify_readiness.py
```

verify_readiness.py核对所有结果的当前输入摘要、引用及开发结论；文件变更后必须重跑受影响检查，不手改摘要使之通过。

## 5. 后续门槛

| 门槛 | 何时必须完成 | 现在是否阻塞写代码 |
| --- | --- | --- |
| 正式对象存储、模型供应商、内网模型资产与密钥配置 | 对应真实适配联调前 | 否；端口、配置及未配置行为已定义 |
| 首批真实标书、实交正文、官方成绩与满分／出处核验 | 检索效果与正式评分独立验收前 | 否；限制精度结论与模型业务发布 |
| 生产容量、多节点Kafka、备份恢复及安全运行演练 | 生产上线前 | 否；不能用开发单节点代替验收 |
| 页面可用性与完整端到端回归 | 对应交付试点前 | 否；不能用协议检查代替软件验收 |

开工后按每张工单同步正式实现仓库契约、代码和测试。本文不触发仓库推送、合并、生产部署或模型发布。
