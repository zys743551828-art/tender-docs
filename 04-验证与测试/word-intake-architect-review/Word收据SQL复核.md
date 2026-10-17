# Word收据SQL复核

结论：SQL02及本轮数据库机制范围内通过，可继续受控Word端口开发；不授予真实renderer、controlled_render或完整Word业务路径通过。最终[SQL02](observed-schema02.sql) SHA256 `380a58eef00224a6094652c076d0d3ff0cb268a30a9868544671f4d37eb1dbfe`。SQL01 `6ec9e40d…`的原件与反例保留；架构师没有执行任何新DDL。

## 已整改的两项静态缺陷

SQL01把render_runs全列INSERT授API，能在创建时填完成结果；‘无UPDATE结果权’不等于不能铸造完成收据。SQL02仅授冻结输入列INSERT，结果只能由Worker完成。

SQL01的JSON state=null可使shape、成功↔PDF及complete条件均为SQL NULL，CHECK接受NULL，成功trigger分支亦跳过。合成`state:null,coverage:complete,gaps:[],chain:{},conversion_attempted:false`在真实PG READ ONLY标量表达式中复现上述NULL；SQL02强类型/coalesce及成功必须attempted=true使shape=false。证据[标量反例](json-null-counterexample-result.json)，不是执行旧Word DDL后的插入测试。

## 独立新库权限与约束机制

PM按精确SQL02新建word_intake01后，架构师以真实非owner API/Worker、自有隔离项目执行：API伪造完成INSERT42501；合法冻结输入可插；Worker JSON-null、成功但未attempted、complete带gap、错输出SHA均23514；合法partial收据可保存；终态改写55000；API结果UPDATE42501。仅一条partial结果保留。见[schema02-permissions-result.json](schema02-permissions-result.json)和[探针](probe_schema02_permissions.py)。

夹具是明确合成SQL上传/原件/blob记录，没有对象字节、实际DOCX上传、renderer调用、engine fence或effect发布，不能当真实转换证据。初次自有夹具漏填基础verified_retain_until，被原保留约束拒绝，整笔setup回滚；只补夹具后重跑，未改产品SQL，诊断另存。

## 原件与转换的实现放行条件

复用既有document_files/versions/blobs/relations，不另造文档库。Word receipt应绑定真实verified upload/DOCX媒体及精确digest；补PDF声明同bid同角色，仍为user_declared_same_draft/unverified，不能因同名或同项目证明同稿。DOCX不能进入PDF解析、current_body或冒充新稿已完成核对；旧PDF结论不能被显示成新Word结论。

真正controlled_render还须冻结renderer/font/options/依赖与隔离profile、源DOCX及产物PDF两端真实SHA，实际成功＋完整coverage＋无gaps才可声明完整受控转换；partial留档但不授完整资格。发布必须在当前operation fence/effect事务核冻结source/profile，SQL FK同bid并不能证明具体handler任务身份。取消/失联/迟到、损坏PDF、字体/版式缺口、主稿CAS及所有single/multipart/rebind路径还待实际冻结源复核。

Owner已说明renderer真成功探针，但本轮尚未审最终renderer源码/inspect运行证据；不能把其moving代码或开发探针当独立隔离证明。预检还需全包XML字节/节点总预算；Owner称已补24MiB/300k，仍须最终源及反例验证。0收费/0OSS/0消费者。
