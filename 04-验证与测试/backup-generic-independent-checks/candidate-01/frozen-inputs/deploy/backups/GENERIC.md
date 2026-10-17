# 按目录发现 schema 的备份后继工具

旧 `run.py`、旧候选及其恢复证据保持原样。新入口 `deploy.backups.generic` 用 PG 目录动态发现非系统 schema 和普通/分区/物化表，manifest 以独立 schema/name 字段保存关系，避免带点或引号的标识符被拼接误判；不把过去的104表数量当作未来范围。

新模块沿用受限 API LOGIN 的 REPEATABLE READ READ ONLY + pg_export_snapshot + 同身份 pg_dump；数据库转储仍完整保存扩展及真实安装 schema。逐表完整行摘要、blob 引用及 catalog/extension 身份须在独立恢复库一致。源账号若拥有任何用户表，或为 superuser/BYPASSRLS/CREATEDB/CREATEROLE 则拒绝。

FDW 表在任何行读取前明确拒绝，防止遍历目录时暗中发远端请求；RLS 表明确拒绝，避免受限视图被误报为完整备份。物化视图如恢复刷新造成非确定性结果，摘要比对会失败，不吞掉差异。sequence、索引、触发器、视图和函数定义由完整 pg_dump 保留；此候选的独立逐行校验对象是发现的表，不额外宣称 sequence 并发状态、扩展外部资产、WAL/PITR 或生产容量已验收。

## 本地数据库组件（当前已实测）

```sh
.venv/bin/python -m deploy.backups.generic database-snapshot \
  --env-file .local/backend-checks.env \
  --container tender-workbench-dev-postgres-1 \
  --archive .local/backups/catalog-database-01
.venv/bin/python -m deploy.backups.generic restore-database \
  --env-file .local/backend-checks.env \
  --container tender-workbench-dev-postgres-1 \
  --archive .local/backups/catalog-database-01
```

数据库组件不 GET/PUT 对象存储，不包含对象字节，收据只能是 `passed_database_component_only`。即使对象引用列表为空，`restore-verify` 也拒绝把未包含字节的组件当完整备份。所有完整 dump、私有 manifest、恢复尝试与收据只在 `.local/backups`（700/600、Git忽略），公开证据只保存校验范围和非凭据身份。

## 完整对象档案（新入口待独立实环境验收）

`create` 对精确快照 blob 引用执行已有原生 OSS 端口 GET/SHA/尺寸检查，`restore-verify` 再恢复到本环境下全新 `restore-drills/<uuid>/` 并 GET 校验；不 ListObjects、不改桶配置、不覆盖原件。参数与上面一致，选择已隔离的原生 OSS profile。单对象200MiB、总对象2GiB与16MiB manifest 预算明确限制，密码/DSN/云签名不进参数和输出。这个分支复用已实测V1的对象端口，但新入口的完整 OSS 演练尚未执行，不能沿用V1证据称已通过。

恢复只创建全新随机 `tender_catalog_restore_<uuid>`，初始化 owner 仅在这个新库导入与离线收权。对全部发现 schema 的 PUBLIC、既有应用能力角色、API LOGIN及其继承角色收回表写入/sequence写入/用户函数执行和 schema 创建能力，只给 API 表 SELECT/USAGE；关闭 PUBLIC CONNECT，仅明确准许验证 LOGIN，设置默认只读。之后真实非owner读取所有表摘要，检查即便关闭默认只读也没有表写入或用户函数执行能力。源库和源角色全局关系不变。

工具不启动消费者，不校正Kafka offset、补派Outbox或恢复写流量，不生成可启动旧任务的环境配置；`full_application_restore_qualified=false` 始终保留。恢复失败留下的随机新库在私有 attempt 收据中记录，不能按模糊库名前缀清理其他任务资源。

## 真实新 schema 机制验证

```sh
BACKUP_GENERIC_INTEGRATION_ENV_FILE=.local/backend-checks.env \
  .venv/bin/pytest -q tests/test_backup_generic.py
```

测试只对自己新建的随机 probe DB 用初始化 owner 建现有五份 SQL 和额外 schema/表，然后以既有限定 API LOGIN 工作；没有对 release 库做热补。包含 `future_metrics`、带点 schema/带引号表名、真实 vector 类型行；导出快照后由另一个非owner连接提交新行，源可见而实际 pg_restore 后不可见。所有发现表的行摘要一致，vector 行在收回用户函数执行后仍可只读查看，future schema 实际 INSERT 在关闭默认只读后仍42501。源 probe 与恢复库均保留为只读供独立审查。全程对象存储与模型调用为0。

后续独立QA应在最终目标schema、真实已隔离OSS资料与候选源码冻结后跑完整create/restore-verify，核对对象字节、全部schema、方法/模板/字体/模型资产及Kafka恢复路线。当前数据库组件通过不能替代这些验收。
