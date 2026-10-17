# 详细设计协议

主记录为[详细技术设计](../AI投标工作台详细技术设计.md)。这些文件描述待实现目标，不是现有应用的接口，也不部署或改动现有业务库。

| 文件 | 责任 |
| --- | --- |
| [protocol.schema.json](protocol.schema.json) | 137个目标对象的JSON Schema；评分输入明确拒绝额外字段，正式服务仍须核验来源与时刻 |
| [openapi.json](openapi.json) | 86条路径、93个操作；包含会话、CSRF、幂等与修订条件；组件由协议Schema映射，检查器核对二者一致 |
| [core.sql](core.sql) | 事务内核：跨项目外键、不可变记录、操作、草稿快照、提交和事件 |
| [domain.sql](domain.sql) | 全新领域建库：身份、登录、上传、证据、规则、事实、成绩、模型、模板、任务和导出 |
| [retrieval.sql](retrieval.sql) | 来源闭包、1024维向量、词法索引、知识版本、用途过滤和评分只读上下文 |
| [worker-functions.sql](worker-functions.sql) | 可执行Outbox/step租约、受限bid锁与研究预算/调用账本原子入口；其余事务按实现规约实现 |
| [roles.sql](roles.sql) | 无密码NOLOGIN能力角色，评分角色只执行固定用途读取 |
| [http-examples.json](http-examples.json) | 16份合成请求／响应示例，包含首次建项目、保存、隐藏分数和首次模型发布 |
| [identity_reference.py](identity_reference.py)／[固定向量](review-identity-vectors.json) | 完整冻结摘要、内容身份与nonce执行身份的精确投影；7组固定合成输入输出 |
| [sync_openapi.py](sync_openapi.py) | 从协议Schema同步OpenAPI组件；--check核对精确一致 |
| [开发编排](deploy/compose.yaml) | 固定镜像digest的PG＋pgvector与Kafka；仅开发单节点 |
| [topic初始化](deploy/init_topics.py) | 创建并核对6类topic配置，处理新建topic元数据传播窗口 |
| [execution-policy.json](execution-policy.json) | Kafka topic／group、手动offset、重试、状态转换和发布前提 |
| [retrieval-policy.json](retrieval-policy.json) | pgvector向量库、RAG全链路、用途边界、profile与正式评分完整范围 |

初始化SQL共104张表，按[数据与事务规约](数据与事务实现规约.md)顺序在空库执行。接口采用全新`/api/v1`目标前缀，业务响应不使用通用ID外壳占位。

范围外：业务服务实现、生产LOGIN凭据、真实云账号、实际模型资产与正式生产部署。实现顺序、配置和验收见[部署与交付手册](部署与交付手册.md)，固定事务算法见[接口与任务规约](接口与任务实现规约.md)，RAG与评分端口见[实现规约](RAG与评分实现规约.md)。这些设计不表示业务API已经运行。

协议Schema是对象形状主记录，OpenAPI内嵌组件仅作客户端生成输入，改动时由映射重新生成并运行一致性检查。JSON Schema验证不能代替来源闭包、业务数值关系、权限、闸门和事务检查。

上传续传、规则批量确认及沿用、事实撤回、矩阵状态、数值核验、报价与复盘、影子对照及实验恢复统一见[保留功能实现规约](保留功能实现规约.md)。开发准入及本次契约／建库核对见[开工检查](../../04-验证与测试/development-readiness-checks/README.md)；历史检查保留原输入身份。

## 公开技术依据

| 官方资料 | 支持的设计决定 | 证据边界 |
| --- | --- | --- |
| [Kafka交付设计](https://kafka.apache.org/41/design/design/) | 分区、消费位点、重发及外部系统的交付边界 | 版本化机制说明；Kafka事务不覆盖PG和模型调用 |
| [Python Kafka客户端](https://docs.confluent.io/kafka-clients/python/current/overview.html) | 手动offset、auto offset store、producer回执与poll | 官方说明；实际客户端与broker组合须故障验收 |
| [pgvector](https://github.com/pgvector/pgvector) | PG内向量存储、精确／近似检索、过滤与索引 | 项目资料；RAG质量依赖模型、分块与真实检索标注 |
| [Tiptap持久化](https://tiptap.dev/docs/editor/core-concepts/persistence) | 结构化JSON持久化；编辑器文档与正式文件分离 | 不证明DOCX无损往返或免费云服务可用 |

这些公开页面用于核对机制，实际生产效果依赖锁定版本、运行环境和故障测试。更完整的候选与许可比较见[执行路线复核](../../01-背景与市场研究/AI执行与工作流选型复核.md)。
