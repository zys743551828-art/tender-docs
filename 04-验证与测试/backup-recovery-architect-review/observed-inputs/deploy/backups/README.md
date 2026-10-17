# 手动备份与隔离恢复演练

这是新系统的开发环境备份恢复工具，不是旧系统迁移。当前完成真实 PG 17 与原生 OSS 的一致快照档案及独立恢复检查；不表示生产备份、异地容灾、WAL 连续归档或 RPO/RTO 已通过。

在工程根目录运行。私有配置文件须为 600；完整档案包含会话、私有投标资料、审计和研究数据，只保存在被 Git 忽略的 `.local/backups`，目录 700、文件 600。备份路径必须位于该目录，且每次创建新目录。不要把档案、manifest 或诊断原文放入公开测试报告。

```sh
mkdir -m 700 -p .local/backups
.venv/bin/python -m deploy.backups.run create \
  --env-file .local/external-release.env \
  --archive .local/backups/manual-01
.venv/bin/python -m deploy.backups.run verify \
  --env-file .local/external-release.env \
  --archive .local/backups/manual-01
.venv/bin/python -m deploy.backups.run restore-verify \
  --env-file .local/external-release.env \
  --archive .local/backups/manual-01
```

`--container` 可指定同一 PG 实例的 PostgreSQL 17 工具容器，默认 `tender-workbench-external-postgres-1`。源 profile 的数据库、工具容器必须属于同一个实例，不能把别处的 snapshot 导入这个容器；不匹配会失败。宿主不用安装 pg_dump/pg_restore。Docker 命令只按变量名传入 PGPASSWORD/PGUSER/PGDATABASE，密码和 DSN 不出现在参数或工具输出中。工具拒绝 superuser、BYPASSRLS 和表 owner 的业务读取连接。

## 一致性与档案内容

1. 以配置的非 owner API LOGIN 开启 `REPEATABLE READ READ ONLY` 事务，导出 `pg_export_snapshot()`。
2. 在该事务内取得所有 `design`/`retrieval` 表的行数、规范化完整行 SHA-256，以及 `blobs` 的 ID、逻辑 key、字节数、媒体类型和 SHA。
3. 使用同一个 API LOGIN 调用 PG 容器的 `pg_dump --snapshot`，在导出事务仍打开时完成数据库转储。导出完整数据库，保留 vector 扩展及其实际安装 schema；不依赖把扩展改装到 public。
4. 只 GET 快照实际引用的对象，通过现有 storage 端口应用当前 OSS prefix。逐一检查真实字节数和 SHA；既不列举桶，也不复制未被快照引用的文件。
5. 写入完整 dump、对象字节和私有 manifest。manifest 包含数据库/LOGIN/PG 版本/快照、不可变 backend/endpoint/region/bucket/prefix、表和对象摘要，不含 AK、secret、口令或签名 URL。manifest 的 SHA 文件用于意外损坏检查，**不是签名或来源认证**。

当前防护上限为单对象 200 MiB、对象总字节数 2 GiB；超过上限明确失败。这个上限不是生产容量承诺。失败目录可能保留未完成文件，但只有完整并通过 manifest 校验的档案才可进入恢复步骤。

## 恢复与只读检查

恢复操作先验证档案和全部本地字节，随后由初始化 owner **仅创建新的随机 `tender_restore_<uuid>` 数据库**，以 `pg_restore --single-transaction --exit-on-error --no-owner` 恢复。不接受指定现有数据库，不覆盖业务库，不给业务 API/Worker 传播 owner 凭据。

恢复库是离线检查库：仅在这个新库收回应用能力角色的表写入与函数执行权限，重新给 API 角色表 SELECT，并设置默认只读。再使用非 owner API LOGIN 重新计算全部业务表行数/完整行 SHA，逐项比较恢复后的 blobs 引用；另开关闭只读默认值的非 owner 连接，真实验证 INSERT 被权限拒绝。它保留业务内容用于检查，但不具备恢复流量的授权。

对象从本地档案恢复到环境 prefix 下面新建的 `restore-drills/<uuid>/`，按数据库恢复出的逻辑 key PUT，再通过该独立定位 GET 校验真实 SHA/尺寸；不修改源对象。收据只记录非密钥身份、校验范围和新建数据库/prefix。未启动 API、relay 或 Kafka 消费者，不会让历史操作自动重跑。当前仅支持具有独立 prefix 语义的原生 OSS 恢复目标。

成功恢复库和恢复对象保留供独立 QA/架构师检查。执行者应按收据确认归属后手动清理**自己本次演练创建的数据库和对象**；工具没有通用删库、清桶或热恢复命令。异常失败后也应核对本次创建资源；不能按模糊 bucket 列表清理原件。恢复步骤不会校正 Kafka offset/收据、废止旧 fence、重建实际模型资产、重新派发 Outbox 或恢复写流量，这些必须在完整恢复环境按发布流程独立验证。

## 独立验证与生产边界

```sh
.venv/bin/pytest -q tests/test_backup.py
BACKUP_INTEGRATION_ENV_FILE=.local/external-release.env \
  .venv/bin/pytest -q tests/test_backup.py
```

真实集成测试在导出 snapshot 之后，通过另一真实 PG 连接提交一行合成数据；源库能读到这笔提交，而 manifest 和实际恢复出的数据库均读不到它。这证明表清单与 dump 共享导出快照，不能用“备份期间暂停写入”替代。测试会留下一行明确标记的合成成员和新的隔离恢复资源。

档案在本地以 600 保存，**没有实现静态加密、密钥托管、远端持久化、WAL/PITR、跨故障域保留策略、自动调度或生产恢复流量**。生产交付应配置独立的持久目的地和加密 profile（例如受管 KMS 包封加密及独立备份账号），验证持久落盘与解密恢复后再增加自动化；当前不默认上传完整私有档案到业务桶，也不要求把长期加密密钥写进代码。本地 SHA 不能认证来自不可信来源的 SQL dump；只对操作员自己生成并保管的档案使用 owner 恢复。
