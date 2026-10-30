# 生产 nginx PDF 原页预览验收

最终有效轮 `word-production-dist-preview-20261031-024457` 通过，exit0、cleanup_closed=true。主机 ZHANGYANSONG，UTC2026-10-30 18:44:57.208498至18:46:22.837181；本机UTC+08:00为2026-10-31 02:44:57.208498至02:46:22.837181。实际 browser.version() 为134.0.6998.35，与此前固定Chromium134基线相同；未升级浏览器、注入自造polyfill或放宽安全参数。

实际URL为 `http://127.0.0.1:8080/bids/5962bdd3-9c82-4f39-870f-a65cf4620ed2/materials`，处于本轮QA/web共享网络命名空间。复用既有六主容器隔离拓扑、官方nginx1.30.5固定digest和原full双配置；API alias api/8000，APP_ORIGIN同127:8080，无Vite/额外代理。宿主受限Word执行器仍是宿主进程，不声称自身容器化或full Compose已安装。

为到达新增状态，仅走一次简单合成中文Word真实成功链：同源上传、实际转换/采用、默认parse partial，然后通过ArtifactActions“查看此版PDF原页”进入Evidence。未重复120s超时、重试或其它页面验收。PDF实际浏览器响应字节与当前S3 PDF相同，见preview-bytes.json；两个文档版本、一个qualified render、物理页1及真实partial缺口由verification.json佐证。

新覆盖实证：桌面来源侧栏(complementary)canvas454×587，999个非白像素；390×844原页弹窗(dialog)canvas326×421，603个非白像素。两端都显示物理页1，唯一组合框value=1/options=[1]，实际accessible label含选项值“物理页1”。两端切换“解析文本”显示真实提取的“施工工期120天，质量合格。合成容器浏览器Word验证。”（实际文字含自然空格）。这不是PDF.js textLayer：现有产品使用canvas原页+独立解析文本页，未新造渲染引擎。

懒加载 `pdf-q-5ow4VI.js` 与匹配 `pdf.worker.min-Dkey6ZUl.mjs` 实际HTTP200，真实PDF.js Worker创建两次。所有本轮请求的static/PDF资源无404或其它错误；只有登录前正常GET/auth/session401被明确分类为预期挑战，其它API错误仍拒绝。page_errors为空。记录到两次真实“正在绘制PDF原页…”加载状态，最终加载消失后才读取非白像素。仅证明本PDF实际请求资源，不声称整套CMap/标准字体/WASM资产均被请求。

桌面点击“关闭原件”、390按Escape、第三次打开后即Escape均返回原“查看此版PDF原页”按钮焦点。每次关闭后2300ms预览请求数分别4→4、8→8、11→11，没有额外请求或迟到弹窗。第三次取消时未观察到PDF绘制loading，inflight_cancel_claimed=false；此处只验证预览关闭后不继续/重复请求，不宣称中断在途PDF传输或取消Word任务。

PDF.js真实兼容缺陷与修复路径完整保留：023439中modern6.4.299模块/Worker均200且Worker创建，但界面报a.toHex is not a function、canvas不出现。官方同锁版本legacy库和legacy Worker自带所需兼容实现；root仅成对切换PdfPreview导入，未改锁、浏览器、安全或业务资格。新[Linux生产构建](../../web-build-20261030/pdf-preview-compatibility/README.md)成功，组件SHA75cc115184f66b818813ef84dc9c83a1a76bed52f966bf653d5d39dd100e6d30。root确认提交188e2d676624fd9f99efa25418579b0c75f49838中该组件blob与新dist sourceSHA一致；起始HEAD未冒称为该提交，按实际SHA/产物证明对应，见preview-component-source-freeze.json。未复制dist。

`/nginx-health`实际通过原nginx代理返回200、database=ready、model=not_configured，无模型调用。最终轮首次即200；023439另保留真实502→502→502→200、0.7685s启动接线过程。023114首次502时driver尚无有界等待，因此在启动浏览器前停止；这不是Word或浏览器主链失败，也不豁免持续502。当前30s有界等待仍要求最终正确200载荷。

四轮原始资源事后逐精确ID inspect全部不存在：023114为6主容器+1网络、无conversion；023439、024114、024457各6主容器+1网络+1瞬时conversion，共31个ID。每轮closed，宿主执行器已退出，镜像/卷未碰，见all-rounds-cleanup.json。运行中API/web/Worker的uid65532、readonly/capdrop/tmpfs/sourceRO和QA共享namespace均有实际inspect；API/Worker真实DB角色非owner/superuser，资格函数仍在LinuxWorker本地执行，宿主只收窄转换请求，无模型密钥或调用。

prior-attempts按实际分别记录：023114是浏览器前健康等待夹具不足；023439是真实产品兼容缺陷；024114在legacy中文canvas及非白像素已通过后，因harness exact-label过严停止，原failure退出不改。最终仅将页码定位改为role combobox name /物理页/，仍严格唯一/value1/options[1]，没有为迎合选择器改产品。失败scope、原summary/resources、browser-failure、PNG、sourceSHA和转换收据保留；失败宿主记录因Windows路径长度平铺存放，无私有日志。

local_config_audit实际view本轮七张viewportPNG：desktop canvas/text、390canvas/text、390focus-return，以及desktop/cancel回焦点两图。中文原页与文字可见，partial缺口和“转换不授予Word检查或评审资格”告知保留；390关闭、页码、切换、回焦点环清楚，无横向溢出或内容遮挡。实测桌面scrollWidth=1440/viewport1440，390为390/390；这些是真实viewport截图，不由fullPage滚动捕获推断未截图状态。root另实际view desktop canvas、390canvas、390text、390focus-return四PNG，确认原页绘制、文字/partial与资格告知、关闭/页码/切换和回焦点正常，无横溢。A4整页按当前比例缩小时原页文字较小，仅代表本合成样本，不扩称任意复杂版式或完整缩放覆盖。

仅该新增预览coverage已完成。完整Tab循环、Word任务取消、多页或其它复杂PDF、page/rich完整浏览器生成链、其它业务页面、Kafka、模型、生产部署未测；原partial仍表示结构化表格/图页未核验，仅提取物理页文字，不等于审评通过。执行版runner/mjs原字节快照与runtime JS SHA一致，本agent未改应用源码/Git，旧production-dist-browser与conversion-v2证据未改。
