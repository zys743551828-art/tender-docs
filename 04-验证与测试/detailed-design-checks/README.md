# 详细技术设计检查

对象为[详细技术设计](../../05-技术与设计/AI投标工作台详细技术设计.md)及其[目标协议](../../05-技术与设计/detailed-design/README.md)。检查不连接现有应用，不调用真实模型，不把协议合法或隔离SQL机制正确当作完整产品验收。

## 检查范围

| 检查 | 内容与边界 |
| --- | --- |
| [check_contracts.py](check_contracts.py) | JSON Schema与OpenAPI 3.1验证；9份合法输入、21个非法反例；写请求幂等／CSRF；终态规则；54项需求落点与本地链接 |
| [probe_core_sql.py](probe_core_sql.py) | 自动创建、销毁无网络隔离PostgreSQL容器；执行核心DDL、关系约束与事务，测试24个并发副作用请求和24个并发事件；不测试产品服务实现 |
| [requirements-trace.json](requirements-trace.json) | 40项FR与14项NFR到详细设计的技术落点，状态均为目标设计 |
| [contract-result.json](contract-result.json) | 协议、反例与静态检查结果，含输入摘要和检查依赖 |
| [sql-result.json](sql-result.json) | 真实PostgreSQL执行结果、容器镜像digest、程序与DDL摘要 |
| [review-rounds.json](review-rounds.json) | 详细设计三轮审查的对象、发现、修正与实际证据；与产品方案三轮审查分开保存 |

完整API／worker联调、Kafka重启与消费者再均衡、真实解析／模型、Word排版、业务精度、正式部署与备份恢复尚未执行。SQL中的取消与fence用条件更新验证机制，正式服务仍须实现对应条件、锁序和事务；数据库不会自动替服务层执行整个业务策略。

## 可落地实施设计

当前完整实现规约、87表初始化模型、97个协议与72个操作的检查，见[实施设计验证](../implementation-design-checks/README.md)。本目录保留早期机制和各次设计检查证据；历史结果只对应各自输入摘要，不能当作当前新文件已验证。

## Kafka与RAG约束阶段的历史检查

该阶段设计确定Kafka＋独立Python Worker，RAG使用PostgreSQL＋pgvector，无旧系统迁移需求。该阶段协议包含46个定义、47条路径50个操作。检查覆盖手动offset、独立topic／group、PG延迟重试、禁止公开检索扩权和正式评分完整章节；结果保存为[kafka-contract-result.json](kafka-contract-result.json)，产品规格与架构资料分别保存为[kafka-product-regression-result.json](kafka-product-regression-result.json)、[kafka-architecture-regression-result.json](kafka-architecture-regression-result.json)。

历史审查和结果不覆盖。该阶段用户后续约束与输入身份保存为[kafka-design-record.json](kafka-design-record.json)；产品三轮后的修改使用[显式用户变更链](../prd-checks/spec-followups.json)，不将原三轮冒充新规格已重新执行三轮。

## 历史设计检查证据

三轮按领域契约、并发恢复、运行交付分别核对，记录见review-rounds.json。目标协议检查通过：42个定义、46条路径49个操作、7份合法夹具、16个被拒绝的反例，54项需求均有技术落点。

核心DDL在PostgreSQL 17.11中实际建库，21项机制检查通过，含24个并发副作用请求和24个并发事件。补充了同项目跨草稿混用的复合外键反例。临时容器已经销毁；无现有业务库改动。

[产品回归检查](product-regression-result.json)确认128条场景定义、抽象规则与既有三轮产品审查身份仍通过；[架构资料检查](architecture-regression-result.json)确认已有来源缓存及机制实验产物身份通过，未重跑此前五组实验。这里的“回归”是规格与证据检查，软件端到端执行数仍为0。

## 复查方法

使用Python 3.12与独立虚拟环境，依赖快照见[requirements.lock.txt](requirements.lock.txt)。它仅供设计检查，不是应用运行依赖。

~~~sh
python check_contracts.py --output kafka-contract-result.json
python probe_core_sql.py --image postgres:17 --output sql-result.json
~~~

第二条命令需要Docker及本地PostgreSQL镜像；程序默认不拉镜像、不暴露端口，创建随机名容器，使用tmpfs，退出时删除容器和匿名卷。镜像实际digest写入结果；复现既有记录应使用记录中的digest，而非假设可变tag始终相同。仅在专用开发环境执行。

更换输入后，已有结果的摘要不再代表当前文件，须重跑相应检查；不手工改结果使之通过。产品审查记录绑定当时输入；用户后续修改单独建链并重新检查当前文件，历史三轮摘要保持不变。
