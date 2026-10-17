# RICH图片资产SQL静审

结论：两张编制图片资产元数据表及当前协议无新增静态阻塞，可由PM在另一个全新隔离库初始化后验证。此结论不批准尚未送审的图片接收/解码、rich3.x writer、容器校验或实际renderer运行，更不代表图表对应/完整A2通过。

候选SHA256 `211cab0a43267630b985af1ef1c7326583317e07e2a0d6bcf4a659816369bdfd`；具体SQL `cba0da43fcf36566e02df46d7962e3c53f7b5daad671b51d1513cae7185f2edd`，协议 `0e35fb5d83a210dc0504587192c036c2b2f19ff57977775a8199207b72322506` 全部匹配。详 [review-record.json](review-record.json)。

SQL边界符合本片目的：authoring_image_assets保存项目内资产head，authoring_image_versions追加不可变版本；同bid/asset/revision/id复合唯一及精确head延迟FK，上一版同资产组合FK、revision-1检查和head UPDATE trigger连续推进。active/withdrawn是新版本，不改旧bytes；API仅head两列UPDATE，无DELETE；Worker仅两表SELECT，不增DML、工程/企业/commands权限。原/规范图分别FK现有blobs，没有新造全业务资料文件库。

复用storage/blobs可行，但既有PDF/DOCX资料上传端口不能因此接收图片；必须独立真实资产登记并由服务端选择对象key、原/规范bytes和proof。数据库的JSON object/array及blob FK只证明基本形状/存在，**不证明**actual PNG、Pillow结果、source_refs资格或完整normalization proof；这些是后续实际端口的必需守卫，不把未实现本身当本SQL缺陷。

实施验收条件：

- API鉴权/项目可写预检后关闭连接，实际有界PNG/JPEG解码、拒动画/多帧/炸弹/错误格式、EXIF处理与规范PNG再编码、字节PUT/回读SHA在所有业务锁外；最终同bid锁重新鉴权/生命周期、Command及asset CAS。原图只能attachment取回，不能直接交LO。正常化proof须与输入输出SHA、实际尺寸/MIME/算法/版本绑定，不能由客户端自报。
- 保存、恢复、构建冻结与Worker最终publish都查同bid当前精确active asset版本；持bid锁按资产ID顺序取当前head，Worker仅SELECT不能FOR UPDATE。API所有head推进/撤回必须同bid锁，由此串行化最终发布。旧快照只读恢复原图，撤回或head变更拒新用，不删历史引用。
- source_refs按严格schema验证精确原件/parse/evidence/逐字与祖先头；从Word或原页派生不得丢来源。冻结到原core snapshot的完整去重图片清单含实际两端blob、key、SHA、MIME、尺寸、proof和asset/version身份。Worker关闭准备连接后读取规范PNG并重新核字节/尺寸，最终来源或资产撤回应零effects。
- 新rich3.x生产器/profile必须独立静审：确定性重生成逐字节、完整OOXML部件/内容类型/关系及规范图片白名单；不能复用PAGE2.x允许集偷偷放行drawing/media或放宽外来Word全域拒。表格合并矩形、宽度、真实图片可见性都需实际DOCX/PDF验证；文字摘要不足以授图表对应complete，无法证明时partial。

新库必须验证合法首版/连续head/撤回与并发CAS，以及跨bid、错asset/前版/head、revision跳跃、版本UPDATE/DELETE、Worker DML/identity与FOR UPDATE等负向权限。首版创建和未提交版本分配仍需API命令约束；SQL不能单独证明用户完成了一次真实上传动作。

本轮仅读取固定SQL/协议与摘要，未执行SQL/DDL、账号/数据库/对象/图片/容器/模型。旧PAGE2.x、外来Word03及已冻结A2候选保持原字节；实际图表/renderer源码、前端和生产容量继续独立验收。
