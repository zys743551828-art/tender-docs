# 分片状态与集中接线说明

当前为待独立验收开发候选；不修改桶 CORS、ACL、版本策略。冻结的 single 原件通道保持原实现。

主开发接线：导入 `from packages.uploads import service as multipart` 并 include `apps.api.multipart_routes.router`。现 create_upload 完成 auth/UploadRequest 验证后，若 `multipart.wants_multipart(body)`，返回 `result(multipart.create_upload(bid_id,r,a,body))`。现 complete、parts、refresh、abort 在鉴权之后按 `multipart.is_multipart(upload_id)` 分流到同名 hook（parts 为 get_parts），传当前 session；multipart 其余返回值与现 result 包装一致，get_parts 直接为 UploadPartsView。complete 分片分支强制 If-Match，幂等摘要含 If-Match。actual OpenAPI 请主 owner 集中更新。

8 MiB 分片，最多 10000，首次及刷新最多 128 条；同源 PUT 绑定 Cookie/session/member + CSRF/Origin + 方法/上传ID/项目/逻辑key/片号/准确大小/revision/声明整文件SHA/到期。票据不含原生 UploadId，浏览器不接触云签名；服务端仅用已登记的对象与原生 UploadId。浏览器调度最多 4 并发，服务端每进程也有 4 个 byte transport slot。没有声称跨进程全局4并发限流。

provider_upload_id 采用 `twb-multipart/v1:` 加严格 JSON：phase/key/nonce/provider_id 固定字段；普通 provider ID 不作状态解释。phase 为 init_reserved/init_uncertain/ready/completing/completed/aborted；真实 provider_id 只在数据库/请求内存，永不作为响应或日志。库里仍是当前既有 text 字段，无 schema 热补。

创建先以短事务持久化 upload、binding、transfer、命令与稳定同源 grant；init_reserved 后仅拥有首次 nonce 的处理器执行一次原生 init，远端请求期间不持 bid 锁。精确临时key候选在 `.local/multipart-initialization-candidates.jsonl` 600 登记，无 provider_id。远端返回后短事务 CAS 绑定真实 ID；PG 确认最多补查/写一次，不重发 init。响应未知记 init_uncertain，保留稳定 upload_id，不 list bucket、不自动再次初始化，用户可创建新的传输。进程若死在预留与调用之间，旧请求仍不会自行重复 init；可查询上传 error_code，必要时新建传输。取消遇到已知远端ID则事务提交后精确 abort，未知ID只停止授权保留候选，不删正式原件。

GET parts 只在 ready/authorized 状态调用原生 ListParts，最多 11 个 1000 条分页，核验片号、重复与准确大小，随后短事务 CAS 保存观察。返回列表来自本次存储观测，数据库旧缓存不是完成依据；终态请查 UploadView。

刷新强 ETag/幂等，单一短事务递增revision并生成窗口授权。旧在途远端分片写可能已经成功，后置 CAS/票据检查只阻止宣布该旧请求成功，**不声称撤销已经发生的远端写入**。完整 SHA 校验和 verify Worker 的二次真实字节核验负责阻止旧字节生成正式版本。

完成先校验完整1..part_count receipts并短事务把 state 设 uploaded、phase completing、nonce 设完成命令ID、revision+1，冻结后续授权/分片发布/取消；未完成命令用内部 pending 响应持久化，不对外返回这个内部响应。同一命令重试可恢复，不同请求/键不能抢完成身份。精确 HEAD+全量GET/SHA若已发现组装成功则直接对账；否则 ListParts 比对真实 ETag/大小后调用原生 complete，丢响应仍以精确 HEAD+完整SHA读回确认。普通无效/缺失收据在远端 complete 之前短事务退回 ready、递增revision并保存该命令的错误响应，允许新授权修正；完整对象尺寸/SHA不符隔离，绝不入队。未知网络保持 completing，以原始请求及幂等键恢复。最后短事务再次CAS，才入队现有 verify-upload Worker；不自行创建 document_version。丢完成HTTP响应重放返回同一个operation，不会重复文档身份。

所有远端 init/ListParts/upload/complete/abort/HEAD/GET 在数据库事务外。存储成功但数据库确认失败可能留下精确对象/会话候选，只能在核对 uploads/blobs 引用后处理；不能把私有候选登记当成删除授权。当前只对原生 OSS 启用 multipart；S3 保留现有 single 行为，未声称 S3 multipart 验收通过。
