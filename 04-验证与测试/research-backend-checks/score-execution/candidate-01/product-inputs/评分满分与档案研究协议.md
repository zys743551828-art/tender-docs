# 评分满分、档案研究与任务草案协议

本文件由产品Owner落实PRD第5.1、8.5、9.1及13.4节。新增字段、资源与用途仍待技术Owner给actual Schema／API／受限SQL和独立验证；不执行DDL、模型或费用调用。既有SQL B的technical_maximum仍为null，直到本协议所述有效权威实际接通，不能临时从条款或标签补数。

## 1. 技术总满分的最短用户路径

评分缺总满分时，原位置给“核对技术总分满分”，直接打开本项目生效招标／补遗原页及可信选择列表。预填提取候选、已知工程／标段轮次、量尺与缺口；用户核对明确的总满分、原页与聚合说明，点一次“确认技术总分满分”。来源不足时可“保存待核对”，表单与选页保留，不要求先重新评审、建知识或进入研究配置。

用户不填写UUID；官方技术成绩、综合分／价格分、最高已见分、条款满分总和和模型建议都不是此处的事前满分依据。“技术权重40%”不自动等于“技术总满分40分”，官方成绩表的100分不反向证明招标采用100分量尺。确认的是原评分办法中的技术总分量尺，不确认正文应得分或模型精度。

## 2. TechnicalScaleAuthority最小字段

| 信息 | 具体字段／约束 |
| --- | --- |
| 版本与归属 | id／version_id、bid_id、engineering_project_id、lot、round_id、bidder_id及可信identity authority；score_object固定为technical_total；用途上下文另列，不用一个最新ID覆盖不同轮次／用途 |
| 满分／量尺 | technical_maximum为有界、有限且>0的Decimal字符串；scale_description保留原技术分量尺、是否含权重及已明确换算规则。权重或目标不明保留pending，不猜100或其他数 |
| 聚合 | aggregation.kind、parameters、description及依据；状态明确known／unknown／unsupported。可描述官方最终总分、平均／去极值／加权等原口径；机器只执行已支持且字段齐备方法，不执行任意表达式 |
| 精确原文 | evidence_refs[]含官方招标／补遗document_version_id、blob_digest、parse_id、physical_page、evidence_id、source_id／revision、逐字quote、supported_fields。总满分、量尺、聚合分别能指出支持的原文，不以同页无关数字充数 |
| 当前用途 | current_prebid：当前material快照／修订及身份；historical_backtest：指定submission_id、历史Subject authority、来源claim heads及historical_as_of=真实submitted_at。先注册满分再由HistoricalInput引用，无需先造一个缺满分的假输入 |
| 决定／历史 | created_by／created_at、confirmed_by／confirmed_at由服务端产生，reason、supersedes_id、有效头修订和强ETag；state区分pending／confirmed／needs_revalidation／conflict／withdrawn／superseded |
| 读投影 | maximum_usable、aggregation_usable、current_applicability／reasons、公式出处与历史版本链接。模型业务资格独立，不让confirmed布尔值等于正式可评分 |

可确认总满分而聚合仍unknown／unsupported：读投影显示“满分已核对，聚合口径待核对／暂不支持”。有据满分可作同量尺覆盖分母；无可执行聚合不能合成正式总分。没有可靠原文总满分则maximum_usable=false、technical_maximum=null，不以填满表单取得资格。

## 3. 最小实际资源与请求

以下为建议actual形状，未注册前不能称已可用。全部写请求认证、CSRF、幂等和对应当前强If-Match，actor只取会话；强令牌由读取返回，不让前端拼。

| 动作 | 接口与保存语义 |
| --- | --- |
| 查看可信上下文／选择原页 | GET /bids/{bid_id}/technical-scale-context，返回用途允许的identity／material或历史Subject／claim、可信原件/页/字段支持与当前缺口、ETag；不能让客户端自己指定不同项目的页 |
| 一次登记／确认 | POST /bids/{bid_id}/technical-scales，输入intent=save_pending或confirm、上下文、满分／量尺／aggregation、evidence_refs、reason。confirm成功同事务追加新版本、当前用途头和人工决定；不再第二次聊天确认 |
| 列表／回看 | GET /bids/{bid_id}/technical-scales及GET /technical-scales/{id}；可查用途／历史范围和current_applicability／原因；回看不按最新总分重算旧产物 |
| 更正 | POST /technical-scales/{id}/corrections，当前头令牌、base_version_id、完整新内容与reason；追加替代版本，旧评审／研究输入按影响过期，不改旧manifest |
| 撤回 | POST /technical-scales/{id}/withdrawals，当前令牌及理由；追加决定使新用失效，原版本／引用可还原，不物理删除 |

来源／lot／round／主体／量尺／聚合／上下文有效头改变，不能证明同一适用范围时needs_revalidation或conflict；新的官方补遗必须有据处理，不按最后上传猜满分。无关资料变化可重核仍等价，不要求用户重复审批。严格当前用途与历史用途的有效头独立，不能按最新创建时间把历史权威塞到当前项目评分。

错误quote、源与parse错配、跨项目／标段轮次、失效来源、依据只是官方标签、单位／量尺不明或并发冲突整笔拒绝confirm，保留输入；pending可保缺口，不部分保存成已确认。更正、撤回与冻结竞争须同一权威版本一致，要么冻结合法旧版本且后续标过期，要么拒绝冻结，不能两边用陈旧预检通过。

## 4. 时点、数值覆盖与模型资格

current_prebid及严格未来模式仍按既有来源实际known、当时身份和输入／训练协议资格，不能借历史claim回写系统时间或绕过未来边界。历史用途在现在确认该精确招标／补遗对当年submission的适用满分，confirmed_at现在、历史内容边界另记，只经HistoricalInput专用proof分支使用。公开日期不自动证明本方当年已知，源、身份、lot／round和实际正文对应仍逐项核验。

原始招标计分原文可按干净白名单进入评分；历史proof／身份核验附件、官方标签或人工核验讨论不进入模型正文。字段technical_maximum只由服务端解析当前可用权威；缺权威时null。无分母显示“已评条款N/M；技术总满分待核对”，不写总分覆盖百分比。有效权威给60而条款总和为80，显示结构冲突，不缩放或覆盖权威凑数；总和不足保留缺项范围，不能仅比率高就放总分。

满分／聚合齐备只是输入结构资格；仍需完整相关章节及必要图表、三次独立有效判断、方法／用途／数据隔离及适用模型的独立验收。当前正式模型资格unavailable不能因登记满分变accepted，也不把满分当官方标签或原始得分。

## 5. 已结束／归档历史档案允许的研究

允许读取归档／已结束／已终止项目中可合法使用的原件，在独立研究域新建historical_backtest任务、数据／训练候选和research_only产物，无需为300档案逐个重开。允许从明确档案研究补录入口追加历史证明／主体／满分authority，不改原项目、实交、当前正文／评审／业务预测、业务阶段或归档标志。

入口须持服务器验证的HistoricalInput／Subject／proof及当前权限／预算／来源；不是客户端给purpose字符串就免普通守卫。普通current_review评分、正文生成／编制、规则事实工作写入仍受closed／archive限制。研究任务不能重新进入普通工作producer移动当前指针，独立角色和operation输出保持研究用途。

当前源错误／撤回／权限／proof冲突、实际稿不符、历史组或满分authority失效均拒绝／暂停，不因归档而豁免。训练未来模型允许把真实有据档案作为候选，能否进具体训练／最终测试和业务发布另按协议；回溯结果不回填当年预测，不进在线20配对，不自动赋模型资格。当前实装只支持synthetic的入口仍不自动变real。

## 6. 受限中文任务草案首片

接受bounded-zh-task-draft/1作为首片能力：只识别四类受支持任务、明确每日时刻及明确资料更新事件；表述外／混合类型／否定／范围／频率歧义集中unresolved，触发不明保留manual。明确“上传后”如被解释为事件，页面必须说明是本项目资料入库完成后的真实领域事件，不是每个文件字节传完就运行。

持久草案最少保存draft_id、owner／bid、raw_request、interpreter_id／version、proposed_definition、selected可信来源／章节、revision、unresolved、created_at／updated_at与输入digest；GET可恢复，PATCH强ETag编辑。未能识别也可显示和编辑草案，ready标志不得把用户锁在无法打开的表单。保存／编辑草案都不创建模板、计划、run或模型费用。

同一表单集中改完并点“保存任务模板”，服务端重新验证来源／目标、完整配置、unresolved及当前强ETag后才创建模板版本。明确每日时刻可以作为候选显示，但尚未保存前写“未启用”，其余必填未解决不能自动创建有效计划。不再额外聊天确认；4任务业务能力仍各自受就绪和资格限制，NL识别成功不是该任务已可执行。

此片不是通用自然语言已验收。自由复杂时间／多任务／跨项目范围／推断章节、完整语义补遗、真实通用模型解析与相应UI恢复仍保留后续差距；可用受控模型端口替换解释器，但需冻结端口身份、输出Schema和预算／用途，不改变一次明确保存与正式决定边界。当前仅28项pure、无API，不记持久草案或FR040通过。

[待执行接受任务](acceptance.json)覆盖登记、时点、范围漂移、档案研究和受限草案，全部pending。此产品决定让实现有入口，不代表接口、SQL、浏览器或模型已通过。
