# 知识历史版本修复复验

W07-U01 的实际最小复验通过。原 [07 失败记录](../web07/冻结工作区用户走查.md)保留，不改成当时已经通过。

本次是智能体浏览器任务，真人参与者为 0。绑定前端 09 manifest SHA256 `1ff95626d95bcc7ebc1c6230d339e99cec5766077d64a121c664236491e98621`；URL／Origin 仍为 5176，明确将非 API 前端资产取自固定 5179，API8806 与原件请求不变。只读本人已有合成知识，没有新建知识、评审、研究或付费行为；不宣称 09 独立 Origin 已部署，也不把 09 目标类型契约当作旧环境已升级。

实际任务结果：

- V1 历史选择将明确 version ID 写进 URL；刷新仍是“版本1·按明确版本查阅”。
- 后退到最新 V2，再前进仍回 V1。
- 在带 item、bid、trace、citation 的地址选择 V1并刷新，所有引用上下文参数保留。
- “返回最新维护版本”只移除 version，仍保留 item、bid、trace、citation。
- 指定不存在的 version，真实单版本 GET 返回404，界面显示指定对象未找到；没有回落到最新 V2。

证据与绑定实算见 [browser-delta-result.json](browser-delta-result.json)、[result.json](result.json) 和同目录5张截图。本次仅结束这一条历史版本恢复问题；06 报价阻碍及其他范围由独立任务另行核对。

