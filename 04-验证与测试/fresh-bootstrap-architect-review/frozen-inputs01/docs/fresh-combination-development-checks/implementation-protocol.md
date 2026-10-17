# 独立完整组合环境：fresh bootstrap候选01

本候选仅准备组合部署底座，不启动API、relay、普通/评审/研究消费者或CPU任务。既有release、model_governance02及旧5175/8802配置和进程均未修改。FR01103、generic及native完整备份冻结源与证据保持原状。

## 一次fresh与已审字节

`deploy/create_check_environment.py`调用独立`bootstrap_fresh.py`，不修改root initialize_database.py/run.py。支持有序重复`--reviewed-sql PATH SHA256`，保留单份extension参数。所有baseline与扩展SQL在任何数据库连接/CREATE前读取为不可变bytes；每份扩展精确验SHA，重复、私有路径、摘要不符立即拒绝。执行只使用已读取字节，不在校验后重新打开文件。baseline七份实际SHA及扩展顺序均写公开receipt。

组合依次执行七份baseline，知识SQL `51c98cf1aea5364b418747161e86bb374b9b3d44222d67891ec18e27bc200003`，治理SQL `0b7dc8863fb83703116189fb3e6bcf442ed5fae2250d50993189e04aefa6f51a`。先在pg_catalog确认全部固定能力组已经存在且为安全NOLOGIN组；不创建缺失组，不给旧LOGIN追加能力，不在旧库补DDL/授权。源目标库名和profile存在均拒绝，错误后不提供resume/hotpatch路径。

只对本次新建的随机独立LOGIN进行创建和能力组绑定：api、worker、research_worker、score_read，以及可选model_train/model_evaluate。属性显式禁止superuser、BYPASSRLS、CREATEDB、CREATEROLE和replication。新数据库撤回PUBLIC CONNECT，仅对本次六LOGIN明确授CONNECT；源owner仅用于新库初始化/账号引导，不写业务记录。已有release API LOGIN真实尝试连接新库被服务器FATAL permission denied拒绝；该连接期客户端错误未附SQLSTATE，不虚报42501。这里不宣称新LOGIN在所有历史数据库的连接能力已被收回，未修改旧库ACL。

初始化profile、普通business profile和两个purpose profile均600，.local为700并被Git忽略。普通profile剥OWNER/POSTGRES_PASSWORD及两新用途DSN；训练/评测profile各只有自己的一个DSN，不复制给普通Worker。CPU执行仍由研究Owner的白名单launcher负责。本工具不修改该launcher或通用run.py。

## 实际新组合

精确DB/profile/LOGIN/SQL源身份见created-environment.json。源库是`tender_checks_combined_delivery01`，业务profile `.local/combined_delivery01-checks.env`，初始化profile `.local/combined_delivery01-init.env`，两个purpose profile `.local/combined_delivery01-model_train.env`和`.local/combined_delivery01-model_evaluate.env`。所有表目前没有业务任务/原件，API写能力用事务回滚探针验证，不留业务记录。计划API8806、Web5176，APP_ORIGIN=`http://127.0.0.1:5176`；在无blob/对象调用、未冻结且PM明确安排后，仅调整本次新组合init/business两profile的APP_ORIGIN，其余字段逐一确认相同。工具现提供`--app-origin`供以后fresh时一次配置。

`bootstrap_check_kafka.py`使用现有固定镜像compose.external.yaml，仅单启kafka。新project tender-workbench-combined01、新volume、新随机cluster ID override、端口19096；已有项目/volume/占用端口均拒绝，不接管旧broker。真实metadata证明19096与19095 cluster ID不同，19095仍可访问；新六topic的分区/副本/retention/cleanup等由现有initialize_topics.py创建核对，新broker consumer groups=0，未生产应用消息。未显式选择独立broker的未来fresh环境会把bootstrap地址置空，避免隐式继承旧DB的固定topic broker。

真实非owner连接六LOGIN核库名/身份/角色标志/非owner及仅自己的能力组。普通Worker不能UPDATE bids或读治理exports，score_read不能UPDATE，train/evaluate不能读samples/exports或调用对方read函数，只具自己read函数EXECUTE。没有实际创建训练/评测任务。

## 已发现的历史prefix缺口

旧helper写`OSS_KEY_PREFIX`，而原生provider实际只读`OBJECT_STORE_PREFIX`，因此旧helper不能证明环境对象namespace独立。本次对knowledge_rag、research03、model_governance02三份checks profile作只读key/hash比较：三者实际OBJECT_STORE_PREFIX相同，且都有无效的OSS_KEY_PREFIX；当前external-release.env的值与三者不同，不能据当前文件名断言旧运行时等于这个release配置。详见prior-prefix-observation.json。旧DB/LOGIN独立仍按原实证保留，旧自有UUID key测试只证明那些精确key的安全边界，不追授独立prefix。未重写任何旧profile或原审查报告。

新helper写真正的OBJECT_STORE_PREFIX独立随机子前缀，去掉无效key；用本次business profile真实构造OssClient.from_env后核其prefix与receipt一致。该配置探针不请求OSS，且原生对象请求总计0。不扫描用户桶、不修改桶策略/CORS、不产生付费模型调用。

质量：11项边界单元通过，ruff/format/mypy4源/bandit通过。实际PG/原生provider配置/Kafka探针分别留JSON证据。不是应用组合验收，尚未启动后续业务进程；待PM固定应用源组合和独立复核后安排启动。所有异常输出均只给安全错误码/类型，不含DSN、口令或SQL原文。
