# Word接收与隔离转换候选01交接

本候选是独立模块的开发交付，等待主后端接线与架构、测试独立验收，不表示完整FR012／FR039或产品已完成。SQL与冻结源码的精确SHA见candidate.json。

已完成实际DOCX字节／大小／SHA／ZIP与XML受限核验、复用原件库的不可变收据、同项目同角色PDF接续声明、真实非owner数据库权限与Cookie／CSRF接口，以及实际本机Docker隔离LibreOffice转换。宏、嵌入对象、外部关系与动态域不执行；原件保存事实与转换、对应检查资格分离。缺字体／缺字真实生成partial，超时真实失败并确认仅本job清理，均不授完整controlled_render资格。

最终私有配置为/workspace/tender-workbench/.local/word-render-profile-dev04.json；通过WORD_RENDER_PROFILE_PATH选择。公开renderer-profile.json不含令牌或DSN，绑定镜像、只读依赖、字体、选项与解析预检源码摘要。转换无常驻服务，Worker compute调用renderer.render启动每job独立network-none、非root、只读、CPU／内存／进程／输出受限容器。没有拉取新镜像、启动模型或付费调用。

验证为18单测＋5真实受限PG／HTTP／Docker联合机制共23过；对象存储在该套测试明确为内存transport，不宣称已测OSS。额外缺字体、缺字、超时三项是真Docker；超时是仅测试的0.001秒故障注入，非容量测试。ruff／format／mypy通过，bandit零发现。成功输入输出原字节及链已留证。

## 后端接线责任

- single／multipart共用inspection.inspect_upload，先持久验证原件，publish_verified才写Word收据并排转换；PDF与DOCX禁止共用同一逻辑文件版本链。declare_pdf_source在新PDF上传同事务写来源意图，核验后只形成user_declared_same_draft／unverified。
- 注册word-render/1到普通document Worker协议和独立Word router，并将contracts.extend_openapi接实际导出；本候选没有修改主main／engine／核心上传。独立测试直接执行compute／publish端口，不冒称Kafka／lease／fence与取消全链已验。
- 新Word正文提交清除旧PDF的当前检查资格，Word ZIP不进PDF parser／RAG。完整转换PDF作为独立PDF文件，其采用必须走既有规范材料提交、解析与来源失效流程。current_word_adoption是受锁资格提示；completed_pdf只返回最新请求且完整受控产物，不能采用迟到旧结果。
- 原Word仍为当前filehead／当前待检正文且冻结输入未变才自动采用。转换早于commit完成时commit也应通过同一采用链；partial只保留产物，无完整关系。转换成功不表示PDF检查已完成或Word交付获资格。
- 未成功时显示“Word原件已保存／本次对应检查未完成”“Word交付未获对应检查资格”，沿同bid／角色／原件上下文“上传此修改稿的PDF”；配置恢复后可重试原件，无需重传招标或强迫进入编辑器。

## 尚待验收

主API／实际上传与对象存储、Kafka租约fence／取消、材料采用与迟到结果、Parser03及RAG来源过滤、浏览器失败接续由后端与QA整体验收。复杂表图／章节层级内容导入、真实业务资料精度、恶意文档攻防、生产镜像与容量运维尚未完成。当前不承诺任意Word无损渲染；存在无法支持的布局会保留partial。A2结构化只读预览可后续另派任务，不能用纯文本化冒无损导入。

Parser03冻结及运行源码、API8806与原有消费者未更改。未执行运行库补丁或owner业务补权。旧SQL草案与历史失败证据保留，不作为本候选通过证据。
