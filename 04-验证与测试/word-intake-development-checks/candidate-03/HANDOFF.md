# Word接收与隔离转换候选03交接

本候选是独立模块的开发交付，等待主后端接线与架构、测试独立验收，不表示完整FR012／FR039或产品已完成。SQL与冻结源码的精确SHA见candidate.json。

已完成实际DOCX字节／大小／SHA／ZIP与XML受限核验、复用原件库的不可变收据、同项目同角色PDF接续声明、真实非owner数据库权限与Cookie／CSRF接口，以及实际本机Docker隔离LibreOffice转换。宏、嵌入对象、外部关系与动态域不执行；原件保存事实与转换、对应检查资格分离。缺字体／缺字真实生成partial，超时真实失败并确认仅本job清理，均不授完整controlled_render资格。

最终私有配置为/workspace/tender-workbench/.local/word-render-profile-dev06.json；通过WORD_RENDER_PROFILE_PATH选择。公开renderer-profile.json不含令牌或DSN，绑定镜像、只读依赖、字体、选项与解析预检源码摘要。转换无常驻服务，Worker compute调用renderer.render启动每job独立network-none、非root、只读、CPU／内存／进程／输出受限容器。没有拉取新镜像、启动模型或付费调用。

验证为48单测＋6真实受限PG／HTTP／Docker联合机制共54过；对象存储在该套测试明确为内存transport，不宣称已测OSS。额外缺字体、缺字、超时三项是真Docker；超时是仅测试的0.001秒故障注入，非容量测试。ruff／format／mypy通过，bandit零发现。成功输入输出原字节及链已留证。

## 后端接线责任

- single／multipart共用inspection.inspect_upload，先持久验证原件，publish_verified才写Word收据并排转换；PDF与DOCX禁止共用同一逻辑文件版本链。declare_pdf_source在新PDF上传同事务写来源意图，核验后只形成user_declared_same_draft／unverified。
- 注册word-render/1到普通document Worker协议和独立Word router，并将contracts.extend_openapi接实际导出；本候选没有修改主main／engine／核心上传。独立测试直接执行compute／publish端口，不冒称Kafka／lease／fence与取消全链已验。
- 新Word正文提交清除旧PDF的当前检查资格，Word ZIP不进PDF parser／RAG。完整转换PDF作为独立PDF文件，其采用必须走既有规范材料提交、解析与来源失效流程。current_word_adoption是受锁资格提示；completed_pdf只返回最新请求且完整受控产物，不能采用迟到旧结果。
- 原Word仍为当前filehead／当前待检正文且冻结输入未变才自动采用。转换早于commit完成时commit也应通过同一采用链；partial只保留产物，无完整关系。转换成功不表示PDF检查已完成或Word交付获资格。
- 未成功时显示“Word原件已保存／本次对应检查未完成”“Word交付未获对应检查资格”，沿同bid／角色／原件上下文“上传此修改稿的PDF”；配置恢复后可重试原件，无需重传招标或强迫进入编辑器。

## 尚待验收

主API／实际上传与对象存储、Kafka租约fence／取消、材料采用与迟到结果、Parser03及RAG来源过滤、浏览器失败接续由后端与QA整体验收。复杂表图／章节层级内容导入、真实业务资料精度、恶意文档攻防、生产镜像与容量运维尚未完成。当前不承诺任意Word无损渲染；存在无法支持的布局会保留partial。A2结构化只读预览可后续另派任务，不能用纯文本化冒无损导入。

Parser03冻结及运行源码、API8806与原有消费者未更改。未执行运行库补丁或owner业务补权。旧SQL草案与历史失败证据保留，不作为本候选通过证据。

## 候选02修复与原件保全

候选01 SHA199d31e0bde9a9cccb8f9893127e7962ead4b035c3bd8e045327474bd8bbf98a及12份冻结源码原字节已复核未变。它有架构W01阻塞，不授转换组件通过；不删除或改写其失败。

W01修复：ZIP元数据资源核验后先受限解析Content_Types，验证Default／Override名称、媒体类型、唯一性和已存在目标。所有声明XML类型的part不论.xml／.XML／.dat均同样解析，使用同一每part／全包字节、节点、深度上限与动态字段检查；XML外观或XML后缀额外保守扫描，类型不匹配阻转换。关系部件按声明类型与真实root识别，非规范命名保守拒绝；XML语义关系目标若无XML类型／已解析内容则拒绝，未知关系不授转换资格。原架构三份合成字节逐一回归，均检出EXTERNAL_OR_DYNAMIC_FIELD，未执行字段或转换这些危险原件。

重试修复：在构造Command和返回旧ACK之前读取受bid与version共同约束的word_view。真实非owner PG／Cookie HTTP反例确认冻结01对另一实际bid返回202旧ACK；02同请求返回404，正确bid重放仍202。证据cross-bid-replay.json。

inspection源码身份变化，dev04不沿用资格；新dev05采用相同已固定镜像、renderer、字体和选项，更新预检摘要并已真实重跑成功PDF→受限PG发布链及缺字体、缺字、超时清理。新profile摘要见renderer-profile.json／candidate.json。SQL380a58…不变，不需新DDL。两处修复不等于独立复审已通过，仍由架构和QA审本候选；完整产品及接线pending不变。

## 候选03：W03保守闭包

候选02有W03阻塞，SHA5dcc4dfad4ee05df0ae326c9bb1f64eff9faaae2abd41e4aa40bb4717e7cb2db及12份冻结源保持原字节；不改其当时失败。复杂域指令可跨run／段落拆分，逐节点regex不能决定是否允许求值。当前LibreOffice没有已独立验证的禁止字段求值策略；复杂版式partial也不能作为执行字段的许可。因此凡fldChar／instrText／delInstrText／fldSimple／fldData均记录FIELD_EVALUATION_UNSUPPORTED并阻止转换，字段不会进入Docker。

这项策略同时覆盖嵌套、无闭合、孤立end／separate、跨part及简单域；不拼接跨part指令冒称有效Word域，也不只匹配已知危险关键字。PAGE／TOC等合法域同样是当前能力缺口，原件可保存并上传对应PDF，不把它们说成恶意。不授动态目录／页码或复杂Word保真资格；A2未来要支持需明确域白名单／结构验证、禁止外部求值与独立验收。普通正文出现PAGE／TOC／DDEAUTO关键词不误拦。当前交付不要求用户手改域或做安全配置选择。

15项新增测试覆盖分run／段落／part、嵌套／畸形、简单与复杂PAGE／TOC、未知DATABASE域以及普通明文正向。原W01三份和W03三份真实合成原件精确SHA已回归，均不获转换资格，字段未被执行。48单测＋6真实PG／HTTP／含Docker共54过。dev06重新绑定预检源码摘要，实际无域DOCX→Docker→PDF→受限PG完整关系已通过；缺字体／缺字partial与超时清理也针对本profile留证。renderer／字体／options／image保持已固定资产，SQL380a58…不变。

当前需架构与QA独立复审03，主Word入口继续关闭；旧01／02的开发测试不充作03独立验收。新Word当前资格、规范材料采用、Kafka／对象存储、浏览器与完整产品pending范围仍如前述。
