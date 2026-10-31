# 有限 SYNTHETIC 业务补验：阶段记录

本记录只覆盖已完成并可核验的有限合成机制、浏览器及隔离恢复证据。**不是全部业务验收完成，不是模型语义准确率、任意复杂标书质量或真人正式签收。** 原材料全部为虚构 SYNTHETIC，不能作为官方评分标签。

当前已推送 app `5516ec2fd4f8b6cfebd98de5a384ca875a797a88`；新 CI 由根跟踪，**仍待终态，未写全绿**。旧 7f92 的 [CI 41194633417](https://github.com/zys743551828-art/tender-app/actions/runs/41194633417) 已终态 **10 success / 1 failure**，见[官方结果快照](ci/7f92-terminal.json)，pricing native 生命周期连续 reopen 的 POST 等待超时保留。修复后完整前端 2011 与原生组合单项通过是后续独立证据，不改写旧 CI。此前 c791/f061 两版完整 CI 11/11 已确认。

三个独立 PDF 场景的普通上传、来源消歧、核心评审、报告下载脚本现已通过，见下方实际字节回执；不是复杂 Word 已采用或整包资格。它们使用 **新实际生产构建静态资源接既有 C791 真实 API 的 frontend preview**：仅替换静态构建响应，API/模型/数据库/存储请求均无 mock，保留原 HTTP 安全头。不能冒称全栈按新 SHA 重装。本轮普通业务 API 实际运行在 C791 环境；后续业务存储的两个 OSS 模块在真实 OSS 路径独立验证。5516 新增 UI 等待与 Desktop stop 修复，另由完整前端 2011、停机 14、原生组合 1 及新 CI 验证；不冒称 5516 全栈按同 SHA 重装。

S7 `tender-fusion-full-327c846b8e5e` 已完成**异常中断后的受限恢复清理**：[恢复回执](lifecycle/interrupted-supervisor-cleanup-recovery.json)实际精确删除 20 容器、3 网络、16 卷，remaining 各为空、cleanup_closed=true。原 4 个保存 Word PID 创建身份均不再存活，原 generation 没有请求/转换回执或转换资源。**graceful_shutdown_proven=false**，缺少原 host summary，不能把它改称正常优雅停机验收。

[第一次失败清理](lifecycle/interrupted-supervisor-cleanup.json)仍保留 normal stop=0、WORD_EXECUTORS_NOT_STOPPED、删除 0、cleanup=false；[原 supervisor journal 身份](lifecycle/original-interrupted-reference.json)保持 interrupted/unfinished。恢复回执明确 old_failure_receipt_preserved 和 original_result_preserved 均 true。另一个 agent 后续独立 2 容器/1 网络测试不属于 S7，本目录不混算其生命周期。无生产操作。

## 覆盖分母与待验

工作矩阵是 **9 项能力，绝不是 9 个测试**。S03/S04/S05 的三场景有限普通 PDF 链已完成，三场景 typed 机制均完成，最终所选截图已有明确范围的根像素复核；S7 精确资源清理已闭环；刷新与停机修复已有定向/完整前端/原生回归，最新 CI 和真实正常优雅停机的结论仍需分开；不能汇总为全部要求通过。原 CI、组合交互及失活会话测试按其原有范围复用，不能替代模型与完整材料链。

| 能力 | 已有事实与边界 |
|---|---|
| S01 合成材料 | datasets `51c28bb`，8 份素材、3 场景；[manifest](corpus/manifest.json)、[expected](corpus/expected.json)。7 份受控正常输入，另 1 份 PAGE 动态域负例；schema 模板不是 HTTP 请求，不能复用占位 UUID 或把 DOCX 摘要当实际 PDF 身份。 |
| S02 Word 上传/转换/采用 | 六份真实 Word 转换 partial，明确保留文字对应/复杂布局 gap，未授采用资格。另行上传 PDF 的业务链是独立路径，不能回称 Word 已采用。当前官方 Gotenberg 已可用，旧冻结镜像缺失不是当前阻塞原因。 |
| S03 有限真实模型链 | 历史 5 笔业务请求 blocked 保留；S7 三 bid 各 extract+core 一次，共 6 provider 请求 succeeded/usage_reported，unknown 0。三个脚本通过并下载实际报告；三 core 最终 fresh GET 均 partial，旧 missing pending 时序记录保留；人工合成步骤见回执，不授模型语义准确率。 |
| S04 正常/矛盾/缺附件 | 三个普通 PDF 场景流程脚本通过；只证明有限输入与安全机制，不能用 legacy 单正文评审代表 other 附件完整覆盖或缺件语义准确率。 |
| S05 报告/导出/包 | 三份实际 DOCX 报告下载与 bytes/SHA 相符；三包机制 conflict 11、normal 13、missing 13 项均 unknown、not_frozen，没有整包下载资格。 |
| S06 身份/跨项目 | [八项真实检查](identity/normal-roles-state.json)；匿名/退出 401、同键重放、旧 ETag 412、跨项目绑定 409。最新[原生组合](native-lifecycle/workspace-journeys.json)另实际验证 active/inactive member、历史 ACK 与新 API 过程通知；未发明 admin RBAC。 |
| S07 视觉/交互 | identity 两宽[像素复核](identity/pixel-review.json)通过所见范围；Word [093753 稳定错误卡两宽复核](word-policy/final093753/pixel-review.json)通过所见错误状态，早期 loading 图仅保留为历史，不能作为该通过依据。 |
| S08 隔离恢复 | 最新单轮 [32/32](recovery/summary.json)，另 [22/22 单元检查](recovery/prediction-profile-unit.json)独立计数；有限当前 API smoke 通过，仍非生产完整恢复/RPO/RTO。 |
| S09 回归/生命周期 | 7f92 CI终态10成功1失败；5516新CI待终态。共享刷新定向27、完整前端2011、停机14、真实原生组合1项通过按各自分母保留，不能相加。S7恢复清理closed不证明graceful。 |

[根工作矩阵快照](matrix-snapshot-110758.json)保留其 `updated_utc=2026-10-31T11:07:58.384228Z` 的九能力状态，不改根文件。其中 S09 当时“清理未闭环”早于 11:13:45–11:14:18 恢复清理，应由上方后到回执补充；它仍正确保留最新 CI/停机缺陷待修，不是全部验收完成。

## 原始材料角色与整包缺口

01 招标、02 补遗是 authority；03/07 为本方技术正文；04 商务、05 **本方报价**、06 清单为本方附件。05 上传角色应为 `other`，不能误归采购方 `boq`。NORMAL 为 01–06；CONTRADICTION 用 07 替代 03；MISSING 为 01、02、03、06，不能补造 04/05。

普通 legacy `review_data.read_bundle` 只读 body/tender/supplement/drawing/boq，排除 other；prepare 把非 body 来源作要求资料。typed API 当前本方包仅接受 body/bid_body，other 返回 `PACKAGE_DOCUMENT_ROLE_REJECTED`。 原件仍以 source_original 保留。精确接线约束在 `packages/domain/bid_package_service.py::directory/member`、`packages/domain/document_parses.py` 与 `packages/contracts/sql/document-parses.sql`；后两者 API/SQL 均拒绝 other 进入该解析通路，`packages/domain/review_data.py::read_bundle` 也筛除 other。即使三个普通业务脚本通过，本轮也不能声称商务/报价附件与缺件已获整包覆盖；安全 guard 不能为跑绿直接移除。

[正式 PRD](../../../../02-业务与产品/AI投标工作台PRD.md)第 0 节要求本方资格/商务/技术/报价/附件并禁止退化为最后一份 body，同时明示目标非完成声明；末尾当前组合缺口仍列单正文提升整包。[融合实施计划](../../../../00-项目管理/标书工作台融合实施计划.md) F04-04 资格商务附件仍为未开始。缺的是非 body 本方响应的显式入包/解析接线与完整真实验收，不能概括为所有附件核查都未实现，也不能只加角色白名单或伪造 parse 跑绿。现有 `bid_packages.py` 的 purposes、DocumentPart.category/shared 附件与 `package_scope_review.py` 的 quantity/expiry/subject/required_fields/text/consistency 受限检查可以复用；它们不自动解除当前接线边界。

## 三场景普通业务与前端预览

[实际报告 manifest](business/report-manifest.json)逐份绑定真实下载 DOCX 的 document ID、字节数与 SHA；三份均已在归档时独立核对。**素材是独立 PDF 输入，不能回称六份复杂 Word 被 adopted。** 三场景报告/原页/规则/评审双宽截图已保存；[最终只读观察](business/final-observation/download-correspondence.json)再次实际点击 UI 下载并核对相同报告 bytes/SHA，无新 review/export/model 命令。根已实际查看[根保存的最终像素回执](business/final-observation/root-final-pixel-review.json)：normal 报告 1440/390、conflict 第二原页 1440/390（中文表格、图片、页码）、conflict 最终报告 390（留白与历史提示）。这些所见像素清楚、无明显溢出；不能推广为所有截图/任意标书视觉通过，missing 报告不冒称单独目视。

| 场景/回执 | 真实继续方式与覆盖边界 | 报告 |
|---|---|---|
| [conflict-pdf-103228](business/conflict/state.json) | fresh；正常提取、明确 SYNTHETIC 人工补漏与来源 partial 消歧，保留其它规则；core partial。不是模型自行补齐/真人签收。 | [DOCX](business/conflict/review-report.docx) |
| [normal-pdf-103929](business/normal/state.json) | 从已经保存的来源决定安全恢复，未重发来源决定/提取；用 100028 完整旧 RuleSetView baseline SHA 与 semantic+evidence 多重集核对，旧条款确为 superseded。103113 的 HTTP 200 已成功，仅脚本错误地把历史数字描述作未保留而误报；修正测试断言，未修改业务规则或响应。core partial。 | [DOCX](business/normal/review-report.docx) |
| [missing-pdf-103933](business/missing/state.json) | 先前人工补漏已收到真实 201，恢复前核实 verified，不重加/重提取；缺少商务/报价原件保持缺少，未补假附件。旧脚本 state 的 pending 保留为早期 GET 时序；[最终 fresh GET](business/final-observation/missing-state.json)明确 partial/current=true，并实际 UI 下载 SHA 相同，旧文件不回填。 | [DOCX](business/missing/review-report.docx) |

[前端新构建](frontend/preview36/summary.json)为 **36 组件项**、format/lint/build 通过、399 文件；[JUnit](frontend/preview36/components.xml)。这是实际构建产物预览，不是静态原型或 API mock；也不是新 SHA 的全栈安装证明。早期 web-release 29 项是历史不同 scope，不与 36 相加。

### 三场景 typed 的实际有限机制

[conflict-pdf-104118](typed/conflict/state.json)、[normal-pdf-105419](typed/normal/state.json)、[missing-pdf-105425](typed/missing/state.json)均返回 `passed_limited_mechanism_not_qualified`：other 入包真实 422、两类 partial parse 冻结真实 422、authority/body 包存储与 manifest 下载字节/SHA核对、空 evidence、inventory 和显式 unknown coverage 通过。分母分别是 **conflict 11、normal 13、missing 13**，全部 unknown；既不是三个 11，也不是 37 项合规通过。包仍 not_frozen/review_ready=false。

真实拒绝对象是各材料列表第一个 other：conflict 的 06 清单、normal 的 04 商务、missing 的 06 清单。normal/conflict 中 05 own 报价保持 other 且未进入包；missing 确实无报价，未补假，不能声称三个用例都直接 POST 报价 422。

package 脚本未接 driver 的 static preview 参数，仍为旧 C791 package UI，不冒称新前端预览。身份/包 scope 绑定会使旧 legacy review 历史 current=false，属正常失效；例如[conflict 最终观察](business/final-observation/conflict-state.json)明确 partial/current=false 且历史提示可见，不能为追求 current 强改历史或重发模型。normal/missing 最终观察当时 current=true 在后续包绑定前，时点不同不矛盾。other 附件目标缺口不因拒绝机制通过而完成。

## 定向修复证据

<a id="TEST-SOURCE-ACK"></a>
### TEST-SOURCE-ACK

对应 `packages/domain/source_effectivity.py`、`apps/api/source_effectivity_routes.py`、`source_effectivity_contracts.py` 及 `test_source_effectivity_ambiguities.py`、`test_source_effectivity_integration.py`。

[先红摘要](source-effectivity/ack-before.json)为 18 项失败；[31 项通过](source-effectivity/ack31/summary.json)含 18 纯逻辑及 13 真实非 owner PG/S3/HTTP 项。[局部语义负例先红](source-effectivity/marker-before.json)是 4 项中的 2 项失败；后续 [8 项通过](source-effectivity/marker8/summary.json)含新增 marker 和已有原件/CAS/重放子集。**31 与 8 有重叠，不能相加为 39 个独立测试。** 两轮 JUnit 与资源收尾均随附。

只有明确全集 ACK、当前 ETag、原页范围/来源字节及无待重验同时成立，才允许 conflict 降为 partial，从不升 complete；旧 payload/defer 保留冲突。局部替换保留其它条款，『其余招标要求保持/不变』禁止 whole_replace。合成测试决定不是客户真人签收。

<a id="TEST-EXTRACTION-GROUNDING"></a>
### TEST-EXTRACTION-GROUNDING

对应 `apps/worker/review_handlers.py`、`tests/test_rule_extraction_grounding_correction.py`。修复保留原逐字、条件、分值、来源与账本守卫；有界纠正并不保证语义准确。[历史 42 项状态](model/targeted-test-evidence-status.json)确认为旧工具原输出，未持久保存 JUnit，不能补造。新同一次反馈列全违规字段的修复已有 **51 项通过**的[持久 JUnit](model/all-field-feedback51/pytest-final.xml)、[pytest 回执](model/all-field-feedback51/pytest-final.stdout.log)和[静态回执](model/all-field-feedback51/static-final.stdout.log)。只修完整错误反馈，不增加纠正次数、不放宽 validator/结构、源冻结、租约/取消、账本与总 quota；不能把 42 与 51 相加为独立测试，也不能据此宣称新真实业务已通过。

<a id="TEST-SOURCE-ACK-UI"></a>
### TEST-SOURCE-ACK-UI

对应 `apps/web/src/SourceEffectivityPanel.tsx` 与 `SourceEffectivityPanel.test.tsx`。[web-release 回执](frontend/summary.json)为定向 **29** 项通过，另 format/lint/build 完成，产物 399 文件；[JUnit](frontend/components.xml)为原成功测试回执。29 是该定向组合分母，不能全部改称 ACK UI 独有测试。最终实际来源消歧业务/像素仍随新 cohort 补验。

<a id="TEST-MIGRATION-API"></a>
### TEST-MIGRATION-API

对应 `deploy/extended_recovery_runtime.py`、`tests/test_extended_recovery_migration_runtime.py`。最新 171856 单轮为 schema 4、authoring 1、material 1、migration/API 26，合计 **32/32，0 skip**。另 22 项仅是既有 /4 profile 与 /3 边界单元检查，**不是 /4 跨集群原生恢复**。

旧 CI umask 022 暴露中间 `.local` 未显式 0700，真实[先红摘要](recovery/umask022/red-summary.json)复现同一 PRIVATE_DIRECTORY_PERMISSIONS；只改测试私有目录构造后，[单 API JUnit](recovery/umask022/migration-junit.xml) **1/1 通过**，无 skip，未重复原 31 项。具体[修复说明](recovery/umask022/README.md)、[最终复核](recovery/umask022/final-review.json)保留 runtime/guard 不变和三轮精确 9 容器全部不存在的结果。

[API smoke](recovery/api-smoke-proof.json)仅验证独立隔离目标激活后的新登录、旧 cookie 拒绝、项目/非空 draft 读取、CSRF、单次设置更新与精确重放、原件下载字节。合成原件元数据 fixture 不伪称上传验证/解析/Word 资格；不启动 Worker/模型，不证明旧队列续跑。生产完整应用恢复与 RPO/RTO 资格仍 false。[清理证明](recovery/all-rounds-cleanup.json)覆盖历史 20 轮精确 58 容器 ID 均已不存在；原有用户镜像/卷保留。

## 实际计费、历史失败与修复边界

[账本前两笔](model/ledger-082509.json)、[第三笔](model/ledger-092032.json)、[S6 初始与一次纠正](model/ledger-093908.json)及[固定失败摘要](model/business-blocked-receipts.json)保留 provider 状态、用量和固定错误码。[S6 安全诊断](model/grounding-correction-real-diagnostic.json)仅含逐字段 grounded 布尔与变化索引，不含原响应/规则文本：原反馈只报首个错误，初始多个字段错，纠正后两个 statement 仍未改变，故原 validator 正确继续阻断。

[S7 实际账本](model/ledger-104133.json)只有 6 笔（每 bid extract/core 各 1），17,142 input + 8,178 output，峰值估计 0.099708 CNY，unknown 0。[当前累计](model/cumulative-104133.json)含此前 6 笔及 probe，共 **12 次请求、45,606 tokens，峰值估计 0.193338 CNY**。按既有[DeepSeek 官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)估算，未查 invoice；历史 0.05634/0.09363 CNY 仅保留原时点，不代表新累计。用户取消本轮金额上限不取消官方价格/有效预算准入、有限 case 与账本。OSS 累计 71 请求，费用微量未核实，见下方独立证明。

| 历史结果 | 已确认原因/后续证据 |
|---|---|
| 六份 Word partial | 文字对应、复杂布局未验证；安全阻断采用。保留失败，不取消资格门。 |
| 首笔 `RULE_STATEMENT_UNGROUNDED` | 语句不逐字；初始 harness 又把 own 报价错误映射 boq。输入角色已纠正，不能靠归一化改原文通过。 |
| 第二笔 `SOURCE_EFFECTIVITY_AMBIGUOUS` | 正常消歧后缺少显式全集 ACK 的降级通路；上述真实 HTTP 与 UI 定向修复，历史失败修复后另有上述 S7 三场景普通链通过，不能改写旧失败。 |
| 第三笔 `RULE_CONDITION_UNGROUNDED` | provider 结构合法不等于来源逐字有效；原 blocked/费用保留，有界纠正回归不等于新业务已成功。 |
| S6 两笔计费仍 blocked | 初始+一次校正均 provider succeeded/usage_reported，但剩余 statement 不 grounded；原失败和费用保留。51 项定向修复之外，S7 的实际六笔请求与报告另有独立回执；旧 S6 失败不改写。 |
| Word 策略卡 loading 图 | 092212 loading 图只证明机制回执，不授视觉通过；093753 新中文稳定错误卡已由根实际查看 1440/390 PNG，文字清晰、布局无溢出，见新 pixel-review。仅负例状态视觉通过，非转换或业务质量。 |
| CI 41188927492 recovery 失败 | 本机 32 通过不覆盖 umask 022。测试私有目录缺口已由 022 先红后单 API green 1/1 证明修复；runtime 未改，bootstrap 未放宽，C791 后一 CI 已 11/11 success；最新 7f92 CI 仍进行中且 pricing 生命周期超时待诊断。旧 run cancelled 与其 job 曾失败分别保留。 |
| 恢复早轮失败 | Windows bind 源 I/O 触发原租约；后来用字节一致只读 tmpfs 源。测试 cleanup 参数及新 reader CONNECT 的隔离 fixture 缺口分别修复；最终同源 32 原断言通过，未放宽租约/权限。 |

## 最终刷新与停机机制回归

<a id="TEST-UI-REFRESH"></a>
### TEST-UI-REFRESH

对应 `apps/web/src/useResource.ts`、`ProjectLifecyclePanel.tsx` 及其直接测试。实际 fetch 仍同步启动；共享完成 promise，调用者在同一 flight 的 GET 刷新真正结束后才宣布保存成功，避免连续 reopen 复用旧 ETag。没有延迟 sleep 或放宽真实 HTTP 断言。

[定向 shared 27 项 JUnit](ui-refresh/final-shared-junit.xml)为 4 文件/27 通过；[完整前端 JUnit](ui-refresh/frontend-complete-junit.xml)为 **199 文件/2011 通过、0 failure、0 error**。两者重叠，不相加。历史 microtask 启动原型的[完整 2010 通过/1 失败](ui-refresh/earlier-full-microtask-failure.json)保留（`finish is not a function`）；恢复同步 fetch 启动后最终完整 2011 全过。更早线程参数启动错误的 [0 tests 回执](ui-refresh/frontend-all-junit.xml)与[分母说明](ui-refresh/results-summary.json)保留，**不是业务失败或0测试通过验收**。

### 原生生命周期组合

[原测试单项 JUnit](native-lifecycle/junit.xml)是 `test_native_workspace_combined_journeys_and_notification_recovery`，**1 passed / 0 skip，suite 50.517 秒**；[实际交互](native-lifecycle/workspace-journeys.json)覆盖写入/取消、归档/结束/重开、连续状态、幂等历史 ACK、active/inactive member 与新 API 进程通知，model_calls=0，source_unchanged=true。使用既有本机 Python3.12.14/Node22.23.2 与固定 PG/S3 镜像；[精确资源](native-lifecycle/resources.json)仅本轮 2 容器/1 网络，closed，私有临时输入删除。它独立于 S7 20C/3N/16V，不能混算。

两次基础设施失败保留最小诊断：首 [internal network 未发布主机端口](native-lifecycle/early/startup-failure.json)未执行 pytest；后 bridge [失败摘要](native-lifecycle/early/bridge-summary.json)由 test helper 清洗环境漏掉 Windows 系统项，Node CSPRNG exit134，浏览器 JS 未执行。[最小系统环境对照](native-lifecycle/early/windows-node-environment-proof.json)确认 SystemRoot 的影响；正式测试白名单仅增 SYSTEMROOT/WINDIR 后原断言通过。没有原始失败日志/秘密导出，不把 runner 基础设施失败算产品业务失败。

<a id="TEST-DESKTOP-STOP"></a>
### TEST-DESKTOP-STOP

对应 `deploy/fusion_desktop.py`、`tests/test_fusion_desktop_word.py`、`test_fusion_desktop_compose.py`。原 CLI 忽略 Word unavailable/cleanup 状态而仍 exit0，且 Compose stop 未带已声明 model profile，导致 worker-review/assistant 漏停；[诊断](desktop-stop/diagnosis.json)保留两个缺陷与原 host summary 缺失事实。

[最终14项JUnit](desktop-stop/final-junit.xml)、[红/绿与静态摘要](desktop-stop/summary.json)及[静态检查](desktop-stop/checks.json)通过。mypy 目标为 Linux，原 Windows POSIX 依赖诊断不冒称通过；这些是离线控制/错误传播回归，没有由该测试 owner 启动或停止真实服务。非 status CLI 在未证实关闭时须非零，正常停服务包含配置中的 model profile。**14项通过不回填 S7 graceful_shutdown_proven**；S7仍只取得异常恢复精确清理资格。最新5516 CI仍待最终结果。

## OSS 原生边界与修复

<a id="TEST-OSS-PRIVATE-PUT"></a>
### TEST-OSS-PRIVATE-PUT

对应 `packages/domain/providers/oss.py`、`tests/test_providers.py`。[离线定向 10 项](oss/private-put-unit10/summary.json)保留先红缺 `x-oss-object-acl` 与修后 10 通过；不是 10 次真实 OSS。随后 [live-app-oss-095414](oss/application15/result.json) **15 请求**通过正常工厂/from_env、服务端 PUT private、匿名拒绝、签名 GET 字节、相同内容幂等及冲突不覆盖，精确对象删除。无模型调用、不改 bucket/global ACL。

<a id="TEST-OSS-MULTIPART"></a>
### TEST-OSS-MULTIPART

对应 `packages/uploads/native.py`、`tests/test_multipart.py`。[首轮真实 native 24 请求](oss/multipart-before24/result.json)虽完成字节相符和匿名 403，但 object ACL=private 断言 false，明确失败且 cleanup true；不能把仅 init 阶段设置 ACL 当最终修复。

按[官方中文 CompleteMultipartUpload](https://help.aliyun.com/zh/oss/developer-reference/completemultipartupload)在 Complete 请求显式保留 private，最终[定向 7 项 JUnit](oss/multipart-complete-unit7/green-junit.xml)与[来源/静态摘要](oss/multipart-complete-unit7/summary.json)通过；[live-native-multipart-101926](oss/multipart-after24/result.json) **24 请求全部通过**，实际 completed ACL private、匿名 403、字节/大小/SHA、abort 与完成两 upload 均关闭、精确 keys 均删除。未放宽资格，没有 cross-region 推断。

首 probe 8 + factory 15 + 失败 multipart 24 + 成功 multipart 24 = **71 请求**；其中失败轮同样计用量。native multipart 不是浏览器/DB 普通应用 multipart 全链资格。standalone presigned PUT 当前不在普通 OSS API 支持模式，不泛化未来 direct 上传支持。没有记录签名 URL、密钥、upload ID 原值或模型响应。

### 仅本轮临时工作树整理

[自建 s5 工作树处置回执](lifecycle/s5-worktree-disposed.json)记录 `.tools/s5` 已删除 10,576 文件、221,614,679 logical bytes；活动 C dev 版本、D 原共享配置保留，已修改代码均可由提交恢复，结果保存在工作树外。删除前根核对 23 个改动源码与已推提交一致，ignored 仅本轮自建 run/dist/pyc，Docker 无其关联残留。这里只是已审查临时工作树清理，**不是五仓最终整理完成**；旧 S7 interrupted result/失败回执仍未改写。

## 归档边界

[来源与变换](archive-provenance.json)记录精选源文件及摘要；JSON 仅替换本机绝对路径，成功 JUnit 去除非必要 stdout/stderr/properties，未改测试结论。[inventory](inventory.json)记录本目录实际文件。没有复制私有 profile/env/log、operator 输入、官方 HTML base64、原始模型响应、整个临时源码树或旧克隆。

S7异常恢复清理已完成；共享GET flight、成功提示时序与两个停机缺陷的最终持久回归已补齐。早期17项是中间重叠scope，不与最终27/2011相加。尚待5516新CI终态；S7从未取得正常优雅停机证明。最终观察没有新增模型请求；计费累计仍为 12 请求/45,606 tokens/估计 0.193338 CNY，OSS 71 请求。以上修复和机制证据不能自动授予业务资格。
