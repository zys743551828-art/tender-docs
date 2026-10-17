# 可信生成正文的页码与受控转换增量（草案01，尚未实现/验收）

此增量接在 TREE/PLAN/双模板文本切片之后。外来 Word 继续拒绝所有动态域，不能因正文生成能力放宽上传转换边界。候选01静态目录与缺失页码的事实保持。

## 端口与责任

- authoring Owner：新纯生成器 structured_text/2；从完整冻结 snapshot 生成确定 OOXML，页脚仅可生成规范 PAGE 域；提供独立纯校验/来源证明模块与反例。正文、目录、模板、字体配置及所有 source 引用仍按既有构建 manifest/CAS/发布守卫。
- parsing Owner：独立生成正文转换端口及独立 profile；现有 `render(external_word,profile)` 不接受例外。具体签名待其认可，可为 `render_generated(source,profile,producer_manifest,frozen_snapshot)`。固定白名单生产器，不接受请求传 callable、任意端口或“trusted=true”。
- 架构：先审来源证明、完整容器语法与域准入具体字节，再执行转换验证；不是协议获批即安全实现通过。PM组合 main/Worker/profile，禁止一般消费者/真实收费。

## 来源证明和完整容器守卫

1. API 冻结 build input：真实 draft_snapshot ID/digest、双模板/有效格式配置、schema 与 writer 源摘要，不能接收客户端上传的 DOCX 或可信标签。Worker在关读连接后生成；renderer 不读取业务DB或接收DSN/原件URL。
2. `producer_manifest` 固定用途 bid_body、build input ID、完整 snapshot digest、实际 DOCX SHA、生产器协议/源摘要。不能仅凭这些可复制文本认定可信：生成正文端口必须以受信白名单中的确定性生产器和冻结 snapshot **重新生成并逐字节比较 source**，且核该生产器/配置摘要与 profile 白名单完全一致。来源证明是服务端构建路径记录，不替代语法检查。
3. 必须完整检查 ZIP 清单、压缩/解压总量、每项大小、重名/路径/符号链接、所有关系和内容类型。只接受生产器可生成的固定 XML 部件；无宏、OLE、嵌入对象、altChunk、外链、数据绑定、DDE/LINK/INCLUDE*、任意样式域、删除/变更域。不得只扫描 document.xml 或单个 instrText run。
4. 唯一动态域为固定页脚位置的 `w:fldSimple`，instr 经严格规范后仅可为 PAGE，无开关、子域、instrText/fldChar/fldData/delInstrText；子结构仅固定格式 run+缓存十进制文本。所有 XML 部件中其他域标签均拒绝；拆run/嵌套/零宽字符/属性混淆/大小写边界均需负例。语法验证失败不得尝试 LibreOffice。
5. 外来 Word 即使内容仿照 manifest、文件名或正文命名空间仍走外来端口；不提供HTTP调用生成正文转换端口。只有 build compute 的固定 handler 路由可进入该生产器分支；通过 API 新建草稿再生成是明确的新正文工作，并非给原上传字节升格。

## 沙箱、结果和对应资格

- 复用 renderer 的固定 Docker镜像/挂载/字体/非root/无网/只读根/资源限额/超时/独立工作目录/清理；新 profile 固定生产器与校验器源摘要以及 PAGE准入协议，不复用外来 Word 的验收结论。
- 首先验证实际 DOCX ZIP含规范 PAGE 与选定字体/字号/边距/页眉页脚，转换实际发生；PDFium实测页数及每页页脚数字。数字应在按冻结纸张/边距推导的页脚区域，逐页为1..N；不能全页删同值数字后假称正文一致。正文中自身的数字必须保留。
- 逐页可核的固定页眉/页脚区域分别验证；主体正常化文字覆盖仍核完整冻结正文、静态目录。超范围、重叠、缺字/缺字体、额外文本、字段未刷新、转换后检查失败保留真实产物及partial/failed/gaps，不能升级Word↔PDF complete/verified。
- PAGE成功只证明页码与转换对应，不能自动确认格式条款、暗标、事实或正式评分。总页数实测，与计划页预算分别展示。
- 章节实际页范围仍需可还原身份（例如已验证导出的 section UUID 书签/目的地与页坐标）才能声明。重复章名、目录中的同名文本不可用于猜页；在可靠映射实现前继续null/unknown，不因页码通过而伪造范围。

## 可重复机制验收

需覆盖真实受限PG冻结→关TX生成→受控转换→发布/最终来源重核；模板/来源/事实漂移、超时/缺字体/字段未更新、失效fence/取消/迟到均保留既有守卫。对不同页面数量、正文中相同数字、重复章名与空段落做真实DOCX/PDF。明确禁止付费模型和外部发布。候选源码/依赖/profile摘要/实际字节与反例分别冻结；任何原Word02/03安全修复不因本草案被提前计入通过。
