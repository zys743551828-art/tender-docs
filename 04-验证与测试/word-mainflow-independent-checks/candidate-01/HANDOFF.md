# Word共享主流程交接

候选固定根为`runtime-preview-02`。普通PDF路径与DOCX路径共享原件库、文件头、上传/批次、Kafka执行协议和fence；DOCX与PDF不能混入同一逻辑文件。目标快照、旧07/F05及运行服务均未改动。

新增输入通过`WORD_INTAKE_ENABLED=1`显式开启，默认503关闭；仍须有完整Word schema。源码固定副本的runner主机挂载路径参与转换身份，必须使用为该固定根生成的私有profile `.local/word-bridge-runtime-preview02-profile.json`，其公开身份在`renderer-profile-02.json`，digest为`61284d08db2703f4d753a3d098e0e54d8d84926ec5fa65c7ee3d3cf1f14a406b`。不能把工作树dev06的profile直接用于不同路径副本。

## 当前行为

- single/multipart按服务器文件名固定格式和Content-Type；实际Worker统一检查完整字节、SHA与OOXML。原件先登记核验收据，转换任务独立派发。坏renderer配置也保存合法原件并明确转换未完成。
- 新Word正文入库后，当前受检PDF清空，旧PDF和评审保留历史。Word不进入PDF parser，只产生明确未读缺口。标准提交检查返回无当前受检正文，不能承接旧PDF资格。
- `word-render/1`仅走document lane，无模型调用；共享取消/代次/fence/生命周期/批次守卫。结果成功保存不等于转换complete，更不等于评审通过。
- API控制面`python -m apps.api.word_coordinator`在对象I/O和解析身份读取完成后，以项目锁重新核对最新完整受控转换、实际PDF摘要、Word文件头、所选材料及冻结上下文。早完成与晚完成复用同一采用函数；旧稿/设置/材料/头改变则只保留历史产物。转换Worker没有当前材料或人工决定写权限。
- PDF采用后新增规范材料快照并派真正PDF解析；Word原件root→PDF原件root→parse祖先链使用同一公开来源身份函数。Word root没有文字块/chunk。撤回或不生效祖先不能重新发布。仅用户补传的PDF保持`user_declared_same_draft/unverified`。
- 已结束批次的Word复用正式原件字节，无需重传；新批次先显示verifying，由普通Worker按当前检查规则重建新收据。API不能伪造Word收据或受控关系。

## 实际接口与运行边界

实际契约`actual-02.openapi.json`共165路径、192操作、315模型；相比固定基线增加Word原件列表/单读/转换重试及`GET /bids/{bid_id}/word-materials/{version_id}`。后者返回当前材料、源文件头与采用结果，明确区分历史已生成PDF和真正当前采用PDF。所有写接口仍Cookie/Origin/CSRF/幂等/CAS。原件下载复用`GET /artifacts/{id}/download`及同源字节端点，不向浏览器暴露云密钥。

执行策略见`apps/worker/execution-policy-word.json`：新增handler仍使用既有document topic，不增加topic；API采用协调器只使用API LOGIN。当前专用验证profile `.local/word_mainflow03-runtime.env`无Kafka地址、无生成模型key；不能用此结果宣称Kafka整链或付费消费者通过。03数据库由PM以16份已审SQL全新初始化，本开发没有执行DDL或热补旧库。

## 已验证范围

固定335源零漂移；13项非ownerPG/HTTP主流程通过，其中包含1条真正原生OSS+Cookie同源上传+真实无网Docker+Parser03+采用+下载SHA链，其余对象端口明确为内存机制。覆盖转换早/晚、新Word取代旧PDF、旧转换迟到、取消/关闭、闭批字节复用、DOCX分片、坏配置与声明PDF恢复。额外3项W04真实PG/Worker验证顺序、API列权限及最新pending/failed不回旧成功；124项纯回归通过。16生产文件mypy/bandit、19 Python文件ruff/format通过，315模型/1268本地引用完整。

`native-readback-02.json`给出合成项目/原件/转换PDF/解析身份及再次原生GET回读结果。隔离库model_calls为0。真实模型判断质量、Kafka、浏览器Word界面、正式评分和Word交付检查资格未验证；独立测试和架构仍需按此固定候选复核。

首份固定副本冷跑打包错误保留在`fixed-mainflow-01.xml`：缺验证夹具、runner文件600不可由容器非root读取，以及运行时缓存被误计入输入。最终副本严格按F05的310源清单复制公共源码644，并单独固定验证夹具；旧失败没有被改写。
