# 研究来源资格 SQL 静态复核

结论：当前观察 SQL 可进入全新独立数据库初始化及受限 LOGIN 实证，无静态阻塞。该结论不等于研究候选 02、合法执行路径、应用事务或实际权限已通过。

SQL 的实际 SHA256 见 `source-sql-static-record.json`；副本 `observed-research-source-qualification.sql` 固定本次输入。未向任何数据库执行该 SQL，未热补历史库。

两个 append-only 映射仅 API 可 SELECT／INSERT；研究 Worker 不获得映射或任意来源扫描能力。资格函数为 SECURITY DEFINER、固定 pg_catalog／design／retrieval／pg_temp 搜索顺序，撤销 PUBLIC，仅授权研究 Worker。返回资格、原因及摘要，不返回正文或标签。

上下文从 operation → run_attempt → run/item/sample → 当前 submission/outcome 头 → review/input → chapter/rule 与 preview 映射派生，带 generation／fence／owner／有效 lease；不接受 Worker 自报白名单。正文完整证据与规则证据取精确集合，逐 parse／物理页／原件 hash 校验；祖先集合和 head 用 typed UUID／时间／修订逐项比较，校验 published、prebid、原业务 as_of 窗口。非 read committed 明确拒绝，避免图锁后读取旧快照。

后续应用验收条件：API 以 bid → graph 顺序在同事务构造 canonical parse-child／root、capture、插入胶囊；capture 自身为 invoker 只读，不能把它误当取得图锁。研究 reserve／finish 必须持已有执行权锁后取图锁，资格与 effect 同事务提交；对象写入与模型计算全部在关键锁之外。API 冻结逻辑使用共享 canonical helper；SQL 本身不重新推导 UUID 算法，须实证旧 parse source 搭新 evidence 被拒。新空库须同时证明合法路径可执行及越权／撤回／重发布／RR／未来时点等失败路径，不以静态函数成功创建代替。
