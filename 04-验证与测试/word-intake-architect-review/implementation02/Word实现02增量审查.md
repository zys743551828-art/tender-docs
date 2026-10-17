# Word实现增量审查

结论：W01原命名绕过已闭合；候选02仍有W03动态域分段漏检，不能放行转换组件。精确候选SHA256为 `5dcc4dfad4ee05df0ae326c9bb1f64eff9faaae2abd41e4aa40bb4717e7cb2db`，12份冻结源已逐字核对。SQL380…和原01证据没有改动。

独立使用原01三个DOCX原字节复验，header1.xml／header1.XML／header1.dat现在均识别EXTERNAL_OR_DYNAMIC_FIELD并禁止转换。新实现通过Content_Types Default／Override发现XML，同时对可疑后缀与XML外观保守扫描，统一包级预算及关系类型核验。retry路由也已在Command旧ACK之前读取真实bid／Word版本范围，修复静态观察到的scope顺序；其真实跨bid202→404开发证据未冒称本轮独立HTTP实测。

**W03（阻塞转换准入）：复杂域的指令可以跨run／instrText分段，逐节点正则无法代表完整域。** inspection.py:338—341仅检查各instrText.text。独立构造真实begin／多个instrText／end结构：单节点DDEAUTO被拒，而“DD”＋“EAUTO”、以及“INCLUDE”＋“TEXT”被标conversion_eligible=true且active_content_gaps为空。字段代码的语义是连续拼接的完整指令，不因run边界改变。现有fldChar／instrText复杂范围标记会令正常结果partial，但那发生在准入之后，不能履行“动态域不交转换器”的明确策略。

建议本首切片保守禁止全部复杂域，或按begin／separate／end严格重建每个完整字段后以明确白名单判断；嵌套、未闭合、跨段、未知或无法证明安全的域不能放行。普通PAGE／TOC如要支持，应作为具体语法与缓存结果的单独范围验收，不能依赖几个危险关键字的逐节点黑名单。验收需同一指令任意run切分的变形测试，以及合法普通文本对照。

fragmented-field-counterexample.json、probe_field_runs.py和3份合成原件保留此反例。没有执行任何字段、Docker或外部网络；不声称已发生代码执行或容器逃逸。已通知Owner与PM，02原件保持冻结。

独立重跑33项纯检查通过。首次执行因冻结12文件不含测试相对路径读取的公开base OpenAPI资源，出现32通过／1夹具FileNotFoundError；原XML保留。随后只在架构师自有执行目录复制同SHA测试和精确公开schema，33项全过，附public-schema-auxiliary.json；没有修改被审生产源或其冻结目录。原三W01字节复验另行记录，不混算新增测试数。

renderer和runner的静态复核确认固定本地daemon／镜像、显式清理宿主环境、非root／network-none／只读绑定和受限tmpfs等约束方向；因W03阻塞，本轮未启动真实转换或独立重跑缺字体／缺字／超时作业，也未连接PG。新profile d700…不能继承dev04预检身份，但其真实运行尚未由本轮授予通过。Worker fencing／取消、主Word桥接、采用／解析／RAG、浏览器和生产隔离／容量继续待验。
