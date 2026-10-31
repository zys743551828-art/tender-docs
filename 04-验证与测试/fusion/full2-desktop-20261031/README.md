# Desktop full/2 工程交付核查

本轮在授权的 Windows 电脑与 Docker Linux/amd64 引擎执行。三条正常业务路径与本轮完整生命周期矩阵均已通过。应用提交为 `6446ba0ad98beb46a0c3942295215aa59dbc5df5`；[CI41174917023](https://github.com/zys743551828-art/tender-app/actions/runs/41174917023) 11/11 项全部成功。本页不代替正式业务、模型质量或生产验收。

## 当前覆盖

| 路径 | 已取得的真实结果 | 边界 |
|---|---|---|
| 普通常规安装 | 新项目 venv、标准依赖镜像、prepare/up、指定合成口令 HTTP200、status、verify 成功 | 独立测试实例；未连接原业务库 |
| 外部 Word | 上传、受控转换、采用、Kafka 解析刷新、真实原页、原件/PDF 下载通过 | 解析仍显示 partial；Word 交付资格未因此授予 |
| 人工 PAGE | 普通 API 的 DOCX/PDF 均 succeeded | 保留 TOC_PAGE_NUMBERS_UNKNOWN、ACTUAL_SECTION_PAGE_RANGES_UNKNOWN |
| 人工 RICH | 普通 API 的 DOCX/PDF 均 succeeded；navigation_only 两页、两轮固定点、无导航 gap | 不代表任意复杂标书的全部版式通过 |
| 桌面与 390px | 实际生产页面截图、原页 canvas、刷新恢复及下载字节通过 | 主审已查看当前原页与窄屏刷新像素；窄屏不替代桌面最终内容核对 |
| 焦点与慢速预览取消 | 既有本机证据复用，见[焦点/取消](../word-prebuilt-20261030/focus-cancel/README.md) | headers 后取消已观察；未证明部分正文已传输或 Word 引擎立即取消 |
| 生命周期 | 普通 stop/up、单执行器故障恢复、拒绝自动重绑定及显式重绑定全部通过 | 普通重启独立通过；未测试非正常断电或强杀恢复 |

使用固定官方 Gotenberg 8.39.0-libreoffice，不自建 Word 转换镜像。应用依赖镜像使用 Python3.12.16、pypdfium2 5.14.0；宿主新 venv 使用已有 Python3.12.14。安装和构建命令集中见[使用说明](../../../使用说明.md)。外部模型标识属于用户可选配置，当前仅 DeepSeek 实现；本轮没有付费模型调用。

## 修复与回归

- [基础有限回归](regression/README.md)：18 模块 527 通过。随后改变的模块分别做定向复验，不能把历史源码记录改写为最终源码全量验收，也不能把重叠用例数相加。
- [Desktop 只读源码映射](desktop-bind-regression/README.md)、[Windows 文件时间戳](binding-ctime-regression/README.md)：真实安装反例与保留的失败记录。
- [渲染器结果身份与退出竞态](renderer-chain-stop-regression/)、[PAGE 路由与 mailbox](page-mailbox-dispatch-regression/README.md)：保留数据库方法身份约束、源验证和版式断言，修复生产接线。
- [安全存储诊断](safe-store-diagnostics/README.md)：仅增加已知错误码、HTTP 状态和操作名，原验证及权限不变；此前一次 ClientError 的细分原因尚未证明。
- [结果缺失提示](missing-result-ui/README.md)：中性文案、定向测试、固定 Node24.22.2 构建及真实浏览器回放。回放不是业务 E2E，真实主链另记。

TEST-DESKTOP-WORD-BOOTSTRAP 对应角色隔离、明确模型声明与私密 stdin 输入；TEST-DESKTOP-WORD-LIFECYCLE 对应两个绑定执行器、状态与显式恢复；TEST-WORD-PAGE-DISPATCH 对应合法 PAGE 生产者协议路由及冻结声明匹配。TEST-WORD-PREBUILT、TEST-WORD-EXECUTION-HOST 和 TEST-DESKTOP-VERIFY 延续各自原范围。FR-010、FR-037、FR-039 的 authoring 目录变动是 PAGE 接线及 PDFium API 兼容；FR-032 的 deploy 目录指纹随安装配套更新，备份/恢复 SQL 和生产恢复准入没有修改。

依赖固定采用生态锁文件、明确版本与部署镜像 digest。现有业务来源、转换资格和证据摘要继续承担来源与数据一致性校验，不另建逐依赖摘要登记框架。旧 Word03 记录保留原身份，新输出不冒充旧字节基线。

## 收尾状态

本轮实例已精确回收：16 个容器、2 个网络、14 个测试卷；登记的 16 个宿主进程身份全部退出，固定镜像保留。详见[普通安装与生命周期记录](installation-lifecycle/scope-result-summary.json)和[真实 Word 浏览器/API 验证](normal-word-browser/README.md)。早期失败保留其原结论；历史一次 S3 ClientError 在最终矩阵中未复现，不能倒推其原因为何。

已移除 774 个重复历史前端源码和 2 个可重建缓存，保留当前 24 个规范输入（其中 11 个 TS 模块参与构建）与必要验收记录；源历史可从固定 Git 提交恢复。未删除用户未跟踪数据、原工作区、私有配置、镜像或业务卷。应用 CI 11/11 全部成功，见[终态回执](ci/summary.json)；文档版本以本仓 `git rev-parse HEAD` 为准。


## 当前技术正文范围候选（587151c；同SHA实测待验）

当前已推送候选为 [`587151cced8f0501303c5b27abc06ccbbb55e5a3`](https://github.com/zys743551828-art/tender-app/commit/587151cced8f0501303c5b27abc06ccbbb55e5a3)。本节是后续工程补充，上述6446等历史结果、失败和未签收边界不改写。按用户2026-10-31最新范围，本方商务／报价／资质附件接入取消本轮交付；继续技术正文需求完整性、原页证据、精确冻结与判断。**根当前587151c栈的完整同SHA安装与业务实测仍在执行，尚不能宣称通过或整体完成**；不把C791 API、后续前端预览与5516 CI拼合成此次验收。

### 正常安装缺口与定向回归

| 实际缺口 | 最小修复及已取得证据 | 尚未证明 |
|---|---|---|
| configure helper缺 `requirements-parsing.lock.txt` 只读源挂载，严格来源读取报FileNotFoundError | 补齐正式helper／Compose等既有RO源清单，来源完整性要求不变；task `parser-source-mount-fix-20261031/host-02.xml` 为67通过，静态全0 | 不是完整安装通过；原始配置失败与收尾保留 |
| Word installation使用旧严格键集，漏接可选parser与base plan一致绑定 | `deploy/fusion_word_spec.py` 接可选parser并严格对齐request/base plan，未知键仍拒绝；task `parser-word-installation-fix-20261031/host-02.xml` 为93通过，1项Linux inode/owner用例在Windows跳过，静态全0 | 平台跳过不计通过；新整栈发布仍待实测 |
| Word host拒绝Desktop peer新增的精确RO解析锁目标 | 仅将 `/workspace/requirements-parsing.lock.txt` 纳入既有精确只读源码目标，其它peer约束不变；task `parser-word-peer-mount-fix-20261031/host.xml` 为114通过，10项实际Linux UID65532 helper用例在Windows跳过，静态全0 | 不放开任意宿主路径、可写bind或docker.sock；投影单测不证明实际host转换通过 |
| 正常verify仍把所有解析固定要求为旧通道partial | 按实际配置严格校验冻结context、来源与parser/config身份；新通道的两页纯文字fixture须complete且gaps为空，旧通道保留固定partial及原gap；`desktop-parser-qualification-fix-20261031/host-02.xml` 为30通过、0跳过，静态全0 | 不接受complete/partial任意一种凑通过；当前候选实际verify及业务仍待回执 |

历史候选 `e509e81` 的 `207a568821eb4409ab7af26ebdaf0ff4` 轮次已实际正常up，两个Word peer ready；verify因上述旧资格断言失败。随后正常stop返回Word stopped及cleanup_closed，精确回收17容器、3网络、15合成卷，剩余0。它只证明该版本启动与正常停止机制，不能替代当前候选完整业务、重装或视觉验收。此前长路径失败及peer拒绝轮次仍保留失败结论；独立精确恢复成功不回填为正常停机通过。

分轮测试scope不相加，也不能替代完整同SHA实测。最初basetemp父目录／explicit环境缺失导致的runner错误保留原记录，不算产品断言通过。Windows验收深路径 `WinError 206` 已用短验收目录与task导出处理，未改系统长路径策略；新clone与实例路径也建议保持简短。

<a id="TEST-PARSER-NORMAL"></a>
### TEST-PARSER-NORMAL

对应 `deploy/fusion_parsing_spec.py`、`packages/domain/parsing_profile.py`、`packages/parsing/`、`apps/worker/handlers.py` 与 `tests/test_fusion_parser_feature.py` 等。默认 `document/1` 固定partial；显式 `prepare --word --parser --parser-image <真实repository@sha256>` 才接私有parser及新 `document/2`，绑定parser/config和源字节，历史解析不重写。parser存在不等于OCR/表格或判断complete。配方及本机镜像分发边界见[使用说明](../../../使用说明.md#41-可选私有解析服务document2)。当前正常上传→document/2→冻结证据的完整链仍待587151c实测回执。

<a id="TEST-MODEL-ADMISSION"></a>
### TEST-MODEL-ADMISSION

对应 `deploy/fusion_desktop_model_admission.py`、`deploy/fusion_desktop.py` 与 `tests/test_fusion_desktop_model_admission.py`。首次模型 `up --model-admission-file` 读取公开官方价格／正数全局预算及相对来源快照，不是API密钥；专用非owner预算profile绑定数据库与模型，保留事务/用途/有效期检查。缺失、冲突、过期、模型不匹配拒绝启动paid角色，不用owner插表或默认价格绕过；该准入步骤provider_calls=0。旧手动预算证据和新正常安装机制分开，当前587151c首次启动实际结果待验。

<a id="TEST-WORD-STATIC-CORRESPONDENCE"></a>
### TEST-WORD-STATIC-CORRESPONDENCE

对应 `packages/word_intake/static_layout.py`、`packages/word_intake/prebuilt.py` 与 `tests/test_word_static_layout.py`。证明仅限单显式section、静态stories、固定表格与内联不透明PNG；源准入、字体、文字可见位置、表格栅格/单元格、像素、顺序与分页等原要求不降，未知结构保留gap/partial。

task `word-static-correction-20261031/complete-unit.xml` 与 `frozen-unit.xml` 均为198通过的同类定向回执，**不相加**。七份既有SYNTHETIC PDF只做新检查器重检，源DOCX/PDF绑定及检查结果可核，但未重新调用转换器；不等于七份新转换通过、复杂Word已采用或真实标书质量通过。必须用当前587151c profile重新真实转换、经Worker/API发布并核对采用资格后另记结果。PAGE仍仅 `one_canonical_footer_PAGE_only`，两项目录/实测页范围UNKNOWN和普通RICH `navigation_only`不升级。详见[Word核查](../../Word03来源与复现核查.md)。


### 候选 124fc6e：精确 Word 来源、保存恢复和输入边界

当前候选为 `124fc6e8005917d0dd98fa1c25a72bc301c1c3ca`，对应 [CI41214902947](https://github.com/zys743551828-art/tender-app/actions/runs/41214902947)；当前尚待终态及统一版本全栈验收。上一候选 587151c 的 [CI41208593659](https://github.com/zys743551828-art/tender-app/actions/runs/41208593659) 已 11/11 成功，不能替代新候选实测。

本候选补全受控 Word 三角色的材料包来源核验，客户端不能注入只读来源资格；保持衍生 PDF 非直接上传身份。相关关系/角色原生用例与前端回归通过，真实浏览器纵向路径仍以新候选实测为准。编辑器 ACK 后跨 schema 的完整文档比较修复和项目列表局部定位修复，保留真正未保存草稿和横向滚动。模型金额/输出上限早期拒绝已有真实 PG17 非 owner 及 rollback/idempotency 证据，无数据库协议放宽。

证据在已授权本机 task 的 `evidence-local-20261030/controlled-word-admission-20261031/`、`final-ui-observation-20261031/`、`model-native-20261031-144037/`；此处不上传私有配置、凭据或原始私人日志。新候选的统一安装、重装、正常停止、技术判定与实际像素验收仍待本轮后续记录。


### 候选 9a15c3d：API 健康检查预算

`9a15c3d989626f4ce27c035978a4b3472783c6f7` 只将 API 健康检查超时由 15 秒调整为既有公共探针的 40 秒，并补充 full 与 desktop 两个入口的配置回归。实测已有成功探针接近 15 秒、亦有超时；不能据此断言此前所有启动失败均由同一原因造成。修前新增用例三项按预期失败，修后 Linux 相关用例 112 通过、0 跳过；真实模型模式的全栈安装、业务及保留数据重启，以及本提交 CI 仍待终态。


### 9a15c3d 技术资料包的真实阻塞

本机最终候选的合成 10/14/12 三份 Word 已完成转换、采用、完整解析与两类资料包冻结；复杂图表样本的转换/版式核查通过，但正文解析仍如实报告图像与 OCR 缺口。编辑器保存恢复及 PAGE/RICH 的四个文件生成、真实下载已通过。PAGE 目录页码与章节实页范围仍为 UNKNOWN，RICH 的导航范围不等于正式业务资格。

真实技术规则请求在 prepare-review 阶段返回 `DEPENDENCY_FAILURE`，尚未产生模型请求或费用。对该合成测试任务以非 owner 工作进程只读重查，`validate_frozen_input` 报 PostgreSQL `42501 InsufficientPrivilege`：当前受控 Word 来源核验读取 `design.commands`，而工作进程不具备该表 SELECT。此为产品接线缺口；不将部分通过写成整链通过，也不跳过命令回执校验或直接授予全表权限。可能的修复是经评审的窄范围 Word 采用回执视图，仍需明确允许对工作进程增加该视图的只读权限并在隔离环境测试；未对生产或现有数据库扩权。

本机证据见 `evidence-local-20261030/final-technical-9a15-clarified-review/readonly-diagnosis.json`、`final-controlled-word-9a15-clarified-installed/` 与 `final-authoring-9a15-model-installed/`。同一 SHA 的保留数据重启和 CI 继续跟踪；真实模型质量、正式评分和真人签收均未验收。
