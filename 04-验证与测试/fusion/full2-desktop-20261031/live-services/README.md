# 本机DeepSeek与OSS最小真实服务验证（2026-10-31）

应用 `6446ba0` 的现有 `DeepSeekClient` / `OssClient` 与原生OSS安全会话，在授权电脑 zhangyansong 上执行。程序从 `D:\workspace\tender\.env` 读取已有凭据，不写入文件、进程参数或报告；只记录字段是否非空。此前只查各仓库而漏查共同父目录，导致配置缺失结论不完整；[首次预检](../live-budget-preflight.json)保留历史事实，不再作为当前阻塞。

[实际结果](result.json)：1次DeepSeek请求，采用当前端口默认低价 `deepseek-flash`，不改用户配置；输入123、输出20、共143 tokens。合成“TEST-CERT”条款返回JSON经现有端口Schema验证成功，未发送任何真实标书或个人材料。结果写入本轮唯一私有Standard对象，经当前OSS端口HEAD/GET读回字节一致；对象ACL为private，匿名GET被403拒绝。8次OSS请求、无自动重试；未列举或读取任何已有对象，未修改bucket策略。唯一新对象已删除，精确HEAD返回不存在，版本控制未启用，无残留测试版本。

[费用依据](cost-evidence.json)：按[DeepSeek官方人民币高峰价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)，模型约0.000406元；依据[OSS官方杭州计费示例](https://help.aliyun.com/zh/oss/product-overview/billing-examples)，8次请求、不到1KiB对象与微量流量，合计保守估算低于0.0015元。实际账单未查询，不能称已确认扣费金额。执行前预留1.06656元（其中OSS1元安全余量），低于合计20元上限；未充值、升级或新增凭据。

[实际执行源码](executed-probe.py.txt)是一次性证据，包含防重复执行标记、模型1次上限、OSS14次上限、无重试与finally精确回收；不作为生产安装入口。请求ID仅记录末8位，不含签名URL、账号标识或原始响应。

边界：这是实际提供商级“模型结构化输出→私有对象存储→读回”的最小机制链，不是普通业务UI整链、预测准确性或正式资格验收；`formal_scoring_eligible=false`。真实服务错误未刻意触发，现有离线错误契约/CI证据保持独立。生产恢复、真实复杂标书及真人签收仍未执行。
