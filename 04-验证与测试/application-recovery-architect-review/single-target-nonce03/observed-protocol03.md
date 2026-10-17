# OPS05 单目标独立恢复协议03（独立失败后的编排修正，待新窗口）

本轮仅使用已封存的完整源档案，创建一个全新恢复目标。不会新建、重启或挂载源集群，也不挂载开发已保留的两个卷。它是独立的单目标场景，不是原完整场景复跑；原 `cluster.plan()` 的1600 MiB初始门槛和 `cluster.create()` 的900 MiB容器门槛均不改、不屏蔽。当前只读检查通过，实际恢复、真实目标权限、云对象及API验收均待执行。

## 固定输入

- code05候选 `6436b14fbd0bd9278f6ae70bea10b1ee4d7e7e2faebda140dd77421d8def052a`，19自有文件（13个生产文件、6个测试文件）及8个观测依赖。
- 后端固定07清单 `71fe0fb2c9dbde7d85e62d20bef2da8e188e59ac00e9eb056cc021a1bd75274e`，308原始文件；15份SQL只核字节，不在源或旧目标重新执行。
- 原完整串行结果 `9176380c0083fc479354a819b9221db370783392c8ccb9ee898282ce2d59c3ab` 是开发证据，不是本轮通过结论。
- 原49项私有证据索引 `5533e3df11748a2e6ce74b67e37b9257184ca93d368b5eba6291e8f6b14d206d`。
- 源目录 `/workspace/tender-workbench/.local/backups/application/drill04-20261017-c23d8a71/archive/`。manifest SHA `ccacb97792be19b157748580a71b0c8e4f891e249556aa3d8dcad1511598358b`；dump SHA `6d568c5730a85cbf1dbf25406fd9fb1070cfc28b48c5b70acde5d87cb7ee3146`，852503字节；7个正式对象合计322字节。
- 独立只读结果见 `readonly-result.json`，403项实际输入全部运行前后核SHA；其568条断言主要是逐文件检查，不用数量代表实际恢复通过。

## 容量门槛与当前结论

原开发记录在source单容器创建后可用空间净减少约964 MiB，业务数据库约15.9 MB；这不是容器最大占用上界。新场景按下列保守预算申请 **1536 MiB** 可用空间：

| 项目 | 预算 MiB | 理由 |
|---|---:|---|
| 一个固定PG镜像的VFS展开及容器写层 | 1024 | 向上覆盖已观测约964 MiB，不采用最初估计450 MiB |
| 一个新PG卷、数据库及WAL | 192 | 覆盖小库、初始化及配置128 MiB WAL量级；WAL配置不是严格磁盘硬限额 |
| 私有输入副本、目标读回与报告 | 32 | 输入约2 MiB，禁止复制模型/renderer资产或镜像 |
| 余量 | 288 | 覆盖瞬态WAL、文件系统及环境小量变化 |
| 合计 | 1536 | 与原完整场景1600 MiB保持不同、显式的场景身份 |

启动同时满足：PM与架构核准本协议；明确独占Docker窗口（Word/build暂停）；实时free>=1536 MiB；原代码内部900 MiB门槛原样成立；缓存镜像digest存在；目标端口空闲；无本轮既有容器/卷/DB/receipt。当前只读执行时free约1154 MiB，**不满足，NO-GO**。不从旧资源回收或删除资料凑门槛。

预算不是容量保证。每个阶段记录同一文件系统free；低于256 MiB即不再推进下一个阶段，停止自身新目标并保存失败和卷。pg_restore/API等有原代码超时；在途调用自然成功或失败后收口，不盲目重试。若需要降低单目标1536门槛，必须新增实际容量论证并重新审查，不能在执行器改数字继续跑。

## 独立资源与执行根

窗口后首次执行才生成一个新UUID。目录为主项目 `.local/backups/application/independent05-<UUID>/`，目录700/文件600；container、volume、DB、owner/DDL/API/reader/worker LOGIN均由冻结 `cluster.create(..., side='target')` 生成，不复用原资源。目标port候选55531，启动前绑定空闲检查；冲突即停，不连冲突目标。

单独准备小型执行根：从候选原始副本复制13个生产文件及 `deploy/backups/{generic,run}.py`，记录SHA；不改被审代码。后端由冻结 `binding.install(backend)` 加载原308输入。为 `application.check()` 的 `-I` 子进程提供明确的本地 `.venv` 链接，不复制依赖环境、不使用moving主源码。实际加载的 `deploy.application_recovery`/`deploy.backups` 文件须全在本轮清单；`apps`/`packages` 须通过原308检查。独立QA编排代码单独归档、单独SHA，不称它是原CLI的一次完整运行。

原archive在新私有目录复制一份后核seal/dump/每个object，与原件完全相同；复制体也不修改manifest或dump。原源配置仅按OBJECT_ENV白名单在父编排进程内取对象凭据；目标前缀追加 `application-recovery/independent-<UUID>-target/`，必须与源及开发目标locator都不同。API子进程只有API DSN、全新session/CSRF秘密和空Kafka参数，不含云、reader、DDL、owner、worker或模型凭据。

## 具体代码调用（待批后编排，不调用原完整plan）

1. 独立编排器再核本协议SHA、候选/308/15SQL、49原证据、archive seal以及可用空间。另绑定源plan文件SHA `62a347bdd144c80f1e8ebcbb4c81fb579b70068598aebb4120e4dc8317bc82da`；其nonce必须按真实row_to_json格式及8字节长度前缀重算，严格等于封存manifest中recovery_control.identity唯一行摘要 `348bacba4fc40399c20fd19e723a64b8dd24348613194f189f996cf035c34e04`，再作为目标cluster.nonce。不改dump、identity表或verify检查。调用原 `cluster.validate_inputs(protocol02_baseline)` 只读核输入，再 `binding.install(backend)`；不调用full `cluster.plan()`，不生成伪造的full-plan通过记录。
2. **新目标resource_id/DB/LOGIN/volume保持全新UUID，只有用于识别恢复数据的cluster.nonce沿用已封存且核验的源标记；全新system_identifier仍必须不同。** 私有单目标receipt写明 `scenario=immutable_archive_to_one_fresh_target`、随机execution_uuid、原backend、target_port和本协议SHA；cluster.nonce独立绑定封存源标记，不使用新execution_uuid。以该目标参数调用冻结 `cluster.create(directory, parameters, 'target', owner_label=manifest['owner_label'], backup_group=manifest['backup_group'])`。保留其900门槛、缓存digest、容器标签、loopback、memory384m/cpu1等原检查。owner仅本次新集群bootstrap；临时DDL仅restore，业务验收不使用它。
3. 在这个唯一新目标中，对原dump运行 `pg_restore --list`（只列dump目录，不是对象桶LIST），独立记录TOC/164 TABLE DATA及禁止不受控源LOGIN的检查结果；不使用宿主缺失的pg_restore冒称已列目录。
4. **调用restore前**先落新私有intent，固定目标locator及7个逻辑key/id/SHA/size/media_type。调用冻结 `archive.restore(target, manifest, copied_archive, new_object_values)`：原 `--exit-on-error --single-transaction`，原owner/ACL映射，恢复失败不补SQL/GRANT；完成后禁用临时DDL。随后由新非owner reader独立核164表逐表行数/内容SHA、13类完整定义、965 CHECK。只允许既定一条CHECK完整嵌套/扁平写法，其他差异全拒。
5. 第4步的同一次 `archive.restore()` 在DB核验后负责7个明确blob的唯一一轮真实PUT/GET SHA（总322字节）；本步核该轮证据并另加7次独立GET，不再次PUT。仅新前缀；不列桶、不读未登记对象、不DELETE。出现unknown先停止保留，不换key批量重试。旧7个孤儿不清理。本轮若未实际云写读，不借开发证据填通过。
6. 新reader真实SQL负例：即使关闭default readonly，仍不能INSERT/UPDATE/DELETE/TRUNCATE/DDL/序列写/RPC/SET ROLE；能读全部164表及空certifications。新API不能读certifications/CREATE/SET ROLE；worker不能CONNECT。所有负例回滚，不能由owner修权使它过。
7. 独立API子进程从308固定树启动ASGI（不对外服务）。登录恢复的合成成员；旧Cookie401；CSRF403；读原项目/知识/报告真实200；唯一新合成bid创建与同key重放一致；只允许新bid同步设置；第二新key及旧项目写、upload、operation为423。依据真实前后全表多重集合证明所有原历史行保留，旧outbox/operations/model_calls逐字不变，未知费不重发，恰好新增一个bid。另加自己的断言，不以 `application.check()` 返回true替代。
8. 原49项输入及封存源/目标dump/object副本运行后重新SHA。源仍不挂载，因此本轮只声明原档案文件未变，不声称重新验证源在线SQL/源云对象或原完整source→target→source序列。
9. 终止前再次比容器全ID、UUID标签、image与卷mount；只对本轮自己生成且身份一致的容器 `stop` 后 `rm`，不 `-v`。保留新卷、原卷、原档案、新receipt及7新对象，记录cleanup和free。失败同样保留证据，不自动重建。

## 允许结论与不在范围

只读检查只能证明封存输入的字节一致性、档案之间关系和纯比较边界。未来全部执行后最多证明“固定07+15SQL的小型合成完整档案在新单目标上恢复、非owner隔离、暂停状态下有限同步API工作”。不授生产RPO/RTO、异地灾备、断电恢复、在途任务/多段上传接续、最新A2/Score/Scale全schema、模型/RAG/Word运行、真实业务资料质量或新模型资格。

原协议01（SHA5696223c756a365abd6db5a202ad535a26d87c13486ccee376cbdc80cab2fa21）已另存，02只澄清restore内置云PUT顺序及前置intent，不改变生产源、原1536/900/256门槛。Seaweed临时停放由PM另行授权，不属于本协议隐含操作。

当前状态：**02首次真实目标因QA nonce编排错误被拒，已精确清容器、保卷；03修正待新窗口，尚未重试。**

03说明：02真实执行起始1670701056字节，目标创建后711200768字节；真实TOC含164 TABLE DATA。恢复在新reader身份校验报DATABASE_RECOVERY_IDENTITY_MISMATCH，未进入对象端口。此为QA把execution UUID误作恢复数据标记，非生产恢复实现缺陷；原失败、执行器和卷均保留，不在该目标修补或重试。03仅改编排标记绑定及其离线先验，仍需新1536 MiB与独占窗口。
