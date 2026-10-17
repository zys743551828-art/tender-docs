# RICH专用生产器与转换端口静审候选01

本包只请求具体源静审；未运行新RICH Docker/LibreOffice，未获PAGE2安全结论自动继承。M1完整人工成稿未完成，原PAGE02/树计划模板/Word03/导航01都保留各自范围。

## 接口与安全闭包

- rich_docx.assemble(snapshot, materials, pagination=...) 产确定性真实DOCX：原生表格grid/gridSpan/vMerge、严格段落/列表、规范PNG内嵌drawing及内部关系。材料按(asset,revision,version)精确allowlist，PNG实际SHA/尺寸/正常化身份复核。整个快照16MiB、DOCX64MiB、规范PNG总64MiB、500材料、每图1600万/全稿使用次数累计3200万像素。
- rich_verification.verify 必须先校生产器/完整manifest，重新assemble逐字节比较，再检查有界ZIP及全部新XML标签/属性/规范PNG/关系。动态能力仅一个规范页脚PAGE；稳定sec_UUID书签与目录内部hyperlink，无外部关系/动作/任意字段；新子树检查之后的其余包交不变的PAGE2验证器再查。旧PAGE-only仍拒此包，外来Word03源完全不改。
- pagination闭合{mode:chapter_start_new_page|continuous,toc_start_pages:{完整active UUID:int|null}}。mode由未来明确build选项冻结，不能暗改旧模板。producer按精确段落计数定位章标题及目录，不按全篇标题字符串首次命中；每项title单独内部hyperlink、数字另run右tab。新页模式章标题pageBreakBefore，父章范围由导航模块按下一非后代章实际起页-1包含后代；continuous章末仍unknown。
- rich_renderer.profile_for(Word03base)绑定精确三文件/字体/镜像/选项以及本包生产器、检查器、导航01及软件配置。prepare先全量精确产物检查，render_once才复用原Word03受限容器low-level helper。没有HTTP/DB/model入口，operator path只由rich_profile私有环境读取，不接用户路径。
- render最多3轮实际排版，下一轮目录数字只用上一轮真实PDF内部dest/目标页首标题核验的数字。converge_navigation再次读每轮真实PDF，完整正文数字含最终TOC数字分别绑定，不忽略数字。未固定点partial且给缩短标题/调整字体版心的建议。每轮DOCX/PDF bytes交调用方持久保全；后轮失败时旧导航只放navigation_history_proof，不冒最终失败DOCX的章页。
- Word03 runner/image/字体/network-none/nonroot/read-only/资源限额/清洁环境/自有清理不改。临时目录、容器均服务器生成。公开runner模式0444仅为非root可读；私有安装profile600，真实运行根改变须重建准确base及派生profile，不能手改摘要。

## 对应资格与当前真实边界

PDF文字/所有数字/页眉脚仍沿已审PAGE2实际PDFium几何完整比较；图片进一步核实际PDF图片decoded RGB像素、尺寸、版心与页面渲染crop可见性，透明图片明确partial。合成PDF已验证同一raw image被白色图形遮盖不能过可见性。

当前有表格时始终保留 AUTHORING_TABLE_PDF_GEOMETRY_UNVERIFIED：已产真实原生表格并比较完整cell text，但还没有实际LO表格边界/跨页几何验证，不将完整文本摘要冒版面保真。这个缺口须后续实际表图PDF与几何增量解决才能满足M1；本包不声称已交付完整表图PDF。实际LO是否保留directDest/PNG原像素仍未知；若出GoTo action、丢目标或lossy像素，严格partial，保留首失败后另交精确delta，不能临场放行。

导航模块只证明导航，不能授通用PDF安全或全部视觉资格。只有所有对应检查和真实固定点都通过才可将产物标complete；代码没有把导航16小PDF或mock adapter当真实RICH渲染。

## 验证与保全

固定350输入逐字节，12 own/338 deps。58离线通过：30 RICH规则/实际ZIP/攻击/2实际合成PDF图片可见性，12专用准入/档案/策略与显式mock-adapter loop控制，16导航真实小PDF。Docker/requests/httpx真实transport/模型from_env在专属准入测试硬阻。ruff/format/mypy9源/Bandit0 issues。公共Pillow12.3.0复用已有requirements-parsing.lock，未改依赖锁；不收费/不启动消费者。

首静态preflight58过但Bandit指出narrowing assert，原350字节/日志移至rich-port-preflight-01并有archive-map；修为显式错误并将图像分析异常降partial保留已转换PDF，另冻本包。原JSON/日志不重写。synthetic-rich-source.docx是真OOXML产物，只是无PG资格的合成快照，没有实际PDF转换。

API/资产13 PG机制是另个移动开发切片，当前static固定根的legacy repository/engine来自PAGE02，不能把本包当RICH API/Worker已注册。SQL元数据已独立审并fresh，但不在本包执行。后续source→资格短事务→关DB对象/渲染→最终fence/资产/来源重核由另候选收口；本包安全端口实际执行须PM在独立静审后授权。
