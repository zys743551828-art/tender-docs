# 外部Word接收最小端口

本能力复用现有uploads、document_files／versions、blobs、document_relations。Parser03及现PDF引用路径不改。新增schema.sql包含不可变Word容器核验收据、明确补PDF来源声明和绑定既有operation的转换运行收据，不另建Word文档库；由PM按已静审精确字节在全新隔离库初始化，不修补运行库或以owner修复业务权限。

端口分四层：`inspection.inspect_upload(filename,data,declared_digest,declared_size)`供single／multipart同一verify compute使用；`repository.publish_verified(c,message,upload,version_id,inspection)`在原件blob与version已建立的同一受限Worker publish事务追加收据／PDF关系；`repository.declare_pdf_source(c,bid,upload,source,actor,command)`在API创建上传同事务持久声明；`repository.word_view(c,bid,version)`供独立Word读取API及现资料行聚合。

DOCX先限制ZIP条目数、单项／总解压字节、膨胀比例、XML字节／节点／深度；先受限解析Content_Types并校验Default／Override无冲突，以声明类型识别任意后缀XML部件，统一包级预算与活动字段检查。XML关系目标的类型与内容不符、非规范关系部件命名保守拒绝，未知关系阻止转换；禁止路径逃逸／重复条目／加密容器／不支持压缩法，验证content-types、主document及根关系。外部关系、宏、嵌入对象、ActiveX／altChunk等为安全缺口，不尝试执行或转换。验证不读取外部URL、不执行宏、不外发正文。有效原件即使有这些缺口仍可安全持久保留并标转换不可用；不是DOCX或资源越界则核验失败，不能称原件已保存。

未配置受控renderer时，转换任务记录`state=failed`、`conversion_attempted=false`、`WORD_RENDER_SANDBOX_UNAVAILABLE`，不是伪造调用soffice失败。已配置时实际执行独立Docker CPU转换；记录原件／PDF摘要、renderer／fontset／options／profile摘要、实际隔离检查和对应范围。失败卡显示“Word原件已保存／本次对应检查未完成”和“Word交付未获对应检查资格”，主动作“上传此修改稿的PDF”。配置恢复后可直接对已保存原件重试，不要求重新上传；未恢复前不展示假重试。转换成功本身不等于对应PDF已经通过评审。

补PDF必须同bid／资料角色，声明源必须是已核验外部Word版本。声明绑定upload，原件核验后Worker只写`document_relations.kind=user_declared_same_draft,verification=unverified,evidence_ids=[]`，不据同名或同项目推定同稿。DOCX和PDF不能写进同一file版本链，复核只绑定实际PDF。来源Word、失败收据、历史PDF关系全部保留。

后端集中接线处必须同时收口：single／multipart filename开放PDF与DOCX；verify原字节及blob媒体类型统一走inspect_upload；new_version／rebind必须保持文件媒体类型；资料commit中Word可落原逻辑文件与可发现资料行，但不得抢current_body或进入PDF解析／自动完整评审。只补Word时旧PDF结论不能被展示为新Word结论；补PDF仍沿原项目资料上下文，不要求重传招标或进入章节编辑器。Word收据接口可直接读取未commit但已verified的持久原件，供失败卡恢复。

单份接收与关系写入使用既有Command／If-Match／上传绑定／Worker fence；检查及对象存储I/O在compute，不持bid图锁。原件保存事实和转换／对应检查资格分开，未收件415或核验失败不说已保存。本地真实受控转换仅按已执行合成范围留证，复杂资料、真实业务精度和生产沙箱仍待独立验收。

## 实际受控renderer

`renderer.py`为固定本机daemon端口：显式`unix:///var/run/docker.sock`，无shell、不继承DOCKER_HOST／context或宿主云key／DSN／代理，私有空Docker配置。复用已安装固定imageID，禁止拉取；只读挂载已固定摘要的LibreOffice、公共系统库、字体、受信Python入口和单份只读DOCX，不挂宿主HOME、Docker socket或任意用户路径。镜像自带默认环境逐项移除，仅保留固定公共环境；实际container inspect后才能启动。既有postgres基础镜像的匿名数据卷用不可访问1MiB tmpfs覆盖，不遗留匿名持久卷。

独立容器network-none／非root65534／只读根／无capabilities／no-new-privileges／私有pid、uts、ipc-none，CPU1、内存512MiB、pids64、文件句柄256、无core；临时目录128MiB、输出64MiB均为noexec/nosuid/nodev tmpfs。容器内LO40秒、宿主50秒超时；固定UserInstallation在每容器独立tmpfs，不改宿主HOME或系统目录。PDF只从容器受信入口的有界帧读出，宿主不挂可写输出目录；拒绝symlink、FIFO及超大产物。完成、失败、超时仅清理本次生成的唯一job容器；清理未确认不得授完整资格。

当前配置安装命令为`.venv/bin/python -m packages.word_intake.renderer --write-profile <新的私有profile.json>`，拒绝覆盖已有profile；主进程用`WORD_RENDER_PROFILE_PATH`选择已审核的本地profile。每次转换重新核验资产与profile，方法变化不静默继承旧任务身份。完整锁定镜像的生产构建、恶意文档攻防、容量和长期运维验收仍需独立执行，不能由本地合成成功代替。

实际PDF还需具备可打开物理页、完整文本对应及字体字形覆盖。缺字体替换、缺字、非文本／复杂布局、文本丢失或输出空白均保留partial／failed；partial PDF可作为有缺口产物保存，不能写controlled_render已核验关系。只有实际成功、完整coverage、无gaps、全部隔离检查、两端字节及完整方法身份同时成立才写该关系。对应检查仍绑定随后实际受检PDF，不因转换而假报评审通过。

## 运行与后端接线

新增`apps/worker/word_intake_handlers.py`实现`word-render/1` compute／publish，复用document topic及普通Worker operation／lease／fence／取消，不启动新的付费或研究消费者。API只读取私有profile并登记转换请求，不写转换完成收据；Worker只在fenced publish写一次结果。SQL在全新Word检查库由PM初始化；运行角色只用`.local/word_intake01-checks.env`独立非owner账号。SQL静审后精确字节保持，不以owner修复业务。

`apps/api/word_intake_routes.py`提供项目Word原件列表、单份状态强ETag及明确重试。`contracts.extend_openapi`仅扩实际注册路径。恢复后重试保留原件，使用If-Match／幂等键；返回旧受理结果前也必须核对bid与Word版本归属；相同项目的相同请求重传返回原operation，能力配置撤掉也不能否认已受理命令。Cookie／CSRF与项目生命周期沿既有规则，跨项目／角色来源声明拒绝。GET失败状态不隐藏已保存原件。

新Word正文提交必须让旧PDF检查失去当前资格，不能用`COALESCE`留下旧current_body冒充新Word已受检。DOCX原逻辑文件／安全收据保留，PDF parser不读取其ZIP字节，RAG不发布未读Word为可引用来源。完整转换PDF登记独立PDF逻辑文件及来源链；自动采用只在源Word仍为该file当前head、仍是当前待检Word正文、冻结输入未变化时成立，迟到结果只保留历史。若转换先于Word commit完成，commit需核对已有完整产物后走同一规范材料采用链。采用触发真实PDF解析／RAG来源／统一评审，不能借转换结果直接解除风险或授Word提交资格。该统一材料接线由后端Owner实现，本模块`current_word_adoption`只给受锁事务内的资格提示。
