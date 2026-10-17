# 实施设计验证

验证对象为[详细设计与实现规约](../../05-技术与设计/detailed-design/README.md)。当前目标是全新建设，Kafka与PG＋pgvector为基础设施；没有旧系统迁移或兼容任务。

## 已执行范围

| 检查 | 实际覆盖 | 边界 |
| --- | --- | --- |
| probe_database.py | 5份初始化SQL、87张表、真实PG＋pgvector，来源过滤／词法／向量、评分账号隔离、Outbox租约、分数边界及模型路由约束 | 使用合成文本与向量，没有下载真实Embedding权重或测试公司资料效果 |
| probe_kafka.py | 真实单节点Kafka＋PG，6类topic初始化、配置核对、delivery回执、PG提交后未提交offset的消费者重启重放、重复效果防重、broker重启后消费位点 | 机制脚本使用核心SQL收据，没有调用产品API或产品worker；不是三节点容灾与完整再均衡验收 |
| check_contracts.py | JSON Schema、OpenAPI 3.1、具体业务响应、首次创建只需项目名、隐藏预测不泄露值、54项需求落点与文件链接 | 静态与协议检查，不是页面或端到端软件验收 |
| probe_compose.py | 实际启动开发编排、87表建库、外部topic初始化、容器内查询，镜像固定digest | 没有启动业务web／API镜像；单节点明文仅开发 |

实际结果：

- [数据库与向量机制](database-result.json)：87表、27项检查通过。
- [Kafka重放与重启](kafka-result.json)：10项检查通过。
- [开发Compose完整启动](compose-result.json)：5项检查通过。
- [契约与规格](contract-result.json)：97个协议、67条路径72个操作，含12份接口示例与21个非法反例。
- [产品规格](product-result.json)与[架构资料](architecture-result.json)：均通过，软件场景执行仍为0。

检查记录绑定脚本、SQL和契约摘要，文件更改后应重跑受影响部分，不手改结果使之通过。

[observations.json](observations.json)记录实测发现的初始化元数据传播问题及修正。历史三轮与Kafka约束检查保持原有输入身份，本次细化以独立用户变更链及检查记录保存，不回写历史成功结果。

## 复现

使用Python 3.12、Docker和独立虚拟环境。安装[requirements.lock.txt](requirements.lock.txt)仅供设计验证，不作为应用生产依赖。

~~~sh
python probe_database.py --output database-result.json
python probe_kafka.py --output kafka-result.json
python probe_compose.py --output compose-result.json
python ../detailed-design-checks/check_contracts.py --output contract-result.json
~~~

机制程序仅使用专用随机名容器／Compose项目；PG不暴露端口，Kafka重放实验绑定宿主随机127.0.0.1端口；Compose实验使用文档声明的127.0.0.1:55438／19092，端口已占用时应停止而不覆盖现有服务，内容全为合成资料。无生产连接和真实模型调用，退出时删除临时容器及匿名卷。需要先取得各程序固定digest的镜像，程序不隐式拉取任意tag。

真实模型、OCR／复杂文档、DOCX／PDF排版、页面联调、多节点Kafka故障、生产存储、真实RAG召回和正式评分精度仍按交付工单验证。机制检查通过不提高这些能力的已验证状态。
