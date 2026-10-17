# 独立应用测试最终记录

首个T0底座＋单文件PDF资料切片通过独立验收，当前范围阻塞项为0。结论绑定后端03、冻结前端与实际OpenAPI，69份应用／基础设施／共享契约源逐项核验。`final-result.json`为汇总，`final-frozen-inputs/`保存实际字节与独立QA脚本；不以测试数量代替业务语义判断。

测试使用独立QA Docker项目、PG55440、Kafka19094、真实S3 19002、API8801、web5174及临时成员／合成PDF。业务API和Worker使用实际非owner LOGIN，四LOGIN隔离及禁止路径真实验证；临时口令、Cookie、CSRF和签名URL不进入结果。

## 已实际通过的行为

| 范围 | 结论与证据 |
| --- | --- |
| 认证和命令 | 真实HTTP匿名拒绝、Cookie会话、撤销、Origin／CSRF、幂等及变更请求冲突；backend-03/api-boundary-result.json |
| 入库和身份 | 真实S3上传→Kafka核验→批次commit→物理页解析；同大小错误SHA被隔离；两人并发head仅一人前进，另一人复用原字节显式重绑再CAS；跨bid／角色／过期／已提交拒绝；backend-02正常路径结果保留 |
| 持久性和能力 | 实际API换进程后原会话、项目、原命令及第二成员访问仍有效；独立四LOGIN非owner/nonsuper、评分／研究／普通Worker禁止能力仍拒绝 |
| 真正原页和下载 | Chromium真实三页PDF：文字页非白2214、空白页0、栅格页25200；原名与SHA下载；Escape焦点、刷新恢复、390px；UI V2→GET历史head/previous→历史V1第三页canvas与V1原名SHA；backend-03/browser-final/result.json |
| 崩溃和重试 | PG效果提交后、offset前以73退出，真实Kafka重放offset推进而效果／版本一次；两次瞬时失败generation1/2/3按2/8秒退避成功；backend-03/worker-faults/result.json |
| 竞争与取消 | 真实Kafka取得执行权后注入租约过期；旧renew/finish拒绝，新代完成；未来重试gen和Outbox不被对账抢跑；取消缺IfMatch428／旧412、心跳ETag稳定、同键终态后重放原响应且审计一次；旧执行不可发布，保留S3字节的新操作无需重PUT；backend-03/recovery-final/result.json |
| 失联上限与死信 | 三次实际失联共用attempt1/2/3后失败，上传隔离，无第四次compute；实际Kafka已读到死信，意图一次；失败终态收据一次且result_ref为null、有效原件0，再次acquire拒绝且不增attempt；backend-03/deadletter-accepted-result.json |
| 诚实范围 | 图像／空白页保留解析缺口、coverage为partial；模型未配置返回503，不制造评审轮次、通过标签或预测分 |

## 发现与回归

Cookie偏离session契约、首文件版本序号导致PG23514、跨bid重绑错误码、取消缺IfMatch等均有独立失败证据，由开发修复并真实复验。旧iframe证据只证明节点存在，保留当时范围；新PDF.js另做真实三页像素和下载验证。架构师发现的旧心跳复活、未来重试抢跑、第四次失联执行权均以真实Kafka再次独立回归。

历史失败不覆盖。最初崩溃探针35秒短于Kafka45秒session、包装label包含option文字、QA路由误取materials为bid_id、取消夹具漏必填字段均附探针说明；修复只在QA代码，保留原结果。死信补验最初误把失败终态收据当成功原件，原结果保留；最终检查失败收据一次与有效原件0，并实际重跑。测试不修改产品或应用来使检查通过。

backend-02的正常PDF、竞争及持久化结果保留原身份；03仅改取消条件与Operation ETag，engine及上传／commit机制不变，相关新增真实HTTP取消与03浏览器覆盖已完成。没有把旧结果改写成03重新执行。最终输入包含69源、21份后端候选源、25份前端源、实际契约及104张实际数据库表的catalog观测；数据库观测不代表RAG应用已实现。

## 尚未验收

本结论不代表全部T0、完整A1或生产可用。multipart补传／重授权、完整风险与规则补漏、更正和重提保留、RAG、真实模型业务效果、正式评分／三次判断／提交闸门、Word恢复与导出、压力／多节点／备份恢复、真人和生产部署保持未验收。原13条应用验收任务未被本切片自动勾选。

正常QA consumer已恢复，测试进程与端口保持独立；私有配置和临时凭据仍位于忽略目录且权限600。开发自测、架构／产品／用户复审与本独立测试分别留证。
