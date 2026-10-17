# GitHub实现功能保留对照

## 1. 结论与证据范围

建议采用当前融合方案的产品结构，保留GitHub实现中减少操作、方便核验、支持反复试算和评分实验的具体行为。GovDash式章节工作区承接编制工作；现成PDF的快速评审、风险优先和评分研究继续独立可用。

对照时逐项检查26组功能，15组的主要行为已在目标方案中覆盖，10组识别出协议或操作细节缺口，1组视觉偏好可以后置。“已覆盖”表示设计已表达，**不表示新系统已实现或已通过业务验收**。同一组可能包含多个相互关联的操作，本清单不用于计算功能完成率。

十项细节的当前设计落点与开发准入统一见[开工检查](../04-验证与测试/development-readiness-checks/README.md)。下表及机器清单保留对照时的差距判断，不作为当前未完成清单；具体协议以详细设计主记录为准。

| 对照基线 | 固定身份与使用范围 |
| --- | --- |
| [tender-app](https://github.com/zys743551828-art/tender-app/tree/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d) | `88d35bb6a7e5e65a3870fe2f3cc103529a0f649d`，后端、前端、契约和测试定义 |
| [tender-research](https://github.com/zys743551828-art/tender-research/tree/007107df49b2dba60846b4d3855c1808b67c9cc6) | `007107df49b2dba60846b4d3855c1808b67c9cc6`，资料入库与评分评测实现 |
| 目标产品 | [工作台PRD](AI投标工作台PRD.md)，产品行为主记录 |
| 目标技术 | [详细技术设计](../05-技术与设计/AI投标工作台详细技术设计.md)及其[实现规约](../05-技术与设计/detailed-design/README.md) |

远端提交已与本地Git提交核对；比较读取固定提交的源码，排除本地未提交的七个前端文件。测试只作为已有行为的规格证据，本次没有重新运行应用测试、真实模型或浏览器链路。历史运行结果见[验证报告](../04-验证与测试/验证报告.md)，不得作为新实现的验收结果。

## 2. 逐项保留判断

表内“补齐”对应第3节的设计待办；源码和测试定位见各行证据链接及[机器可读清单](../04-验证与测试/feature-preservation-checks/retention-matrix.json)。保留产品行为，不要求复用旧代码、旧数据身份或旧部署。

| 编号 | GitHub中值得保留的行为 | 对用户的价值 | 对照时设计判断 | 代码／测试 |
| --- | --- | --- | --- | --- |
| R01 | 上传齐备后自动解析、评审、生成报告；失败步骤可恢复 | 上传后直接看到结果 | 已覆盖：PRD 3.5、7.2 | [实现][R01-C]／[测试][R01-T] |
| R02 | 否决风险优先，只给一个主要下一步，按钮直达具体问题或矩阵 | 不需要自己找先做什么 | 已覆盖：PRD 3.7；当前输入冲突优先于旧报告 | [实现][R02-C]／[测试][R02-T] |
| R03 | 响应矩阵把违反、检查失败和无法判断放前面，满足项收起，规则就地确认 | 先看需要处理的条款 | 补齐D05：PRD已表达四类状态，逐行接口仍欠执行状态 | [实现][R03-C]／[测试][R03-T] |
| R04 | 证据点击即看原PDF；无可信坐标就显示整页；中文字体与CMap同站部署 | 能核验，也适合内网 | 已覆盖：证据及坐标原则保留，前端交付清单应包含本地PDF资源 | [实现][R04-C]／[测试][R04-T] |
| R05 | 分片并行、仅补传未完成片、签名过期刷新、重试复用幂等键 | 大文件失败不用重传全部 | 补齐D01：已有授权、完成和状态查询，分片及刷新协议不足 | [实现][R05-C]／[测试][R05-T] |
| R06 | 用户指定“作为某文件的新版本” | 修改稿不会变成无关联的新文件 | 补齐D02：不可变版本已有，显式逻辑文件归属仍缺请求字段 | [实现][R06-C]／[测试][R06-T] |
| R07 | 重新抽取后沿用未变条款的人工决定；批量确认尚未处理的条款 | 减少重复逐条确认 | 补齐D03：目前只有单条决定，沿用条件和批量操作未落到协议 | [实现][R07-C]／[测试][R07-T] |
| R08 | 撤回错误事实生成新事实集，保留旧集与理由 | 能纠正工期、数量等错误事实 | 补齐D04：事实决定不能完整替代事实撤回及新集操作 | [实现][R08-C]／[测试][R08-T] |
| R09 | AI引文逐字核验，新增数字检查，拒绝项记录原因 | 便于发现AI编造与漏处理 | 补齐D06：保留核验思路，增加结构化数值与失败明细 | [实现][R09-C]／[测试][R09-T] |
| R10 | 确定性检测相同工程量冲突、长段重复与范围残留 | 常见错误不用完全依赖模型 | 已覆盖：PRD 7、10；保留主体、型号、属性、单位的区分 | [实现][R10-C]／[测试][R10-T] |
| R11 | 问题可确认、驳回、暂缓、重新打开，人工处理留理由 | 能处理误报和再次出现的问题 | 已覆盖：PRD 10.1；已解决须绑定新稿复检 | [实现][R11-C]／[测试][R11-T] |
| R12 | 冻结评审输入，保留每轮文件、规则、事实身份 | 旧报告可以还原 | 已覆盖：PRD 4、10；新版两轮差异需独立实现 | [实现][R12-C]／[测试][R12-T] |
| R13 | 提交前核验正文与评审是否匹配；登记实际稿件 | 防止用旧报告放行新稿 | 已覆盖：PRD 10.3；补录已发生事实走独立命令 | [实现][R13-C]／[测试][R13-T] |
| R14 | 七个项目开关；可选模块关闭后不再催办 | 轻量用户也能用 | 已覆盖：PRD 7.4；所有输出渠道一致隐藏 | [实现][R14-C]／[测试][R14-T] |
| R15 | 完整Word评审报告与原PDF批注副本 | 方便线下修改和协作 | 已覆盖：PRD 15.2；与投标正文区分产物身份 | [实现][R15-C]／[测试][R15-T] |
| R16 | 报价规则带来源预填，实时曲线、多候选、技术分换算、手动保存历史方案 | 直观看不同报价的影响 | 补齐D07：目标只有单候选试算和保存，曲线与历史查询不足 | [实现][R16-C]／[测试][R16-T] |
| R17 | 否决风险闭环和逐轮风险、预测走势 | 能复盘改稿是否改善 | 补齐D08：PRD保留，MetricsView暂只有比率及MAE等汇总 | [实现][R17-C]／[测试][R17-T] |
| R18 | 业务模型和影子模型分别记录结果 | 比较新模型而不干扰正式预测 | 补齐D09：影子观察已有文字要求，输入绑定与结果契约不足 | [实现][R18-C]／[测试][R18-T] |
| R19 | 批量预测先估调用与费用，限制样本和并发，缓存续跑，单样本失败不中断全批 | 评分实验可控、可反复开展 | 补齐D10：通用预算和恢复已有，研究批次预览与恢复契约仍不足 | [实现][R19-C]／[测试][R19-T] |
| R20 | 资料表校验、预览不写入、重复正文识别、相近模板分组与异常清单 | 历史资料可靠进入新系统 | 已覆盖：PRD 11.3、13；缺失值和真实零分分开 | [实现][R20-C]／[测试][R20-T] |
| R21 | 冻结分组及时间划分、基线对照、分层误差、失败覆盖和独立测试 | 能判断模型有没有真实进步 | 已覆盖：PRD 9、14；保留研究侧实现中的严格评测约束 | [实现][R21-C]／[测试][R21-T] |
| R22 | 模型登记、适用范围、业务发布与撤销路由 | 新模型不合格时可以停止使用 | 已覆盖：技术设计10；新建系统首次无模型也可回退到不可用 | [实现][R22-C]／[测试][R22-T] |
| R23 | 网络失败原样重试复用操作键；内容改变创建新动作 | 少重复提交，错误后能继续 | 已覆盖：实现规约的幂等、ETag与操作查询；保留用户输入 | [实现][R23-C]／[测试][R23-T] |
| R24 | 明暗主题和跟随系统 | 长时间阅读更舒适 | 可后置：作为视觉偏好，不阻塞风险与评分链路 | [实现][R24-C]；未找到专项测试 |
| R25 | 键盘操作、窄屏卡片、图表对应文字或数据 | 高频操作更顺手，手机也能核对 | 已覆盖：PRD 16；保留验收方式 | [实现][R25-C]／[测试][R25-T] |
| R26 | 同字节内容去重，证据按原文物理顺序展示 | 避免重复样本和阅读顺序错乱 | 已覆盖：PRD 7.1；去重不合并不同来源身份 | [实现][R26-C]／[测试][R26-T] |

## 3. 应补齐的十项细节

下表保留对照识别出的设计待办与验收要求；当前接口、字段和事务落点见[保留功能实现规约](../05-技术与设计/detailed-design/保留功能实现规约.md)。P0用于核心评审、事实安全及评分实验；P1随辅助模块交付，不延后两个P0主目标。设计落地不表示软件已实现。

| 待办 | 优先级与目标落点 | 需要写清的协议和验收条件 |
| --- | --- | --- |
| D01 上传续传 | P0；UploadGrant、UploadView、上传接口，A1-01 | 明确单次／分片模式、part number／大小／ETag、已完成片查询、重授权、终止和过期清理。完成时按片序核验，摘要由服务端复算。验收覆盖中间片失败、签名过期并发刷新、完成响应丢失后的幂等重试。旧实现只证明同会话补传，不宣称跨浏览器重启恢复；跨会话恢复需另行授权和校验文件摘要。 |
| D02 逻辑文件与版本 | P0；UploadRequest、资料head，A1-01 | 明确逻辑文件身份和“本文件新版本”命令，校验同bid、角色与当前revision；同名文件不自动归组。旧版本不改写，新正文使旧报告标过期。验收覆盖错标段引用、并发上传、同字节重复及外部Word改稿。 |
| D03 规则沿用与批量确认 | P0；RuleView、决定接口，A1-02 | 批量确认携带当前rule set和decision revision；只操作明确选中的待确认项，返回确认、跳过及冲突项，重复请求不重复审计。沿用必须同时检查条文、性质、条件、例外、适用范围及生效来源关系；新增、实质变更或无法证明等价的条款重新确认。保留原决定来源和操作人，不把沿用记成用户刚确认。旧实现仅按性质和去空白条文匹配，不能直接照用。 |
| D04 事实撤回 | P0；事实写入／撤回命令、FactSetView，A1-02 | 明确人工补事实、改事实和撤回的独立入口；撤回生成新集并移动head，旧集不变。带理由、来源、revision和幂等键；相关报告标过期，新建议应用前重查事实head。验收覆盖重复撤回、旧集并发更正和撤回后旧AI补丁应用失败。不填事实也可开始快速评审。 |
| D05 矩阵逐行执行状态 | P0；MatrixRow、响应矩阵，A1-03 | 在业务verdict外增加独立执行状态及错误原因，区分未开始、运行中、成功判定、执行失败；不能仅靠verdict=null推测。任务成功但证据不足表达“无法判断”，模型／解析失败表达“检查失败”；总覆盖不能替代逐行信息。验收覆盖零条成功、单条失败、无法判断和部分覆盖，均不得显示全部满足。 |
| D06 引文与工程数值核验 | P0；规则提取、AI补丁与结构化校验，A1-02／A2 | 保留逐字引文、允许来源和拒绝明细。工程量使用主体、属性、数值、单位、适用范围核验；覆盖中文数字、单位换算、允许的确定性计算，区分章节编号与工程事实。来源里出现过同一数字不代表该工程断言成立。失败项记录原因、影响范围和可重试性，不能静默删除后显示完整成功。 |
| D07 报价曲线与版本 | P1；PricingSimulationView、方案查询接口 | 增加候选点／受限价格区间、曲线与计算条件；规则预填须能回看出处，用户已有输入不静默覆盖。试算不落库，显式保存追加方案版本，支持历史列表与指定版本查看；新输入后旧曲线标“待更新”，防抖保留显示不等于结果仍适用。技术分换算注明条件，全部Decimal，不引入成本利润或必中价。 |
| D08 风险闭环及轮次走势 | P1；MetricsView、项目复盘查询 | 补识别、解决、驳回、提交时仍开放和官方否决数；已解决与误报驳回分别计数，提交时状态取冻结快照。走势绑定每轮正文、规则、方法与量尺，方法变化明确断点；隐藏预测时仍可看风险数量。正式误差只取合格事前预测与已核验官方成绩，分母和未知数可见。 |
| D09 影子比较 | P0；实验、路由、预测研究记录，C-02 | 指定业务和影子artifact、相同冻结输入、各自method／feature schema、运行及失败身份；影子不改业务route或展示分，不增加独立官方样本数。方法输入不同时明确比较口径。影子失败不阻断业务结果，发布仍需独立验收与人工命令，不能在线挑最接近官方成绩的模型。 |
| D10 批量实验预览与恢复 | P0；实验运行命令、研究批次查询，C-01／C-02 | 执行前冻结样本manifest、范围、重复次数、并发及价格配置，显示调用／token／费用估算与上限，估算不调用付费模型。每样本及replicate有状态、失败和调用账；失败隔离，恢复只执行未完成项，变更方法另建运行。缓存身份必须包含用途、fold、完整输入和profile，禁止通过弱缓存跨用途或跨划分复用。 |

## 4. 保留能力时应改掉的旧行为

| GitHub实现的边界 | 当前方案应坚持的处理 |
| --- | --- |
| `ai/predict.py`的item_context按14,000字符挑选章节部分内容 | 正式评分读取完整相关章节，超长分包并记录必读范围；普通聊天RAG片段不替代评分输入。 |
| 三次判断全部失败返回满分一半，且有对应测试 | 输出失败／不可用与原因；不得把故障变成0分、满分一半或旧分。旧测试不作为新验收标准。 |
| 三次输出档位范围合并为原始区间 | 保留独立判断及分歧记录；有统计含义的区间需单独校准和覆盖验收，不能称为已验证置信区间。 |
| 应用度量的`verification_status <> 'conflict'`会包含待核验成绩 | 正式误差严格使用已核验官方结果及合格事前预测。研究仓库已有更严格筛选，不能把两个仓库口径混为一谈。 |
| 引文核验主要忽略空白；数字检查只是阿拉伯数字集合差 | 保留可解释、可追踪的守卫，增加D06的语义边界；不宣称正则已解决事实核验。 |
| 规则沿用只匹配性质与去空白条文 | 使用D03的完整等价条件；工期、例外、补遗适用范围变了就重新确认。 |
| 原有缓存与锚点身份不足以覆盖全部新用途／实验边界 | 保留缓存续跑和受控比较，按新方案加入用途、祖先闭包、分组、fold、完整章节和方法身份。 |

上述边界的固定代码与测试位置同样收录在证据清单。没有真实标书和官方成绩独立评测，不能据此宣称旧方案或新方案预测更准。

## 5. 产品与实施取舍

最值得继承的是让人少找入口、少重复确认、出错后能接着做的行为。结果页继续给一条风险结论与一个主要动作；完整矩阵和原文随时可展开；规则确认放在提交前，已有PDF无需先配置知识库或在线编辑器。

章节编制、随页助手、混合RAG、知识发布与失效、图形化历史导入、细粒度两轮比较属于目标方案需要新建的能力。旧仓库中有检索适配和部分入口，不能把关键词降级实现、接口名称或文档规划当作这些能力已完成。Kafka也是原仓库已有的基础设施设计；新系统应按当前消费、事务、fence和重试协议独立实现。

实施时先补D01—D06，保障最小评审链路；D09、D10随评分研究并行，避免实验只能靠手工脚本；D07、D08在报价和业务复盘工单中补齐。主题切换可后置，键盘与窄屏核验保留基本验收。实际实现必须同步主记录、目标契约与对应测试；本次对照不新增旧库映射、旧接口兼容或双系统切换工作。

证据文件和静态核对方法见[功能保留证据说明](../04-验证与测试/feature-preservation-checks/README.md)。

<!-- 固定提交引用 -->

[R01-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/pipeline.py#L241
[R01-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_auto_pipeline.py#L202
[R02-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/report.py#L58
[R02-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_report_actions.py#L58
[R03-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/report/ComplianceTable.tsx#L20
[R03-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/report/ReportPage.test.tsx#L210
[R04-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/evidence/PdfViewer.tsx#L35
[R04-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/evidence/PdfViewer.test.tsx#L41
[R05-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/upload/uploader.ts#L107
[R05-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/upload/uploader.test.ts#L173
[R06-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/files.py#L78
[R06-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/upload/uploader.test.ts#L107
[R07-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/ai/handlers.py#L54
[R07-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_rule_carry_over.py#L56
[R08-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/rules.py#L457
[R08-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_facts_and_confirmations.py#L54
[R09-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/ai/grounding.py#L42
[R09-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_ai_pipeline.py#L76
[R10-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/checks/consistency.py#L69
[R10-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_checks.py#L27
[R11-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/findings.py#L133
[R11-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_review_fixes.py#L202
[R12-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/snapshots.py#L153
[R12-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_review_fixes.py#L47
[R13-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/outcomes.py#L127
[R13-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_review_fixes.py#L97
[R14-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/schemas.py#L93
[R14-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_auto_pipeline.py#L550
[R15-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/export.py#L176
[R15-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_auto_pipeline.py#L302
[R16-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/price/PricePage.tsx#L97
[R16-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/price/PricePage.test.tsx#L191
[R17-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/metrics.py#L94
[R17-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_auto_pipeline.py#L390
[R18-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/ai/predict.py#L292
[R18-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_predict.py#L145
[R19-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tools/batch/predict_dataset.py#L1066
[R19-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_batch_predict.py#L104
[R20-C]: https://github.com/zys743551828-art/tender-research/blob/007107df49b2dba60846b4d3855c1808b67c9cc6/src/tender_research/intake.py#L868
[R20-T]: https://github.com/zys743551828-art/tender-research/blob/007107df49b2dba60846b4d3855c1808b67c9cc6/tests/test_intake.py#L261
[R21-C]: https://github.com/zys743551828-art/tender-research/blob/007107df49b2dba60846b4d3855c1808b67c9cc6/src/tender_research/score_eval.py#L470
[R21-T]: https://github.com/zys743551828-art/tender-research/blob/007107df49b2dba60846b4d3855c1808b67c9cc6/tests/test_score_eval.py#L245
[R22-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/model_registry.py#L102
[R22-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_model_registry.py#L35
[R23-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/lib/useIdempotentAction.ts#L11
[R23-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/lib/useIdempotentAction.test.tsx#L16
[R24-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/lib/theme.ts#L30
[R25-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/features/report/ComplianceTable.tsx#L122
[R25-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/frontend/src/routes/App.test.tsx#L42
[R26-C]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/src/bidwb/services/evidence.py#L20
[R26-T]: https://github.com/zys743551828-art/tender-app/blob/88d35bb6a7e5e65a3870fe2f3cc103529a0f649d/backend/tests/test_evidence_order_and_uploads.py#L22
