# Word03来源与复现核查（2026-10-30）

本次有限核查应用dev `283e334186cec0bfedce91585846470cea700474`，不重复已通过测试套件，不修改转换器、期望摘要或准入规则。[实际资产/源码/镜像预检证据](fusion/word03-provenance-20261030/evidence.json)。结论：宿主配套资产内容已全部对上，缺少原精确基础镜像及可定位的获取来源；当前无法构建原镜像或执行规定的真实转换。


## 当前静态对应机制补充（587151c候选；正常安装及最终集成待实测）

本页以下Word03来源结论与回执保留历史时点。当前Gotenberg预构建路线新增 `packages/word_intake/static_layout.py`／`word-static-correspondence/1`，新profile源码身份包含检查器；它复用现有PDFium/RICH纯检查，是受限证明适配，不是新转换器或任意Word排版保证。

候选范围严格为单一显式页面section、静态正文/页眉页脚、固定原生表格及内联不透明PNG。源DOCX先过原安全准入，宏、动态域、外链不因此放开；实际PDF还须核对文字可见位置、静态stories、页边距、表格栅格/单元格、图片像素与归属、对象顺序及显式分页，并满足原字体与转换隔离/清理证明。未知或不支持结构保留gap/partial，不能以HTTP200、全文字符串相等或单测代替资格。

**源码已实现不等于重新转换验收已通过。** 用新检查器重检旧SYNTHETIC PDF，只证明检查器与既有输出的对应情况，不能宣称587151c候选新profile重新执行DOCX→PDF成功。最终仍须从同SHA准备新profile、真实独立转换、绑定源DOCX/实际PDF/receipt，经Worker发布与API使用；本轮最终安装/业务链由唯一运行器执行，尚待回执，不预写通过。旧复杂Word partial及旧回执不回填，Word03冻结源不改。

PAGE的 `one_canonical_footer_PAGE_only` 仍只允许规范页脚PAGE；`TOC_PAGE_NUMBERS_UNKNOWN` 与 `ACTUAL_SECTION_PAGE_RANGES_UNKNOWN` 不因静态适配消失。普通RICH `navigation_only` 与独立compute complete仍是不同边界。可选parser document/2接解析身份/证据通路，不代替Word采用或PAGE目录资格。本方商务／报价／资质附件接入已取消本轮交付，历史材料与检查不删除。配置见[使用说明](../使用说明.md#41-可选私有解析服务document2)。

## 原规范究竟固定什么

`workbench/packages/word_intake/renderer.py:28`固定本地Docker image ID：

```
sha256:327daa8fae7178d61f93142f146b098467b345e10997b9eb79f63bd58e5c8f3c
```

这是代码交给`docker image inspect`并与`image.Id`核对的本地镜像身份；仓库未记录可拉取的`repository@manifest-digest`、RepoDigests、精确基础镜像tag/版本、构建上下文或原镜像归档。不能把该ID当作已知registry manifest引用去猜测拉取。设计文档仅称“既有postgres基础镜像”；这只是家族线索，不足以确定精确来源。

运行时使用`--pull never`、显式本地socket、空Docker配置和隔离环境。LibreOffice、Python、系统库、字体以及受审runner通过固定只读挂载进入容器，实际入口是`/runner/python -I /runner/run.py`，不是镜像默认postgres入口。容器需保持network-none、非root、只读根、无capability及原资源限制。

`generated_renderer.py`另固定renderer/inspection/sandbox_runner三个原始源码SHA；PAGE和RICH通道复用精确Word03基础身份。路径移动时按原工具重新生成新安装profile，不能手改旧profile摘要或放宽常量。历史公开profile不是自动可安装的私有运行资格。

## 实际检查与分类

| 项目 | 结果 | 分类 |
|---|---|---|
| 本地daemon | 28.4.0，可访问 | 无daemon权限/连接阻塞 |
| 精确image inspect | exit 1，No such image | 缺本地缓存 |
| 精确来源 | 五仓受控源码/文档及renderer的全部Git历史没有registry引用、原镜像归档或生成该ID的构建材料；最早源码随27bc639归档进入 | 同时缺可定位来源，不能只用pull解决 |
| 宿主资产 | 10组资产摘要均与原profile一致；runner只改安装路径，其内容摘要也相同 | 无需用户重复提供LibreOffice/字体/系统库 |
| 三个Word03源码 | renderer 547b94cb…、inspection 76032305…、runner ec7069c4…，均保持原审定字节 | 不需要重写转换器 |
| 宿主程序版本 | LibreOfficeDev 26.8.0.0.alpha0，Python3.13.5；完整字节摘要见JSON | 真实本机观察，不声称外部最新版本 |
| 远端访问/凭据 | 因没有确切原引用，未向猜测的registry发送pull | 不能归类为已证实的远端不可访问或鉴权拒绝 |
| 商业授权/付费 | 未发现本链要求购买授权的记录，也未触发任何购买或凭据配置 | 当前没有此类已证实阻塞，不据未知来源推断许可要求 |

主资产比较使用原`_asset_manifest()`逐文件计算；不是仅检查路径存在。旧profile的runner路径为`/workspace/tender-workbench/...`，当前为`/workspace/tender-render/workbench/...`，按同一runner文件对齐后内容摘要相同。最终profile仍须由原工具在实际新运行根生成并复验，不能直接复用旧路径的profile。

## 已有Dockerfile为何不能直接构建替代

仓库的`deploy/word/Dockerfile`由eba0a56引入，属于根目录旧供体的独立Word服务：FROM python:3.11-slim-bookworm、LibreOffice writer 7.4.7包，复制service/manifest/validator等文件并运行service.py。它没有原image ID或生成该ID的锁定材料，也不等于上述宿主挂载的26.8开发版/3.13 runner。其基础FROM也未固定registry digest。Word03原始归档27bc639中没有这个Dockerfile。

因此没有执行这个已知不匹配的构建，没有换用现有pgvector镜像，没有改IMAGE/期望SHA，也没有在宿主机直接转换来冒充沙箱验收。构建同类服务会产生另一条转换协议，超出本次“原指定转换器”核查授权，不能当作原验证通过。

## 原基线恢复路径

如果原环境仍可联系，可由项目维护方自行追回以下任一材料；这是恢复原基线的可选路径，不是把未交付的镜像包转为用户必须提供的前提，也不需要账户口令：

1. **首选：从原Word03验证环境导出的镜像归档**，其image ID必须为上面的完整ID。原环境操作者可执行`docker image inspect <原ID> --format '{{.Id}} {{json .RepoDigests}} {{.Os}}/{{.Architecture}}'`并用`docker image save <原ID> --output word03-base.tar`导出，同时给出归档SHA256。需要镜像归档，不是容器文件系统export。
2. **或者：可信、不可变的原registry引用与来源记录**，形如`repository@sha256:<manifest-digest>`，且目标平台配置的image ID经inspect必须精确等于上述原ID。只有版本tag或“某个postgres镜像”不够；若私有registry确实需要授权，再针对该已知地址讨论现有只读访问，不索取或新增持续凭据。

获得后先校验归档/来源、加载到现有隔离daemon并再次核对实际image ID/环境字段，再用原build_profile和PAGE/RICH工具生成新路径的受审profile，按原隔离规则运行真实DOCX→PDF及表图/目录/页码对应检查。当前宿主资产已匹配，不需要扩大到生产部署、真实模型或生产数据库。

本轮结果：只读预检完成；构建和真实转换**未运行**，状态为待外部原镜像材料，不是权限拒绝，也没有结果不确定的外部写动作。应用源码未改，既有9/9CI仅作原提交历史证据；本轮新增检查为资产摘要、来源/构建入口审查和文档同步检查，不宣称重新执行原完整套件。

用户本机Docker的独立核查同样未找到冻结镜像；项目内两份tar经目录结构检查为源码包，没有Docker save manifest。已有7.4.7旧供体镜像未替代指定转换器。[本机证据](fusion/local-docker-20261030/evidence.json)与[范围说明](本机环境与Docker收尾验证.md)记录结果；原指定真实转换仍未执行。


## 可复现交付责任与版本化替代方案

本地复核应用`c0b659378cfc7884bc3d58fd7c324bace0bea1e5`的renderer与原D盘仓库相关Git历史：renderer首次随`27bc63909dd176676ea918beeee745daebdb8f6a`整份交接归档进入；旧供体Dockerfile在`eba0a56ce83af65dc853af9c10e2c66f37ba7e97`引入。当前活动克隆为浅克隆，因此历史判断复用了本机原仓库可见历史，不声称覆盖不可见或已丢失历史。未发现生成原ID的构建/拉取/归档记录；这一缺口属于项目可复现交付缺陷，应由工程维护补齐。

现有`word03-base-profile.json`与`renderer-profile-02.json`记录原image ID、资产摘要和PG_VERSION等镜像环境变量名，支持“当时保存了具体运行身份”的判断；它们不是镜像构建证明或独立可验证的registry收据。前端`word03-final-affected-tests.log`是10项Vitest通过，不能证明LibreOffice真实转换。已有后续阶段记录明确转换未运行。由此不能断言该镜像从未存在、AI虚构，或当前registry发生鉴权失败；可以确定现有交付材料不足以重新取得同一身份。

固定值是本地image配置身份，运行时还与inspect的Id比较，并不是带仓库名的registry manifest引用。[Docker拉取文档](https://docs.docker.com/reference/cli/docker/image/pull/)的按摘要拉取使用`repository@sha256:...`；[OCI配置规范](https://github.com/opencontainers/image-spec/blob/main/config.md)区分镜像配置与分发描述。因此不能对裸ID直接猜仓库pull。即使某个postgres tag可下载，也不能由tag家族推导它是原身份。

历史执行证据进一步包括`workbench/docs/word-mainflow-development-checks/HANDOFF.md`及同目录`fixed-mainflow-02.xml`：归档在2026-10-17T03:05:39Z记录13项非owner原生测试、0失败/0跳过，包含真实无网Docker转换用例；`native-readback-02.json`保存Word/PDF版本、解析身份、complete覆盖和实际对象字节回读摘要。配套冻结测试代码调用真实storage/Worker并核对采用、解析及下载字节。这是历史转换执行记录，强于单独profile，但未记录原image ID的构建或分发过程，也不是本轮独立重放。准确结论是“历史记录过受控转换，当前可复现交付链缺失”，不能推断从未真实运行。

### 采用现成开源组件与预构建镜像

用户已明确Word转换不自行构建镜像。新路线优先可信维护者发布的现成开源组件或预构建镜像，不再采用自建/2转换镜像实施包；旧Word03的历史结果与冻结身份保留。项目维护方核实发布来源、精确版本及平台manifest/config digest、许可证、中文字体和布局、API适配与运行隔离，再在本机独立验收。现成组件若不能满足明确必要能力，应列出差异，不改用自建镜像规避要求，也不要求用户寻找未交付的原镜像。

新镜像输出不等同旧Word03字节。真实DOCX到PDF、页码/目录/中文字体/表格/图片、异常拒绝与资源清理须重新验收；旧PAGE/RICH资格不自动转移。没有真实验证前不切换冻结IMAGE/哈希，不重写旧结果。

依赖固定采用生态标准锁文件、精确版本、部署镜像版本/digest及包管理器内建完整性验证，不另建逐文件摘要注册表或复杂版本门禁；不得使用latest等浮动部署引用。外部模型API按用户决定不纳入版本锁，通过厂商/协议端口按需配置；当前仅实现DeepSeek，仍保留费用与资格校验，不调用付费模型。新Word路线的选型比较与实测边界见[架构决策](../05-技术与设计/工作台实现架构.md#adrword-转换采用成熟组件与薄适配)。

## 新预构建方案实际验收

官方Gotenberg 8.39.0预构建组件已在用户电脑执行，新协议不改旧Word03冻结身份。CLI中文、复杂输入partial、外来域拒绝三项符合预期；PAGE合成样本complete。RICH合成两页样本含中文单元格、目录点引导和实际页码、图像与表/图题，最终complete、无gaps。[源件](fusion/word-prebuilt-20261030/rich.docx)、[实际PDF](fusion/word-prebuilt-20261030/rich.pdf)、[第一页](fusion/word-prebuilt-20261030/rich-1.png)、[第二页](fusion/word-prebuilt-20261030/rich-2.png)及[结果](fusion/word-prebuilt-20261030/rich-summary.json)可核对，实际像素已查看。

新适配处理PDF表示差异：半描边宽表格接合、冻结目录可见点引导、实际源锚点的`_`与`5F`名称表示。转换排版交给现成组件；原文字、数字、表格、图题位置与像素、目录固定点检查保留，失败不授予资格。旧资料中任意表格和真实RICH联合检查原本仍未验，不能把旧文本主链complete误当所有复杂版式通过。

[正式原生入口](fusion/word-prebuilt-20261030/worker-summary.json)1通过、0失败、0跳过；[领域回读](fusion/word-prebuilt-20261030/worker-result.json)包括非owner PG17、Cookie/CSRF API、私有S3、outbox/fence Worker、实际PDF/原件下载字节、采用关系与匿名拒绝。默认解析器仍为partial；Kafka broker及Windows产品同源分片上传不在此测试中。父级和转换子级回执均closed，另有超时/取消回收回归。

有限样本不覆盖任意复杂标书、全部跨页合并表格、任意字体和图文环绕；须用业务代表原件继续验收，不宣称完美兼容、生产或真人签收完成。原材料仅作历史保留，不要求用户提供旧包才能使用新方案。


工程检查映射：TEST-WORD-PREBUILT（范围见本页，不等于正式业务验收）。


工程检查映射：TEST-WORD-RICH-PREBUILT（范围见本页，不等于正式业务验收）。


工程检查映射：TEST-WORD-CLEANUP（范围见本页，不等于正式业务验收）。
