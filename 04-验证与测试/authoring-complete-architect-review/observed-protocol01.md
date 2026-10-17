# 完整A2首切片：树、编写计划与双模板协议01

依据产品交接《完整A2与任务UI交接》SHA471d0defbee06fef0d6e90105a3898e4d5a158edf4801096d4580bb82ceab20b。此文及具体 structure.sql 先供静审；未执行DDL，不表示当前接口/前端/生产已完成。旧原件/PDF快评、人工章节与不可变草稿历史保留；不是旧系统迁移。PM已释放backend07固定后的authoring源整合窗口，主main/通用Worker、Word接收/转换由原Owner保持独占。

## 1. 数据与权限

SQL只新增五表，不ALTER既有核心表：authoring_structure_versions、authoring_structure_nodes、authoring_plan_versions、authoring_template_families、authoring_template_links。API可SELECT/INSERT五表，只可UPDATE模板族两个head列且触发器强制+1；其他版本/节点append-only。模板实际配置继续在既有template_versions，kind严格outline/layout分别独立。族head指向相同族/修订/具体版本，前版链严格revision-1、复合FK绑定，不能改写旧配置。

结构版本绑定真实bid/draft/snapshot/revision，额外INSERT触发器核验snapshot ID和revision确为同一行、两个模板kind正确。节点硬FK同草稿section、准确(section_id,section_revision)、同树parent、同章节plan版本。同级active ordinal的NULL根也唯一；SQL只约束直接自环，应用必须整体拒绝多节点环、active子指向deleted父、重复ID/顺序、非连续同级序与超过六层/500 active/2000含墓碑节点。不能称FK已代替这些业务守卫。

五新表不给普通Worker/研究/评分角色SELECT或DML。API将完整结构/计划/模板数据写入原draft_snapshots.manifest并冻结摘要；Worker沿原draft_snapshot_sections/section_revisions/快照manifest以及build_inputs读取精确冻结材料，读取时核完整结构摘要及章节清单。不能回读模板head或最新章节生成旧构建；不授Worker写计划或模板/业务身份权。

## 2. 树CAS、删除影响、复制恢复

每节点稳定section UUID、parent UUID、同级ordinal、type chapter/appendix、section_revision、plan_version_id、模板origin family/key；标题/要求/来源从准确core section_revision读取，避免另存一套标题。旧未注册结构快照只读按实际flat章节投影，不虚构历史层级。首次新增结构功能随正常草稿新修订记录，不批量修改旧快照。已注册树中的deleted节点保留，不因旧保存或读取重新激活。

结构动作先preview：base_draft_revision + 当前draft强ETag + 顺序changes；服务端在真实bid→draft→来源图的统一锁序构造完整prospective tree，冻结原快照/业务上下文、来源头、前后差异、删除影响、服务端新ID映射，写原proposed_actions(kind=authoring_structure)；只返回预览不改草稿。add可用client_key引用同批已新增父节点，服务器产生真实UUID。move/indent/outdent/reorder最终都转真实parent/同级序，键盘与拖动调用同协议；copy整个子树时section和全部PM节点均新ID，保留原文与有效来源引用，但不复制人工决定、事实确认、评审已通过或ready状态；计划置not_started/needs_basis并保留待核说明。

删除预览包含子树、关联要求ID、来源/引用数、人工内容字数、已有计划/待补事项；明确要求仍存在且评审义务不被解除。不DELETE sections/revisions/plan：tombstone只改变本次结构active membership。历史快照原字节和原引用持续只读，恢复保留原section ID并追加当前修订；失效来源不能恢复到新有效正文，保留预览和拒因，不自动清掉引用。

confirm/reject消费同一个action_consumptions。应用锁bid/draft，先原ACK例外，再生命周期assert_work_writable、draft CAS、预览/上下文/来源最终重核，整个树和必要内容变更原子提交。新快照只含active章节且按DFS导出；需要时追加章节revision同步旧content.attrs.ordinal为全局DFS序，树ordinal仍是同级序。标题/正文不变不伪造人工事实。所有有损操作可从旧结构版本预览恢复，不覆盖新稿，412保留输入与候选。旧save/add/restore/apply路径在整合时共同接active membership/结构冻结；直接编辑deleted章节拒409，历史GET可读。零active章节可保存为空草稿，实际构建拒DRAFT_EMPTY。

## 3. 人工编写计划与页数预算

人工计划独立于既有模型writing_plans端口。每章节追加plan版本，state=not_started/drafting/needs_basis/ready（ready仅人工编写状态，非合规/评审通过）；goal、key_points、rule_ids、严格原文source_refs、pending_facts、planned_pages(null或1..10000整数)、budget_basis(manual/requirement/template)+说明。requirement预算须指明仍有效要求，来源经原assert_sources，不能拿摘要当原文或把待补事实变为confirmed。

保存计划同draft强ETag/base_draft_revision且计划base_revision校验，在同事务追加新draft snapshot；tree node固定新plan_version_id。构建读取该快照中的具体计划，不回读当前计划。计划预算和实际分页字段分离，预算总数只汇总已知项并列未知章节，不自动删正文。旧稿或模板复制的ready不继承。

## 4. 目录/版式模板与项目要求优先

目录模板只有结构key、parent_key、ordinal、title、chapter/appendix及可选计划提示，不含项目事实、资格或跨项目引用。每版本不可变、独立族CAS。选择目录先差异预览：同族稳定key或用户明确map才复用原section ID；新key新增空章；移除旧模板key时有人工内容/引用/计划的章默认保留为手工章并标待核，真正删除必须显式选择并列影响，不盲覆盖。应用新增/移动/保留/删除均在同一结构Action及snapshot中记录。

版式模板严格本地文本子集：A4、明确白名单字体、字号、四边距、段间距、封面纯文本/是否启用、页眉页脚纯文本/PAGE字段、章节decimal或none编号、TOC是否输出。任意URL/外部字体/宏/HTML不可入配置。目录版本和版式版本分开保存/选择，不能同一个字段假代表两者。

项目强制格式由人工显式绑定当前有效且人工confirmed要求及其有效原页source_refs，字段/值按同一闭合样式schema，作为structure.metadata.format_bindings固定（含reason）。系统不从一句模型文字静默推定格式义务。已绑定项目约束覆盖企业模板同名字段，preview列出base/proposed/effective差异和rule/source；用户不能选择企业默认覆盖该绑定。若绑定要求/依据变化，构建失败要求重核，不自动丢约束。未被结构化覆盖的格式/暗标文字仍列未判明范围；style选择需明确核对ACK摘要，ACK只选版式、不确认规则/合规通过；未覆盖项持续unknown。新人工绑定也不替代规则域本身的法律确认。

## 5. 实际构建与诚实分页

new actual BuildRequest扩展：directory_template_version_id、style_template_version_id均明确，固定保存ACK后的draft revision/snapshot；旧单template_version_id请求只在原未选新配置的旧文本路径保留，不能在已注册双模板草稿上静默丢弃用户样式。构建manifest包含完整树摘要、各section/plan具体修订、两模板版本/配置摘要、项目格式绑定/有效样式、既有规则事实/资料heads及真实renderer/font/option身份。编辑继续不会改旧manifest，follow_up现有CAS语义完整保留。

真实OOXML输出目录/层级/编号/字体/边距/封面/页眉页脚，不把风险/AI系统操作字样插入正文。内含非可见bookmark/正常Word字段用于后续真实核页；实际PDF仅经原隔离LibreOffice链，Word/PDF各自成功失败，不写假PDF。PDF实际页总数来自PDFium，实际章起止/目录页码未可靠测得时返回null和未核范围，不从编辑器估算/标题字符串碰巧命中猜页。首切片允许page_ranges=unknown，但不能称预算已达标；完整表图、准确范围/预览核对、Word内容导入继续后续切片，分别验收。

## 6. 实现/验收顺序与明确排除

新 modules/schema/SQL→架构具体静审→PM独立fresh初始化→真实非ownerPG/ASGI并发/回滚/非法FK/环路/墓碑/复制新ID/强ETag/跨域来源/撤回/生命周期/模板链与冲突→真实DOCX ZIP与PDF转换内容/未知页范围→冻结源+依赖+证据→独立QA/架构/UI。真实模型调用0，不启消费者，不修改旧releaseDB或bucket配置。SQL静审、组件通过、实际PDF转换各自只代表对应范围，不外推完整A2或生产放行。
