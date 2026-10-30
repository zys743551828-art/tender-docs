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

## 最小外部材料与下一可执行步骤

只需提供以下任一项（不需要账户口令）：

1. **首选：从原Word03验证环境导出的镜像归档**，其image ID必须为上面的完整ID。原环境操作者可执行`docker image inspect <原ID> --format '{{.Id}} {{json .RepoDigests}} {{.Os}}/{{.Architecture}}'`并用`docker image save <原ID> --output word03-base.tar`导出，同时给出归档SHA256。需要镜像归档，不是容器文件系统export。
2. **或者：可信、不可变的原registry引用与来源记录**，形如`repository@sha256:<manifest-digest>`，且目标平台配置的image ID经inspect必须精确等于上述原ID。只有版本tag或“某个postgres镜像”不够；若私有registry确实需要授权，再针对该已知地址讨论现有只读访问，不索取或新增持续凭据。

获得后先校验归档/来源、加载到现有隔离daemon并再次核对实际image ID/环境字段，再用原build_profile和PAGE/RICH工具生成新路径的受审profile，按原隔离规则运行真实DOCX→PDF及表图/目录/页码对应检查。当前宿主资产已匹配，不需要扩大到生产部署、真实模型或生产数据库。

本轮结果：只读预检完成；构建和真实转换**未运行**，状态为待外部原镜像材料，不是权限拒绝，也没有结果不确定的外部写动作。应用源码未改，既有9/9CI仅作原提交历史证据；本轮新增检查为资产摘要、来源/构建入口审查和文档同步检查，不宣称重新执行原完整套件。

用户本机Docker的独立核查同样未找到冻结镜像；项目内两份tar经目录结构检查为源码包，没有Docker save manifest。已有7.4.7旧供体镜像未替代指定转换器。[本机证据](fusion/local-docker-20261030/evidence.json)与[范围说明](本机环境与Docker收尾验证.md)记录结果；原指定真实转换仍未执行。
