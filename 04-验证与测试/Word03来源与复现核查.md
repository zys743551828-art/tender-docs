# Word03来源与复现核查（2026-10-30）

本次有限核查应用dev `283e334186cec0bfedce91585846470cea700474`，不重复已通过测试套件，不修改转换器、期望摘要或准入规则。[实际资产/源码/镜像预检证据](fusion/word03-provenance-20261030/evidence.json)。结论：宿主配套资产内容已全部对上，缺少原精确基础镜像及可定位的获取来源；当前无法构建原镜像或执行规定的真实转换。

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

### 两条路线及建议

1. **原身份追回**：由维护方找到原环境、原镜像归档或配置ID可核对的不可变分发引用，保留原源码/字体/隔离规则；有材料才执行上节真实转换。继续无目标地搜索或索取用户模型key不能解决此问题。
2. **新版本转换器**：若无法追回原包，建立独立`word-docker-render/2`候选，使用受控官方依赖和可复现构建材料重新验收；不把新的输出称为Word03原基线复现。建议采用此工程修复路线，但启用新基线前须由用户明确确认范围。

### 新版本的最小实施包

| 工作 | 具体产物与完成条件 |
|---|---|
| 锁定工具链 | 新目录保存Dockerfile、构建上下文、平台linux/amd64、基础镜像repository@manifest、实际配置ID；LibreOffice稳定版、Python与系统包精确版本/下载SHA/来源快照。不能直接沿用旧Dockerfile浮动FROM，亦不能声称重建出原ID |
| 自包含资产 | 将转换器、runner、动态库与可再分发字体纳入候选镜像和资产清单，移除对Codex专属绝对路径及整台宿主系统库的依赖；保留每个文件摘要及版本清单。Docker客户端/daemon协议另作安装前置，不挂载socket进转换容器 |
| 来源与许可记录 | 保存[LibreOffice许可入口](https://www.libreoffice.org/about-us/licenses/)及实际所选分发包的许可证/通知；[Noto CJK许可证](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE)按具体所用字体归档。其它字体逐个核实来源和再分发条件；不得把用户系统中的商业字体默认打包。缺所需字体时保留替代/字形缺口，不宣称排版一致 |
| 交付镜像 | 在干净隔离构建环境验证完整命令；导出docker-save包、归档SHA、镜像配置ID/RepoDigests、SBOM/来源锁及操作说明。另在没有开发者缓存的隔离daemon加载并核验，明确“功能可重建”和“逐字节同一镜像”两种标准，不提前保证构建字节确定性 |
| 运行适配 | 新版本独立profile与安装器，固定镜像/字体/选项/源码；保持network-none、非root、只读根、无capability、受限内存CPU/pids/超时/输出，禁止宏与外部链接。不允许任意镜像或host路径由请求指定 |
| 精确收尾 | 使用本机一次性资源规范记录run ID、容器与临时目录，在成功/失败/超时/取消后按精确身份收尾；镜像交付物需保留时在回执明确其用途，不进行全局prune |

旧供体服务可参考其构建包版本和隔离实现，但其Python3.11、LO7.4.7、服务入口与协议均不同，不能直接接到原Word03常量。新候选应使用一套明确锁定、可分发的工具链，不拼接旧服务与原宿主开发版身份。

### 新版本重新验收与旧基线影响

先跑拒绝与隔离反例：宏/外链/不支持包、缺字体/字形、错误镜像/profile、超时/大输出、异常与取消清理。随后使用有来源摘要的合成DOCX执行真实转换，覆盖中文和数字、页眉页脚、节/页边距、图片、普通及合并表格、跨页/重复表头、目录和页码、混排顺序及可见遮挡。逐页核对PDF文字/字形/几何/栅格和源DOCX，不以“文件可打开”作为通过。超出已审接受域继续partial。

原生集成须在独立非owner PG/S3中走外部Word接收→转换→页码证据及PAGE/RICH生成通道，并检查报告/后续使用资格和历史回执隔离。新版本要重新冻结adapter、inspection、runner与检查器摘要，受影响的generated_renderer及profile门控须按新协议审查，不以修改旧期望来放行。

LibreOffice版本、字体、库或构建环境变化都可能改变PDF字节、分页和几何。旧PDF/profile/原件链、历史验收结果原样保留；新输出保存新的版本身份与验收回执，旧运行不自动获得新资格。新版本通过也不倒推Word03已复现，更不授予真实投标排版、模型质量或生产使用资格。

本轮只完成来源审查与可执行实施方案；没有拉取/构建替代镜像、改变IMAGE/源码冻结摘要或重写旧结果。下一需要确认的是“允许创建并验收独立新版本转换基线”，不是要求用户补交AI工作时未提供的原包。
