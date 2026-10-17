# 后端运行与交付边界

这是全新系统。运行配置由私有`.env`或`deploy/run.py --env-file … --role api|worker`注入；显式profile存在时不再补读根`.env`。API、普通Worker、研究Worker、正式评分只读使用各自非owner LOGIN。初始化和运行分开，业务不使用owner或把API凭据发给普通Worker。

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
.venv/bin/python -m apps.api.bootstrap --login demo --name 演示成员
.venv/bin/python -m apps.api
.venv/bin/python -m apps.worker relay
.venv/bin/python -m apps.worker consume
.venv/bin/python -m apps.worker reconcile
```

bootstrap隐藏口令；自动化可`--password-stdin`，不提供生产HTTP绕过。API8800，web5173同源代理。Cookie `session`为HttpOnly/SameSite=Lax，HTTPS时Secure；全部写请求验证Origin/CSRF，领域命令再校验幂等与真实强ETag。口令、正文、签名URL、应用传输ticket不记录；uvicorn关闭access log。

当前代码注册了T0/PDF、评审准备及核心核对、RAG、authoring、research、SSE的实际路由。注册只表示有真实处理器，不表示各能力均通过独立验收。实际接口由`python -m apps.api.export_openapi`或`/openapi.json`导出，目标计划中未实现的接口不出现在actual路径中。authoring/research/RAG/解析有各Owner的独立候选及限制。

T0包含会话恢复/撤销、真实游标项目分页、PDF批次与单文件100MiB、字节核验、显式verified字节重绑、新文件/新版本CAS入库、历史版本、原页证据、下载及操作取消。DOCX直接上传仍415，multipart未实现。S3开发使用签名传输，OSS使用Cookie/CSRF和短ticket的同源私有传输，不改变桶CORS/ACL。文件head修订与版本序号分开；保留已核验字节不等于用户已同意新目标。

A1.1代码提供一键评审持久准备请求：解析和要求准备完成后，独立API控制面冻结真实稿件/规则/事实/来源全祖先与方法身份，再派核心核对→汇总两阶段任务。用户离开不会阻止推进。自动开关开启时合并未冻结最新稿；关闭时旧请求过期。冻结后输入不改写。读取入口有review-state、单轮manifest、历史列表、矩阵、问题、规则及证据。准备请求取消使用review-state强ETag，已冻结轮次使用operation取消；共享解析不因单次请求取消而终止。

```sh
# 独立API控制面，只能注入API角色；正式启用前先核对模型预算和目标环境。
.venv/bin/python -m apps.api.review_coordinator
# 普通Worker角色；与document消费者分group，处理已冻结准备/核心任务。
.venv/bin/python -m apps.worker consume-review
.venv/bin/python -m apps.worker consume-export
```

普通模型每次外调前先原子登记model_calls并检查当前generation/fence/owner/live lease/取消及预算。一次请求的准备和评审共享最多10次、最大输出token预留总和32768，单次不超过8192；SDK没有隐式重试。成功结果有实际对象SHA，可重放复用；reserved/unknown不自动补发，结果迟到不能越过取消或fence发布。实际usage/requestID用于追溯，不以普通chat接通授予正式评分资格。调用资料不超过明确字符范围，超限失败并列缺口，不截断后声称全文已读。

核心核对覆盖完整已解析正文，不用普通RAG片段代替完整评分。已知critical跨轮保留且按来源身份去重；新轮零发现、失败、关闭可选检测都不解决旧问题。只有有理由驳回或字节确已改变的新稿对同一有效规则完成明确复核才关闭。四层覆盖分别为source_scope/core_extraction/judgment/scoring，人工确认单列；不以运行比例冒充真实漏提率。正式评分/三次判断及模型准入尚未在该核心切片完成，不生成假分数。

配置本地解析端口时，新入库派发document/2并将其parser/config摘要绑定input_digest，服务身份改变会拒绝旧任务重放；旧document/1保持保守字层文字提取与真实缺口。新端口提供真实OCR/字层段落/规则网格表，物理页、bbox、结构及partial/gaps原样入库。扫描版式、图纸理解、复杂表合并和生产解析基线仍有独立验收范围；没有能力就不标完整。CPU端口不是Docling/Paddle已准入。

Kafka真实手动offset、固定group、每分区单在途；发布收据/effect/Outbox/audit/SSE同事务，外部网络在bid锁外。多阶段依赖完整登记，必需步骤全部成功才结束整轮。失联先废旧fence，再升代次；依赖失败/进程接管共用3次阶段尝试，最终持久失败和死信。未来/未发布同代Outbox不被对账抢跑。普通执行器拒绝研究处理器，研究执行器仅用研究角色。

开发机制验证示例：

```sh
.venv/bin/ruff check apps/api apps/worker packages/domain
.venv/bin/ruff format --check apps/api apps/worker packages/domain
.venv/bin/mypy apps/api apps/worker packages/domain
.venv/bin/bandit -q -r apps/api apps/worker packages/domain
.venv/bin/pytest tests/test_providers.py tests/test_domain.py tests/test_review_identity.py tests/test_actual_contract.py
RUN_MODEL_LEDGER_INTEGRATION=1 RUN_REVIEW_INTEGRATION=1 RUN_RECOVERY_INTEGRATION=1 RUN_CANCEL_INTEGRATION=1 .venv/bin/python deploy/run.py --env-file .local/backend-checks.env -- .venv/bin/pytest tests/test_model_ledger_integration.py tests/test_review_integration.py tests/test_recovery_integration.py tests/test_cancel_integration.py --assert=plain
RUN_SLICE_INTEGRATION=1 .venv/bin/pytest tests/test_slice_integration.py --assert=plain
```

机制库不能启动Kafka消费者/relay与其它库共group。评审纵向机制使用真实PG/S3/ASGI及明确MockTransport，直接驱动权威Outbox，不作为Kafka或模型效果验收。解析HTTP另用RUN_PARSING_WORKER_INTEGRATION=1显式启用，配置私有不打印。真实Kafka T0另测；真实模型业务、完整A1人工确认/补漏/事实/提交及生产恢复容量仍需后续独立测试，不能由开发自测替代。
