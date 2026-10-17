# 首个PDF资料切片验收

项目经理核对最终源码、实际测试及专业评审后，放行T0底座的首个PDF资料切片，允许按此范围本地演示与继续开发。当前切片阻塞为0。整体项目、全部T0及完整A1仍按[实施任务板](../00-项目管理/实施任务板.md)推进，不把资料解析称作风险评审完成。

## 交付范围

- 同源登录、会话撤销、CSRF、项目创建及分页，真实PG持久化与命令幂等。
- PDF单文件私有S3上传、实际SHA256及长度核验、明确部分入库、版本head CAS及已核验原件的显式重绑定。
- PG Outbox、真实Kafka、独立受限Worker、租约与fence、持久退避、三次失联上限、取消与强ETag、幂等重放及死信。
- PDF物理页文字和明确解析缺口、自托管PDF.js原页、空白／图像页导航、当前与历史版本原件及真实下载、390px操作。

原件解析仅覆盖物理页文字；OCR、结构化表图和规则／风险评审未接通，页面显示partial或未就绪，不生成假风险、假分数或正式通过结论。Word在本切片上传前被明确拒绝，用户可先从外部Word导出PDF。

## 验收依据

| 责任方 | 已实际完成 | 结论与证据 |
| --- | --- | --- |
| 后端开发 | 质量门禁、正常PG／S3／Kafka纵向、独立PG恢复及取消处理器自测 | [最终后端候选](/workspace/tender-workbench/apps/api/backend-candidate-03.json)，自测独立于QA |
| 前端开发 | 格式、lint、类型、9项组件／单元场景及构建；真实原页／下载／历史自查 | [前端候选](/workspace/tender-workbench/apps/web/qa-previews/frontend-candidate.json) |
| 独立测试 | 真实HTTP、实际LOGIN隔离、PDF链路与错误SHA、并发重绑、持久化、Chromium、真实Kafka崩溃／退避／取消／失联及死信 | [最终测试记录](implementation-test-checks/final-result.json)，[输入与结果核验](implementation-test-checks/final-verification.json) |
| 架构师 | 实际源码审查；隔离PG／ASGI复现并复验恢复预算及取消条件；基础设施边界 | [实现架构复审](implementation-architect-review/复审.md)，阻塞0 |
| 产品Owner | 实际上传、范围、原页与版本；直接取件、手机入口、历史追溯三项修正 | [产品实际评审](implementation-product-review/PDF资料切片实际产品评审.md)及[最终输入增量](implementation-product-review/候选03增量产品复核.md)，阻塞0 |
| 用户视角 | 真实浏览器执行合成任务；局部失败、原页、390px、键盘返回、412接续、下载与历史 | [实际走查](implementation-user-review/实际PDF切片走查.md)及[最终输入增量](implementation-user-review/后端03输入增量复核.md)，所查阻塞0，真人参与0 |
| 项目经理 | 69份当前来源摘要、9份测试结果及各角色最终输入身份核对；范围、依赖及整改整合 | [本批放行记录](project-gates/software-slice-gate.json)，[源码冻结](project-gates/implementation-candidate.json) |

原失败、候选和当时的运行输入保留。无变化的正常路径保留对应原证据并说明影响范围；新增取消条件及原页渲染有受影响复验，不把旧运行源码摘要改为当前值。

## 后续验收边界

分片上传、SSE及完整操作体验、A1规则／风险／人工补漏与复检提交、RAG、三次完整评分判断、Word转换和报告导出按后续工单实施。原13项A1应用验收保持待执行。真实代表性资料、模型效果、正式评分精度、真人试用及生产容量／恢复分别验收。

运行步骤和本地私有配置见[源码工程说明](/workspace/tender-workbench/README.md)。本次没有远端发布、合并或生产部署。团队职责与质量要求以[团队协作与质量准则](../00-项目管理/团队协作与质量准则.md)为主记录。
