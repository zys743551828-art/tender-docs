# 通用备份：原生 OSS 完整开发恢复候选

这是新的完整对象演练证据，未修改 V1 或通用数据库组件的冻结源码与证据。新入口 generic.py 保持原已审摘要；这次补充真实 native OSS 执行，而不是引用旧 V1 或数据库组件通过结论。

独立 profile `.local/external-release.env` 只在进程中读取；以该实例 owner 仅创建全新随机 source probe DB、执行当前初始化七份 SQL 及额外 `future.native`/带引号表/vector，然后通过受限非owner API LOGIN 写合成业务。没有修改 release DB、现有业务表授权或全局角色成员关系。模型、消费者、Kafka 操作均为0。

对象访问限于本次随机 `generic-native-drills/<uuid>/` 子前缀中的精确合成 key。快照内三个已登记 blob/原件：空字节、中文文本及约512KiB二进制，共524610字节。API 的 RR READ ONLY 导出快照后，另一个非owner连接真实提交第四个 blob/原件和第二条未来表行。相同导出快照用于 pg_dump、108张发现表逐行摘要及 blob 精确引用选择；快照档案只有前三个对象和第一条未来表行，不停服、不伪造快照。

完整 create 分支原生 OSS GET 精确引用的三个对象，对尺寸与SHA256校验，私有本地档案700/600；manifest保存 backend/endpoint/bucket/prefix、导出快照/源身份、catalog/extensions和完整表摘要。dump、全部对象、manifest与恢复收据均留在Git忽略的.local/backups，不复制进公开候选。

restore-verify 分支仅创建全新随机 restore DB，owner只导入该新库并离线收权；恢复对象仅PUT到新随机 restore-drills 子前缀，精确GET/SHA/尺寸校验。非owner API读取108表行摘要、全部schema/extension、3个blob及原件身份一致；带点schema、引号表及向量值可读。关闭默认只读设置后，真实INSERT仍42501。快照后第四个对象与新行在源库可见、恢复库缺失。完成后source probe亦被收权为只读，供独立审查。

单项真实集成测试通过；本次准确原生调用为 PUT7、GET9（含独立回读3次），LIST0、DELETE0。所有已引用source4个对象、restore3个对象与两座新库均保留，不扫描用户桶、不改CORS/桶策略、不删除任何对象。独立检查还核对完整档案权限和Git忽略。

另运行8项本地单元/边界回归；3个需显式环境的集成项在该不带环境命令中正确skip，不能将skip计为通过。完整native项在独立显式命令真实执行并通过。最初夹具遗漏bids.settings被NOT NULL拒绝，发生于任何OSS请求前，已修复；这不是备份工具缺陷或通过证据。

本结果是完整数据库+引用对象的开发只读恢复，不授予 full_application_restore、生产RPO/RTO、PITR/异地加密备份或Kafka写任务恢复资格，也不证明正式模型资产、业务评分或真实官方材料质量。生产容量及最终目标schema变动后仍须复核。

复验：`BACKUP_GENERIC_NATIVE_ENV_FILE=.local/external-release.env .venv/bin/pytest -q tests/test_backup_generic_native.py`。该命令每次创建自己的新库/前缀并保留已引用资源。精确现有只读样本与私有档案路径见 development-result.json；只读复核无需重发对象请求或重跑写演练。
