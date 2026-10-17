# 外部服务适配

本模块实现原生阿里云OSS和DeepSeek生成端口。API仍为现有PDF切片；适配器接通不代表核心评审、RAG、研究或正式评分已交付。业务接入模型前必须另外实现持久调用账本、预算和当前执行代次校验，不得直接交普通Worker失败重试器重复付费。

## 环境选择与隔离

安装根目录 `requirements.lock.txt`，其中原生SDK固定 `oss2==2.19.1`。DeepSeek使用已有 `httpx==0.28.1`。私有配置通过部署入口注入，不把配置值写入脚本、日志或Git。

现有开发环境继续选择 `OBJECT_STORE_BACKEND=s3`。OSS环境必须使用新的隔离数据库/部署配置，不能修改现有S3数据库所依赖的全局存储配置。同一数据库今后需要混存时，应先持久化profile和对象定位，当前端口没有宣称支持混存。

OSS要求 `OBJECT_STORE_BACKEND=oss`、官方杭州service HTTPS端点、`OBJECT_STORE_REGION=cn-hangzhou`、`OBJECT_STORE_BUCKET=tender-base` 和相应官方bucket HTTPS地址；键名依次为 `OBJECT_STORE_ENDPOINT`、`OBJECT_STORE_REGION`、`OBJECT_STORE_BUCKET`、`OBJECT_STORE_BUCKET_URL`。凭据仅从 `OBJECT_STORE_ACCESS_KEY`、`OBJECT_STORE_SECRET_KEY` 读取。端点不接受请求参数覆盖。

可选 `OBJECT_STORE_PREFIX` 默认空。非空时必须为ASCII字母/数字/下划线/短横线组成的层级、以 `/` 结尾，例如 `workbench-environments/<随机身份>/`。适配器统一为head/get/put/sign/delete添加一次该前缀，数据库和API继续使用逻辑key。禁止路径跳转。部署创建一个新的前缀后应保持稳定，不能把已有逻辑引用重新解释到另一前缀。

## OSS端口

`storage.client()`选择原生 `OssClient.from_env()` 或原S3客户端。现有 `storage.get(key)`、`put(key, bytes, media)`、`sign(key, method, extra, seconds)` 保持调用形状。原生facade支持 `head_object`、`get_object`、`put_object`、`generate_presigned_url` 和定点 `delete_object`，不提供bucket管理/遍历接口。

原生V4签名保留环境proxy和CA，禁止重定向，SDK没有隐式请求重试，连接5秒、读取30秒。SDK原始日志关闭，错误仅转换为固定错误码和文字。缺失对象保留现有 `ClientError/NoSuchKey` 分支；鉴权失败、连接失败、异字节冲突分别明确返回。

正式写入携带 `x-oss-forbid-overwrite:true` 和业务SHA元数据。首次写返回版本身份（服务提供时）；同key冲突仅在读取实际字节并确认完全相等后复用，否则 `OBJECT_CONTENT_CONFLICT` 且不覆盖。HEAD、ETag和CRC均不替代业务SHA核验。

签名授权最多900秒。正式 `blobs/` 不签PUT；仅 `staging/` 与合成探针 `workbench-checks/` 可签PUT。PUT默认签入 `Content-Type: application/pdf`，调用者必须发送相同头；其他媒体类型由可信调用者用 `extra={"ContentType": ...}` 明确提供。GET不签额外上传头。签名URL只交授权调用者，不打印或保存到验收报告。

## DeepSeek端口

`DeepSeekClient.from_env()`读取 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`。仅接受官方HTTPS根地址或 `/v1`，默认 `deepseek-flash`；旧 `deepseek-chat` 不被自动替换。配置支持的模型范围仅说明本阶段生成端口支持，不能作为正式评分准入。

同步调用 `client.generate(messages=[{"role":"user","content":"..."}], max_tokens=256, json_schema=..., temperature=0)`；异步服务使用 `await client.generate_async(...)`。JSON Schema可省略，提供时要求单JSON对象并校验输出。远端引用、悬空根内引用和非法Schema在付费前拒绝；输出重复键、NaN、错误结构、截断、模型身份不符或usage不合法均拒绝。输入、最大输出、响应字节数有限制。

每次调用恰好一次HTTP POST，不流式生成、不自动修复、不自动切模型、不重定向、不隐式重试。显式关闭thinking；默认总deadline45秒覆盖整个网络交换与慢速读取，并有连接/读写分阶段超时。沿用环境proxy/CA验证。

成功返回 `Generation`：`content`、可选校验后的 `data`、`usage`、`request_id`、`response_id`、`model`、`finish_reason`；`formal_scoring_eligible` 固定false。没有HTTP请求标识时以响应身份关联本次返回，两种身份均保留。失败 `ProviderError.details.billing_state` 为 `unknown` 或 `usage_reported`，保留已知usage及安全请求标识，不回显prompt/原始响应/凭据。`retryable` 固定false，未知是否计费不得自动补发。

## 验证界限

`tests/test_providers.py`仅离线传输边界测试，不连接真实模型或用户对象；覆盖单次尝试、总时限、输出/usage/Schema校验、日志安全、原生签名、隔离前缀、条件写和实际字节冲突处理。`tests/test_slice_integration.py`继续使用原开发S3/PG/Kafka验证PDF全链路。

真实外部调用由独立测试统一执行：一次短DeepSeek JSON请求和 `workbench-checks/` 下随机合成OSS对象，记录在 `tender-docs/04-验证与测试/provider-integration-checks/`。不扫现有对象、不改bucket全局设置；开发没有另发模型请求。真实请求与最终源字节的微小前置Schema校验差异在候选清单中单独记录，不能声称最终源所有分支均已真实付费执行。
