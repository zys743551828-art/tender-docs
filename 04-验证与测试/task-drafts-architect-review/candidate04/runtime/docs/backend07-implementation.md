# 后端07：工作关闭守卫与固定执行接线

本片保留06及更早候选原字节，新增READ COMMITTED当前写入前置、统一bid→Command→领域对象/来源锁顺序；只读历史RR不改变。新普通work在closed/terminated/archived项目拒绝，旧ACK只重放原结果；取消、显式终结和专用实交/结果/身份档案更正为明确例外，不暗重开项目。

单/分片授权、代理传输快照及发布、完成/重新绑定/批次commit均核项目与显式batch closure，分片interim ACK不能授权关闭后的远端完成。网络不持bid/命令锁；最终再次同bid锁重核。普通Worker acquire关闭后不授执行权；已取得lease的迟到finish记录失败和审计，不发布业务结果，使用量账本仍保留。只读assistant、比较和派生档案索引不移动正文/规则/人工决定，按明确例外继续可读派生。

POST `/bids/{bid_id}/upload-batches/{batch_id}/retained-uploads`使用源upload强If-Match、幂等键和显式理由/目标，将已关闭批次的verified保留字节选入新open批次。当前项目须可工作，源同项目、未入库、仍在保留期、声明/实际blob一致，远端原件核验通过才创建新version/upload/binding；共享原blob，不重新上传、不复活旧batch、不冒充controlled render。旧file-head CAS与后续commit再核仍有效，过期/失效明确拒绝。

任务02已真实接`_advance_head`与`freeze_candidate`的同一review_guard，输入漂移持久superseded，不跟随auto_review静默换稿；main仅在实际`materials.committed`同事务传真实audit UUID给事件协调器。固定助手implementation01两个项目handler正式进入共享engine，compute/publish使用Owner真实端口，API角色另物化回答/提案，普通Worker不写人工决定。global助手仍独立null-bid协议和topic，不伪造bid。

无费用comparison使用独立`tender.task.comparison.v1`和`tender-comparison-worker-v1`，CLI `python -m apps.worker consume-comparison`；不订阅review topic。每个普通consumer按固定lane白名单在acquire前拒绝错路handler并保留位点。助手项目consumer为`consume-assistant`；global为`python -m packages.assistant.transport worker`，不是普通engine的全局DAG。完整topic/group/初始化与职责见`apps/worker/execution-policy-07.json`。本片不启动任何付费消费者；独立新broker的topic创建/真实运输验证由PM按冻结policy执行。

归档基本router尚未由main公开；全producer独立验收和PM阶段放行后再开放，不能把少量底座测试说成所有入口完成。归档历史可读、只读问答/检索/派生索引允许；新编辑/生成/Action/项目知识引用/报价等受守卫。source档案补录、identity显式更正、线下实交/结果更正保持独立审计和旧历史，不能借普通上传旧意图绕过关闭。

06工程断言guard另有07最小加固：明确“并非/不是/更正/作废”等否定上下文不能截出肯定子串；无法证明给诊断partial，不泛化为通用中文语义质量。

开发机制验证采用真实受限PG、原生OSS合成对象、Parser03/LibreOffice和显式MockTransport。外部生成0次；不代表实际模型效果、生产恢复容量、浏览器或独立验收。Word新端口/SQL仍为Parser Owner独立候选，本片仍只收PDF，不提前混入移动Word实现。
