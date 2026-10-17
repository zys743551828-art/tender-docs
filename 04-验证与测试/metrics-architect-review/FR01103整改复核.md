# FR01103整改架构复核

结论：FR011冻结后端模块本次通过，MET01与MET02均闭合，剩余本范围阻塞0。main接线、提交hooks、前端和正式预测资格仍独立验收；不声称完整FR011产品已交付。

输入candidate03/candidate.json SHA256 `92cc733d0fb295d325c827879b81b705c8aef6ab691d298a5d4cf72a81489c46`，核验13冻结源与13观察依赖；实用副本metrics router/results/views/predictions/snapshots/core/records，依赖字节与冻结清单一致。01原source候选／未来漏洞、02历史来源误拒反例与报告均原样保留。

sources.inspect将知识可见cutoff与系统有效窗分开：known_at不晚于present/as_of/业务known_at三者最早；head.valid_from及valid_until分别核实际观察as_of与当前，而非历史业务cutoff。没有把当前系统头回填到历史；过早as_of无真实系统发布证据时仍拒。authority schema升为v2，旧冻结v1不默默取得新协议资格，需要用户真实重新核验；旧记录身份与历史保留。

MET01的正向canonical官方原件／parse／页／逐字证据、全祖先published/用途/时效、完整闭包和当前authority摘要守卫保持。核验RC，bid→graph→具体source heads锁至追加决定；同键历史结果重新投影当前有效性，不复活旧成功。

架构师在全新隔离PG17+pgvector、受限非owner API/Worker、真实登录ASGI、合成SQL原件/evidence执行43项通过：候选39＋自有原candidate/future_known两反例、历史来源现在发布正向、真实graph等待一项。三个原反例回归分别证明：候选或明日才知来源核验409/0正式分母；资料早已知而系统今天才发布可200/verified/usable=true，verified_only为1/1。真实等待以pg_locks确认核验确实阻塞在90127图锁，持有者撤回root并提交后409且0新核验记录。

候选机制继续覆盖业务cutoff、防未来/未发布/当前未生效/过期/撤回，缺canonical/head/parse、祖先与closure变化、旧authority/重放、bid先于graph锁序，以及阶段/成绩独立、精确分母、真实动作epoch、并发CAS、冲突不退旧头、风险快照未知/去根/回滚、隐藏预测与19/20阈值。预测producer尚无实际业务权威，真实在线配对0，阈值只验软件数学，不授模型资格。

证据candidate03-review-record.json与candidate03-independent-junit.xml，四项自有regression.json记录实际断言。对象及模型均未调用，无private读取/收费/消费者。未重跑真实官方材料、生产或整体前端；当前manifest仍main_wired=false，不能将本模块机制通过推广为全部接线通过。
