# 后端运行与切片边界

从新工程根运行，配置由本地`.env`或进程环境注入；生产须分进程注入对应能力凭据，不能使用初始化owner。数据库与Kafka/S3初始化由deploy负责，不重复执行建库。

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
.venv/bin/python -m apps.api.bootstrap --login demo --name 演示成员
.venv/bin/python -m apps.api
.venv/bin/python -m apps.worker relay
.venv/bin/python -m apps.worker consume
.venv/bin/python -m apps.worker reconcile
```

bootstrap交互隐藏口令，自动化可用`--password-stdin`从标准输入提供至少12字符口令，不提供HTTP初始化绕过。所有成员共享项目访问，正式人工决定等权限并未授予Worker。

API为`http://127.0.0.1:8800`，浏览器从5173同源代理访问。Cookie名`session`、HttpOnly、SameSite=Lax，HTTPS配置下Secure；登录与全部写请求校验精确Origin。登录返回AuthView内CSRF，条件写再携带真实If-Match及Idempotency-Key。登录失败按账号与来源摘要在PG审计中限流，不记录口令。`GET /auth/session`只恢复当前有效会话。

当前实际实现：登录/退出/恢复、项目创建和签名游标分页、上传批次、PDF单传S3授权、single状态/刷新/终止、后台核验、显式重新确认目标、文件head CAS批次提交、操作查询与取消、材料列表、物理页文字证据及原件下载。所有业务前缀`/api/v1`。完整实际路由和形状由`python -m apps.api.export_openapi`导出，`/openapi.json`相同；目标快照的未实现路由不出现在实际OpenAPI中。

文件head修订与版本序号不同：空head=1；首文件版本序号=1，提交后head=2。同字节当前head复用版本；重新绑定保留旧目标链及核验结果，选择后仍需最新head CAS。核验先检原字节SHA与大小，正式内容键不暴露PUT签名。S3签名/会话期限与7天未入库核验保留期分开。PG最终提交前核对对象仍存在，缺可信SHA元数据时实际回读校验。

Worker使用真实Kafka、固定group、手动offset、有界线程池、每分区单在途；阶段领取先受限bid锁，再operation/step，执行权用generation/fence/owner/lease。远程核验/解析在事务外，发布结果/收据/审计同事务，heartbeat30秒。瞬时依赖错误最多3次持久Outbox重投，最终失败写PG和死信意图；失联扫描接管过期租约，取消不允许迟到结果发布。PDFium调用在进程内串行锁保护，避免非线程安全的底层PDF操作并发。

本切片支持PDF、单文件上限100MiB，尚未实现multipart。DOCX在创建上传时415，不声称已保存Word原件。PDF仅提取物理页文字；OCR、结构化表格和图页解释未接入，解析返回partial和真实缺口，bbox为空则整页查看。规则提取、核心核对、RAG、正式评分、章节编辑、提交登记与结果核验均未交付；开始评审明确503 MODEL_NOT_CONFIGURED，没有绿色通过、假风险、假分数或假报告。来源范围的partial不等于评分或检测质量。

开发自测：

```sh
.venv/bin/ruff check apps packages/domain tests
.venv/bin/ruff format --check apps packages/domain tests
.venv/bin/mypy apps packages/domain tests
.venv/bin/bandit -q -r apps packages/domain
.venv/bin/pytest -q tests
RUN_SLICE_INTEGRATION=1 .venv/bin/pytest -q tests
```

默认只执行4项无外部依赖自测；纵向用例需专用真实PG/Kafka/S3环境，会创建合成成员/项目及不可变审计，不删除或冒充真实业务资料。用例启动并只停止自己的relay/consumer/reconcile进程。独立测试由qa执行，开发自测不能替代其结论。生产容量、多实例故障、真实OCR/模型、正式存储供应商协议及恢复演练仍待对应验收。
