# PDF 原页兼容修复与生产构建

应用`188e2d676624fd9f99efa25418579b0c75f49838`仅将原页预览成对切换为同一锁定PDF.js6.4.299包的官方`legacy/build/pdf.mjs`与legacy Worker，并更新构建说明。未改变Gotenberg转换、字体/PDF业务资格、浏览器版本或安全设置，没有自写polyfill。

原生产补验发现真实缺陷：Chromium134中modern Worker直接调用`Uint8Array.toHex`，资源HTTP200且Worker真实创建，仍报`a.toHex is not a function`而无canvas。已锁包的README指引旧环境使用legacy；随包legacy库与Worker自带检测和标准兼容实现。失败、修复后首次选择器过严及最终成功均见[浏览器原证据](../../word-prebuilt-20261030/production-dist-preview/README.md)，不把失败轮改写为通过。

[生产构建](build.log)于2026-10-30 18:39:45–18:40:03 UTC成功；[产物与组件源摘要](summary.json)和[实际组件源码](PdfPreview.tsx.txt)留证。根核实commit blob与该实测源码均为`75cc115184f66b818813ef84dc9c83a1a76bed52f966bf653d5d39dd100e6d30`。目标格式、TypeScript及ESLint检查通过，完整CI独立按该提交跟踪，不借用4108绿灯。

同一Chromium134真实回归现已通过桌面/390px原页canvas、中文解析文本、物理页1、legacy资源200/实际Worker、关闭与Escape焦点返回及关闭后无新预览请求。取消时绘制加载已结束，不声称在途传输中断；任意复杂标书、完整缩放、正式Word资格和业务/生产签收仍未由此证明。未复制整份dist或私有配置。
