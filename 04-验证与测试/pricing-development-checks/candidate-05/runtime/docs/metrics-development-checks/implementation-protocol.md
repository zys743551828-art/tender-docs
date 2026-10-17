# FR011 实现协议

按产品《业务结果与指标最小交接》实现独立业务阶段，不修改现有成绩 outcomes、冻结目标 SQL、main/exporter。新 router 由主开发集中 include，actual schema 由本包扩展函数交主 exporter 接线。

存储复用现有 audit_entries，但不是按模糊事件时间挑一条：每笔变更先取得本 bid 行锁，跨 metrics 命名空间计算单调 metrics_epoch，使用 clock_timestamp() 明确记录实际动作时刻并追加专用版本记录。BusinessResult 内容不可变，核验追加独立事件；每 submission 显式 head_revision 在登记、更正、核验均递增。读取认 epoch/head_revision；旧记录始终保留，当前冲突/失效绝不回退旧 verified。审计事件带 stream 增量；读请求 REPEATABLE READ READ ONLY。同键请求复用既有 Command，If-Match 属于幂等摘要。没有新 SQL 或 release 热补。

拟定实际路由：GET /bids/{bid}/submissions/{submission}/business-results（含唯一当前头与历史、head ETag），POST 同路径登记（If-Match 对空头revision0），GET /business-results/{result}，POST /business-results/{result}/corrections，POST /business-results/{result}/verification-decisions；GET /metrics；GET /bids/{bid}/round-trends。登记和更正都要求原 submission 身份可验证且为当前有效 head，stage 不推导成绩。所有核验必须针对当前结果头，verified 要官方原件角色、逐字块/物理页、公布/获知时点、真实核验人及冻结提交身份一致。注册可保存 user_reported pending；官方依据不足不授 verified。来源withdrawn/失效、身份变更单独给 unusable_reasons。

有效提交只从 submission_heads 取得，按被冻结确认的工程/标段/轮次/主体分组。跨项目出现同单位两个无法判断的当前 head，作为冲突整体排除并计数；绝不按最后创建时间猜选。时间窗按 submitted_at，as_of 作为实际记录观察截点，返回全部头与结果身份摘要、verification_scope、排除数和缺口。业务记录登记时间不迁移实交时间窗。

入围率严格 4/6 类别、最终中标率严格 awarded/(awarded+not_awarded+not_shortlisted+rejected)，取消/流标/pending/conflict 不入分母；全部使用 Decimal 字符串，零分母 null。已核实/未核实单列，verified_only 明示排除。

风险快照提供 submission writer **同事务**调用 `freeze_submission_risks` hook，最多一条不可变冻结记录；没有该记录的旧 submission 不由 GET 补写。实际 submitted_at 早于当前登记且无逐项可证当时审计，保守 basis unknown、计数 null；不把今天 lifecycle 反推当时。当前风险来自最近适用评审，沿 finding_lifecycle.origin 去根，只计否决/强制义务问题；resolved_by_recheck 与 dismiss 分列，confirm/defer 不解决。已知快照只聚合其明确覆盖部分，并输出已知/未知数量。官方否决只读业务 stage=rejected，不作因果归因。

预测不能由当前 predictions.created_at 推定事前未见标签。主配对冻结提供内部 hook，只有未来正式评分 producer 的专门服务端完成 authority、成功 operation、完整章节/三次判断/清洁输入权威一致才允许冻结；本次不新增任何供浏览器伪造该 authority 的写入口。每个 review/body/method 的预测在提交前冻结后固定一条，提交 hook 再绑定实际提交。现系统没有这样的正式 producer authority，所以真实业务在线配对默认零；单纯历史 prediction 或事后回测全部排除并给原因。20 对门槛和百分制 MAE/bias用显式合成权威夹具验机制，不声明真实已准或模型资格。

趋势按 review 输入/方法/正文/规则/事实/范围固定身份，项目内稳定分页；方法/规则/范围变更和缺输入均不可比并注明原因，正文变化单独标出。官方技术分只附当前实际提交所绑定的真实 review/body。show_prediction=false 时服务端完全去掉数值、区间、误差和差分，聚合误差排除对应配对；不返回可旁路反推的总误差和部分差分。0/19 对误差 null，20 起展示 Decimal MAE 和有符号 bias，回落19立即隐藏。

待主 owner 接线：router include、actual OpenAPI extension；所有标准提交/线下补录/导入的写事务调用冻结风险与主配对 hook。未接线不能称 MET-08 的实际登记体验完成。备份现有 generic 表快照可纳入 audit 中新记录；后续独立演练将核实所有新 schema 的表目录与扩展依赖，不沿用旧104表计数声称完成。

## 集中接线约定

- `app.include_router(apps.api.metrics_routes.router)`；导出 actual OpenAPI 时调用 `packages.metrics.contracts.extend_openapi(actual)`，使用新增 BusinessMetricsView / BusinessRoundTrendPage，不能把旧目标 MetricsView 当实际投影。
- `packages.metrics.snapshots.observe_review_risks(c, *, bid, actor, command, review_id=None)`：评审最终 operation 已 succeeded 且 current_review_id 已指该轮，或人工 finding 决定完成后，在原事务调用。普通 Worker 使用既有受限 `design.lock_worker_bid`，无 bids UPDATE/FOR UPDATE 权限也可追加观察；API 用普通 bid 行锁。未新增权限或 owner 业务路径。
- `freeze_submission_risks(c, *, submission, actor, command)`：submission 可以是尚未 INSERT 的 prepared dict，须有 id/bid_id/body_version_id/review_id/submitted_at。在同事务先生成 ID、freeze、最终 INSERT，把返回 snapshot 放 evidence.open_risk_snapshot。失败一起回滚，无孤儿记录，不 UPDATE append-only submission。复用相同 submission.id 返回原不可变快照。
- 当前适用观察同时校验当前 body/material/rules/facts/content_checks/method 与来源闭包；当时观察与登记时完整上下文不同即保守 unknown。不承诺对已有历史记录任意重建，也不把晚于 actual submitted_at 的观察倒签成事前证据。
- 快照 `scope_definition=disqualification_and_mandatory_root_findings`、`not_all_submission_blockers=true`。FR011指标不是全部闸门；标准提交必须另外冻结完整 registration_blocking_findings/gate_snapshot。
- `packages.metrics.predictions.bind_submission_prediction(c, *, submission, actor, command)` 也支持 prepared，同事务写独立不可变配对 authority，返回 payload 或 null，不二次更新 submission。没有事前预测 producer authority 时为 null，不能阻断有据线下实交。
- `freeze_prediction(...)` 仅面向未来正式评分 producer 的内部接线：要求专门完成 authority、成功 operation、全部 scoring rule 的完整章节包及三次有效判断、干净且未见标签、事前来源有效；没有浏览器可调用的造资格入口。目前应用尚未产生这种正式 authority，真实在线配对仍为零。

测试入口：`RUN_METRICS_INTEGRATION=1 .venv/bin/python deploy/run.py --env-file .local/backend-checks.env -- .venv/bin/pytest -q tests/test_metrics.py tests/test_metrics_integration.py`。该配置仅选既有隔离本地 PG；不启动消费者，不向对象存储或模型发请求。所有 source/review/prediction/outcome 证据行均为明确合成机制夹具，真实官方结果数和正式模型资格数均为零。每次执行保留合成身份、审计与当前头；不清空共享库。
