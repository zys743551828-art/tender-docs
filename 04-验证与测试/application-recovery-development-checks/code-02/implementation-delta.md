# OPS应用恢复代码02整改范围

状态：静审候选，未创建资源、未执行SQL/对象恢复。code01 a886ce893db31eeb36b7e3a4f74152b46c1d932b2b5780a3f3b5f05aa05460cd及原24离线/缺口交接不改；协议02 e33b/c832不改。不是应用恢复已通过。

1. 所有CLI绑定阶段先核固定07 308字节，再安装FrozenBackend finder；source/seed、snapshot、target/restore和API-check/provider不再使用普通搜索路径。只允许manifest里的apps/packages源，namespace仅固定根，缺模块拒绝而不fallback。缓存中混入live模块/第二namespace根即拒，不能通过改sys.path洗成可信。SourceLoader直接编译再次核SHA的源字节，不读旧pyc。源、快照、目标阶段保留显式module origins收据。
2. object_port先要求固定绑定，再在只有对象用途配置＋必要proxy/CA的env内导入固定OSS provider；source seed与恢复同根，不从live provider取代码。直接调用未绑定snapshot/restore/seed/新cluster也在资源或网络动作前拒绝。
3. 真实API验收拆成python -I独立子进程，stdin只传backend身份、合成对象ID与登录名/口令/旧cookie，环境只允许唯一API_DATABASE_DSN、全新session/CSRF、空Kafka和禁默认profile标志，以及基础proxy/CA。不传cluster/owner/reader receipts、不传source_session_secret；HOME/DOCKER_CONFIG/PYTHONPATH/云key/Worker/purpose/backup DSN都不进入API环境。child再次校用途与固定根，验收原app无lifespan背景消费者；完整历史/队列前后读验仍在父级专用reader完成。此处是用途/输入/环境隔离，不宣称同UID文件系统沙箱。
4. 磁盘预算提升：初始至少1600MiB、创建每个cluster前至少900MiB；估计两容器写层900MiB＋两小PG卷320MiB＋档案32MiB＋256MiB余量。真实空间不够直接拒绝、不拉新镜像、不删任何既有资源。此刻只读df约1.6GiB，实际执行前须重新量测，不能依据估算宣称容量通过。
5. 新测试覆盖live provider缓存、混合namespace、缺模块fallback、源字节变化/pyc跳过、13类ambient credential排除、4类注入第二用途拒绝、5个未绑定入口0资源/0网络拒绝、磁盘不足拒绝。实际干净子进程导入固定07 main/knowledge/provider核全部生产module origins，未进入app lifespan/未连接数据库/未访问OSS/未调用模型。

保留范围：15SQL最新已审输入，不擅自纳入还在审的score SQL或Word后续代码。default work_paused；仅单个新UUID同步API测试例外。原outbox/租约/generation/model_calls unknown不重置、不续跑；无Kafka消费者。外部CPU权重/字体为只读资产字节绑定，Word sandbox/真实推理/生产RPO RTO容量均未授。
