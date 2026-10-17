# OPS05 DB-only独立恢复协议01（待架构与PM窗口）

本方案是自动审批拒绝云对象传输后的安全替代：只恢复新隔离数据库并验收暂停应用的本地ASGI接口。**不读取任何OSS配置或凭据、不构造对象端口、不上传或下载云对象、不调用包含对象上传的archive.restore()。** 原含云协议03、prepared及拒绝记录均保留，不重跑、不间接执行。云恢复明确blocked，不能以本方案通过替代。

固定生产输入仍为code05 `6436b14f…` 13生产工具文件+2已冻backup依赖、固定07的308应用文件及原15SQL档案。复用仅含源代码/封存本地档案的ea8eb…准备根，其原prepared SHA `3ca8de5f3490ac1e204d6a6040003d21ebbd8b95d3d1cfc26bd5739a7a044a84` 不修改。新DB-only receipt、容器、卷、数据库、LOGIN和system_identifier必须全新，失败旧目标和两原开发卷均不挂载。独立脚本另行绑定，不声称是原完整CLI或含云restore的一次通过。

容量沿用单目标1536 MiB初始、原create内部900 MiB、每阶段256 MiB。只允许一个新PG容器；`--pull=never`、缓存pinned PG17镜像、loopback随机空闲候选55531，内存384m/cpu1，仍必须由PM交还独占窗口后执行。空间不足即停，不降门槛；停止/删除只作用本次全ID+UUID标签+卷mount核准的容器，保全部卷和档案。

原dump SHA `6d568c5730a85cbf1dbf25406fd9fb1070cfc28b48c5b70acde5d87cb7ee3146`，manifest SHA `ccacb97792be19b157748580a71b0c8e4f891e249556aa3d8dcad1511598358b`。仅本地读取7个对象文件作为封存档案校验，绝不发送。源plan文件SHA `62a347bdd144c80f1e8ebcbb4c81fb579b70068598aebb4120e4dc8317bc82da` 所载恢复标记必须按原row_to_json+8字节长度前缀算法重算并等于manifest唯一recovery_control.identity行SHA `348bacba4fc40399c20fd19e723a64b8dd24348613194f189f996cf035c34e04`。只有cluster.nonce沿用该数据标记；execution UUID/全部资源与目标systemID保持新建。

执行顺序：

1. 再核协议、两个QA脚本、原prepared25项输入、403项封存输入、308应用清单及源plan marker；不连接源DB。冻结binding只加载原308应用源码。
2. 新私有目录700/文件600记录DB-only intent。调用原cluster.create(target)，owner仅本次新建目标bootstrap；DDL身份仅恢复，不能用它修业务权限。
3. 对原dump在这个目标内实际pg_restore --list；164 TABLE DATA精确核对。随后调用原s.pg_tool的pg_restore --exit-on-error --single-transaction。显式编排原archive.restore的DB部分：原cluster.reassign→accounts→finish_ddl；不调用其对象分支，不补DDL/GRANT，也不变更dump或标记表。
4. 新非owner reader以RR READ ONLY核164表内容SHA、catalog和13类完整definitions；965个CHECK只允许原既定一对完整括号写法等价。目标systemID必须与源不同。独立保存原始读取目录/定义的私有结果，不用开发布尔结论替代。
5. 原受限LOGIN负向权限实际验证：reader关闭default readonly仍不能写表/DDL/序列/RPC/SET ROLE，且全部表/空certifications可读；API不能读certifications/DDL/SET ROLE；worker CONNECT拒绝。失败不补权。
6. 一个显式只含API DSN和新session/CSRF秘密的子进程从原308树调用真实ASGI。HTTPTransport/AsyncHTTPTransport、urllib和Python socket外连硬拒，只有内存ASGI和libpq的明确loopback API DSN；Kafka为空，无provider/对象配置。读恢复的合成项目/知识/报告metadata，不访问对象字节；验证旧cookie401、CSRF403、唯一新bid及同key重放、新bid同步设置、第二新key/旧项目写/upload/operation423。父进程用reader独立核所有旧行保留、旧outbox/operations/unknown model_calls逐字不变、恰好新增一个bid。
7. 重核原403+prepared25输入未变。全过程不得读取source-objects.json/target-objects.json/providers.env，也不得构造OSS或model端口；只读member-credential的三个必要登录字段，沿stdin传给API子进程，不输出或写公开记录。
8. 结果保存为“DB及暂停ASGI范围通过，云恢复blocked”。精确stop/rm本次容器，保新卷；回报资源余量和交还窗口。失败也保留，不自动重跑。

本方案不验云对象可读性/取件、真实网络服务器/浏览器、生产RPO/RTO/断电恢复/在途接续、最新A2/Score/Scale、模型/RAG/Word运行或业务资料质量。原15SQL及fixed07仅作为恢复档案边界，不扩大为新模块当前完整schema验收。
