# 可审执行范围

这是待用户明确授权的验收方案；没有启动真实评审消费者，没有执行 `execute`，没有新增远端模型请求。工具边界独立验证见 [candidate-03-result.json](candidate-03-result.json)。

唯一项目为 `da2d5493-2732-4f17-b8c4-5b237cf7abfa`。只允许已展示的一页招标原件和一页正文：招标要求工期不超过 90 天，正文承诺 120 天。批准对象是以下清单的完整字节身份、解析和资料头，不只是项目编号。

| 对象 | 固定身份 |
| --- | --- |
| 原件清单 | `/workspace/tender-docs/04-验证与测试/bounded-review-execution-checks/fixture-90-120.json` |
| 清单 SHA256 | `17099eecfabad42a1fecf75ca393502bbec004cbcd987c0ac41cdd1b227153f7` |
| 招标版本 | `c5a1b932-73df-4a4b-b4c7-0114997837cf` |
| 招标原件 SHA256 | `1216aa4bb1b8531d2e55d665a2eee89bc4dbd68dd84f598ee8a259434c2a0b56` |
| 正文版本 | `96e3cdc4-c60a-450c-9a36-61786ce27345` |
| 正文原件 SHA256 | `d1f712ce14e1149c0a94a076b69e3eebf2fec001114eaa1d497ddef177a5a541` |
| 资料快照 / revision | `cf6f5a94-2ce6-4626-8c75-e92530c0e0ee` / `2` |
| 事实范围 | 无事实头，不接收附加事实 |

真实 release 数据库的独立只读复核见 `runtime-03/real-fixture-readonly-corrected/result.json`：完整 heads/documents 一致，`auto_review=false`，模型调用与 prepare/review operation 均为 0。这个观察只代表复核时刻；实际调用前工具还会重新核验。

# 调用与停止边界

只允许一次 `extract-rules` 和一次 `core-judgment`，各最多 8192 输出 token、24576 字节输入；重复、失败、unknown 和缓存路径都消耗对应阶段的窗口名额。结构失败、后置业务校验失败、unknown、取消、源清单变化、原件/解析/资料头/事实变化立即停止；不改提示补发，不用 nonce 追加付费。

180 秒是新调用的启动窗口。在途原生请求保留 45 秒总超时，另有有界的存储和收尾，不宣称整个执行必在 180 秒内结束。PM 的保守费用估计为 USD 0.0350208，规划额度 USD 0.05；这不是供应商已确认账单，也不是工具强制的美元硬上限。普通评审不获得正式评分资格。

使用独立一次性 Kafka group，关闭自动位点存储和提交；不修改普通消费组位点。必须只驱动上述单个 bid 的 `reviews.tick`，不启动全局 review coordinator，也不启动普通或研究消费者。

# 冻结代码与命令

已独立验过的工具候选为 `2867b7b571a444045a7f558cbca34fca2078c60aa80cd4f4de9826075bec3292`。当前可审的运行映射在 `planned-execution-runtime-03/runtime-candidate.json`，SHA256 为 `c844810aeb1924cff9841b97ce4bf71f10f32805123cbd4a9874e8f40191200e`：后端候选02的47个原源、91个已捕获依赖、工具03、私有配置启动器及其配置读取模块，共141个源文件。运行代码全部来自该目录，不从移动的 main 导入业务实现。141个源及已导入模块的身份已独立核对。

后端02存在另行保留的规则克隆重复准备 P2。PM若选择整改后的后端03，必须先完成其受影响增量，再建立新的完整运行清单并替换下方路径、摘要；不能把本清单改名后当成03。旧清单和测试证据保留。

以下默认命令已经实际运行，仅打印预览。`preview` 本身只打印限制参数，不检查源清单；本次另行调用了 `frozen_sources` 才完成源身份核验。

```bash
cd /tmp
PYTHONPATH=/workspace/tender-docs/04-验证与测试/bounded-acceptance-independent-checks/planned-execution-runtime-03/execution-inputs \
  /workspace/tender-workbench/.venv/bin/python \
  /workspace/tender-docs/04-验证与测试/bounded-acceptance-independent-checks/planned-execution-runtime-03/execution-inputs/deploy/run.py \
  --env-file /workspace/tender-workbench/.local/external-release.env -- \
  /workspace/tender-workbench/.venv/bin/python -m deploy.review_acceptance \
  --bid-id da2d5493-2732-4f17-b8c4-5b237cf7abfa \
  --candidate /workspace/tender-docs/04-验证与测试/bounded-acceptance-independent-checks/planned-execution-runtime-03/runtime-candidate.json \
  --candidate-sha256 c844810aeb1924cff9841b97ce4bf71f10f32805123cbd4a9874e8f40191200e \
  --fixture /workspace/tender-docs/04-验证与测试/bounded-review-execution-checks/fixture-90-120.json \
  --fixture-sha256 17099eecfabad42a1fecf75ca393502bbec004cbcd987c0ac41cdd1b227153f7
```

用户明确授权以后，PM先确认最终运行清单与上述两份原件不变，再用真实会话、CSRF及当前 ETag 发起一次该项目评审请求，保存响应的 `Accepted.resource_id`，这才是批准的 `command_id`。当前没有为执行创建该命令，不伪造 UUID。实际启动命令只在以上固定命令末尾增加 `--command-id <这次真实resource_id> --seconds 180 --execute`；这条执行命令尚未运行。

PM仅在授权窗口内驱动该 bid 的准备与冻结，受控工具只处理这个真实 command 下、数据库身份一致的 prepare/review operation。工具每次付费前比较完整原页输入，核心规则必须来自该 command 唯一的真实 prepare effect，不能改用人工规则包。任何失败即停止两者；完成后确认进程已退出并记录调用账本、usage、结果状态和源码摘要。若已有任何 model_call，执行器拒绝重新启动；unknown 必须独立对账。

# 当前限制

完整普通 handler 的独立验证使用真实受限 PostgreSQL、ASGI、实际解析端口和显式 MockTransport，模型产物对象为内存测试存储。真实90/120项目此次只做了数据库只读核对；尚未完成该项目真实 Kafka 加付费模型的组合验收。没有把模拟返回、预览输出或供应商连通测试记成真实业务效果通过。

自动审批此前拒绝启动收费消费者，理由是缺少可信用户对具体项目、调用次数与费用范围的明确授权。该拒绝仍生效；本方案用于让PM提交具体授权请求，不执行或间接绕过。
