# 技术总满分SQL增量复核

结论：C的SQL差异闭合TS01集合矛盾，所读增量没有新增静态阻塞，可由PM仅在另一个全新隔离库初始化。B原件和集合反例保留，不热补B或既有业务库；本结论不是SQL运行、应用或满分权威资格验收。

输入C SHA256 `ba5bd56c4ae03628cfac01d085cf8eb10fa95b094d3d554f2e866db10bb5dfad`。对照B `3fdce8d604ad1f17bf324330b7d15078b8cc063fa883eaa201852e67d48e9674`，逐行差异见 [B-C.diff](B-C.diff)，身份与范围见 [review-record.json](review-record.json)。

前段claim全集已改为从technical_scale.evidence.source_id经服务器source_ancestors推导的DISTINCT document_version_id；不再等于直接quote文档。完整祖先身份核验又加入node.digest=实际同bid原件blob SHA。历史claim校验移出直接evidence循环，对每个选中祖先文档映射逐版本核实际doc digest、同bid/submission/Subject、当前confirmed claim decision、current_proofs及存在/本方已知/效力覆盖提交时点，另拒事实时序早于content_existed。原直接PDF证据与Word来源祖先不需要共享伪造的parse/evidence才能进入集合；多余和遗漏映射仍严格拒。

原B中的函数权限、RC→91143→bid→graph锁序、服务端created_at、版本/决定/映射封口、最大值有限性、完整声明token及physical1..N页、当前身份与效力窗、聚合unknown/unsupported、score/Worker无新权和formal不可用均无变更。本次没有重新声明完整SQL已执行通过。

真实新库验收须有普通PDF正向、独立双版本来源闭包的充分历史声明正向；缺任一祖先claim、错Subject/提交/digest、claim pending/撤回、失效proof或事实不覆盖submitted_at均拒；确认与来源/决定头变更并发正确串行；API保存须同SQL全集而不是客户端允许列表，原件实际字节在锁外核验后再CAS。代码8源、45pure和API同步是开发交接，本次仅静读SQL delta，尚未独立复验它们。

历史正向不代表“Word当年已知”可以自动证明“2026新转换PDF的精确版本2019已知”。每个版本的声明都必须有本协议允许的独立充分依据；若后续采用派生表示的专用资格，须另行明确产品/输入authority，不能靠本次集合修复放宽时间事实。本次只解开有据合法双版本集合的结构矛盾，不授任何真实案件历史资格。

实际仅文件读、摘要和diff；零SQL/DDL/数据库连接、账号、对象、模型或消费者操作。后续实际错误须保留首次失败，不回填本静审为运行证据。
