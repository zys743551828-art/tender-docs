# A2 可信生成页码与真实构建候选02

这是树/计划/双模板第一片之后的独立增量，不是完整A2或生产发布通过。候选01 f6fd6390…、PAGE静审包 ddd11c50…、Word03 b12e2e45…及全部首失败原样保留。

## 改动与接线

- 修正静态DOCX OPC Content_Types/relationships 的默认命名空间写法；真实Word03已复现旧包失败，并用独立最小对照确认新包可转换。无全局ElementTree命名空间状态。
- 新 generated_docx / generated_verification / generated_renderer 为单独的 PAGE-only 可信生成通道。完整冻结快照重生成逐字节比较，再全包关闭语法检查；只允许规范单页脚PAGE。外来Word03及其三文件不改，外来PAGE仍拒。
- 双模板BuildRequest仍是候选01同一接口，服务端冻结 generated_text/2 与生成器/验证器身份。API在事务外读取 installed profile，Worker prepare后关闭连接再实际Docker转换，finish按真实fence和来源最终守卫发布。原人工保存、快照、旧strict_text/1和快速上传PDF保留。
- 私有环境 AUTHORING_GENERATED_RENDER_PROFILE_PATH 指向 generated_renderer.profile_for(packages.word_intake.renderer.build_profile()) 的精确JSON。生成配置须从将运行的固定根执行，绑定实际runner挂载路径；更换根需重新生成profile，不能简单覆盖摘要。公共runner需0444，私有profile仍0600。
- 无新增DDL、共享main/engine/exporter改动。现有export/1 handler直接调用同一compute/publish端口；新增结构router和契约的接线保持候选01所列。actual.openapi.json形状与01逐字节相同，仅为集成预览，不冒API8806已部署。

## 真实证据

- 架构已静审的PAGE同字节代码：实际本地Docker/LibreOffice刷新7页中文、封面/静态目录/页眉/页脚，物理页码1..7与全部正文数字摘要匹配；原生列表、单页、另一中文字体正向。缺字符和低边距为partial，显式极短读取时限注入的实际timeout为failed且自有容器清理确认。timeout仅验证失败清理，不代表容量。
- 完整回归80 passed /3 skipped；20 PAGE纯规则、原26纯、原30PG/ASGI加4新真实PG/engine/Docker用例。3skip为宿主LO旧测试及未安装旧通道profile的两种旧follow_up正向，不计通过。ruff/format、23源mypy、Bandit通过（旧B603 nosec无匹配警告保留）。
- 新四项明确memory对象端口，并硬阻requests/httpx真实HTTP、模型from_env/key；仍真实PG、ASGI和批准Docker。真实engine compute时连接已关闭，取消迟到不发布，重复消息不再次领取，后续编辑使旧构建仅留历史，来源撤回使finish无outputs/effect。
- 原回归中的合成OSS构建仍按既有授权实际PUT/GET自己的新合成字节；不扫描桶/不改配置/不删已登记历史。新四项不是OSS验收；没有Kafka、收费模型或消费者。

## 首失败与范围

原PAGE runner0600导致非root读取失败，原结果及profile/脚本保留；只修公共文件可读metadata，19源SHA不变，重新无包装实际调用才成功。先前诊断脚本对两个None做相等的body_digest_match观察不计对应通过，正式clean-result含真实摘要。

首PG第三项因内存夹具漏storage.client发生精确自有合成键OSS GET404，不是产品时序失败；旧JUnit与输入字节/随后错误cwd导致的中断重跑均保留，详fixture-failure-record。正式修夹具保留真正PDF字节核验，不能用Mock授业务来源资格。

页脚PAGE通过不意味着目录各章页码已知。仍明确TOC_PAGE_NUMBERS_UNKNOWN和ACTUAL_SECTION_PAGE_RANGES_UNKNOWN；计划页数不当实际页数。表图实际内容、可视预览、选区AI与Word内容导入为后续切片，未承诺完成；无真实模型质量/前端/Kafka/企业文档保真结论。
