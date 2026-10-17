# RICH端口静态与独立反例审查

结论：**存在RP01/P2图片可见完整性误判，当前候选不能作为图像完整检查通过的依据；Owner修订检查器及派生profile后增量复审。** 没有把该问题认定为沙箱逃逸；本轮已读闭合生成器/OOXML/profile范围未见其他新增阻塞。尚未授权或执行真实新RICH Docker转换，不能将本轮视为M1通过。

精确候选SHA256 `e2f15d90940acb10e31cfb85331d22844cce47575fd12855cfb8821d9d08fa31`，12自有/338依赖共350固定输入执行前后匹配，11份开发证据及HANDOFF等摘要核对。自有执行器直接读固定树，以python -B关闭字节码、关闭pytest cache、硬阻DB/HTTP/子进程/模型。58项原离线检查全部通过；新增本人独立实际PDFium反例仍检出缺陷。

## RP01 / P2：全图平均误差掩盖稀疏内容消失

定位 [rich_pdf.py](/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/rich-port-static-01/frozen-tree/packages/authoring/rich_pdf.py:176)：实际页面crop与源图仅用整幅平均差异比较，非平面图允许均值<=3，平面图<=1。原始嵌图像素摘要和摆放尺寸一致，只能说明PDF仍保留该图像对象，不能说明其内容实际可见。

独立反例生成规范不透明120×90 PNG（白底，仅黑数字9），按120×90pt嵌入实际PDF，然后增加25×25pt白矩形盖住数字。检测返回coverage=complete且mean_error=0.4；独立PDFium真实渲染检查，图像crop原54个非白像素变为0，数字完全消失。未使用Mock转换器，不执行Docker。证据：[脚本](partial-cover-counterexample.py)、[检测结果](partial-cover-counterexample.json)、[独立渲染读回](rendered-cover-evidence.json)、[原PDF](image-digit-baseline.pdf)、[遮盖PDF](image-digit-covered.pdf)及同名PNG。原58测试只覆盖整图被遮盖，不能代表小面积关键数字也可检测。

影响：仅有图片而无表格的稿件可以通过该图像检查，稀疏数字/签章等小面积内容被遮后仍得到图片complete。没有复现完整端到端build错误发布，本轮也未授其资格；有表格的检查仍明确partial。

建议采用保守的局部/前景内容损失检测，兼顾实际抗锯齿和缩放；无法可靠证明时保留图片partial。不能单靠把全图阈值从3稍降，因为内容面积仍可任意小。验收应包含原数字遮盖、稀疏多字/细线、局部覆盖、正常缩放/抗锯齿、透明图与全图遮盖，新的检查器身份必须进入派生renderer profile。Owner已接受该反例，保留01原件并计划另冻02。

## 已读安全与资格边界

准入先核完整manifest/生产器，再从冻结snapshot和精确asset版本材料重assemble逐字节；随后有界ZIP/全部新XML标签属性/关系/PNG检查，再用未放宽PAGE验证器核剩余包。图片仅规范PNG内嵌、无外部关系；稳定书签与内部目录hyperlink、唯一规范页脚PAGE均封闭。包/图片/像素/使用次数预算明确。原外来Word03三文件与沙箱runner未改，外来Word仍拒字段。

profile固定Word03基础资产及新生产器/检查器/图像/导航摘要，执行前真实build_profile须相同。复用无网/非root/只读/资源限额及自有随机容器清理。三轮排版每轮都先准入实际DOCX，再从真实PDF的直接内部dest、首标题与印刷数字派生下一轮；最终失败仅保留navigation_history_proof，不把旧结果冒最终章页。实际DOCX/PDF两端SHA由render_once链绑定；原16导航小PDF不当真实LO收据。

文本与全部数字（包括实际目录数字）、页眉页脚独立几何比对；表格native grid/gridSpan/vMerge生成且完整cell文字参加正文检查，但目前表格始终AUTHORING_TABLE_PDF_GEOMETRY_UNVERIFIED，不冒表格布局完整。透明图仍partial。实际LO directDest/PNG保真、跨页表格/真实RICH稿、API/资产/Worker/fence完整接线均未验。

本轮0Docker/DDL/DB/云/模型/消费者，没有复制350源或创建大型资产。独立证据、输入与JUnit见同目录；实际新资源窗口应在RP01修订稿复核后由PM安排。
