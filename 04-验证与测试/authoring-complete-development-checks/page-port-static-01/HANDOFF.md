# 安全PAGE独立生成端口：具体代码静审包01

本包只有静态代码与20个纯机制测试，**新的PAGE转换端口尚未执行**。需架构审阅后，PM再安排实际profile初始化/真实Docker/PG发布/fence验收。不覆盖首片01 f6fd6390…，也不修改外来Word03检查源或宣称RICH已实现。纯测试在5 Owner+14依赖共19份原字节固定根运行，零DB/模型/对象/Kafka/容器I/O。

## 实际端口

- `generated_docx.assemble(snapshot) -> bytes`：从完整原core冻结投影生成严格文字DOCX，只有请求page_number时加入页脚规范 `w:fldSimple w:instr=" PAGE "` 与缓存1；无instrText/fldChar/嵌套/开关。输入16MiB、产物64MiB限额。`generator_identity()`固定生产器/内容与模板/结构校验源码摘要。
- `generated_docx.producer_manifest(source,snapshot)`：固定用途bid_body、snapshot ID/digest、完整sections digest、结构digest、实际docx SHA与生产器摘要；它是路径记录，不单独授信。
- `generated_verification.verify(source,snapshot,manifest,generator_digest)`：先限类型/大小及精确生产器身份，再根据冻结snapshot重新生成并逐字节比较，不能靠调用者重算manifest使任意上传字节变可信；之后完整ZIP/内容声明/所有XML/关系闭包。ZIP仅6基础+header/footer两个部件，压缩/展开/每项/重复/符号链接限制。XML UTF8/节点/类型/元素/属性白名单，拒DTD/实体/处理指令/未知关系或部件。
- `verify_container`另验全包：唯一定量动态域只可在footer的直接段落位置，instr字面完全为 ` PAGE `，run/t缓存结构精确；其余域标签/命名空间/隐藏part均拒。此独立闭包不替代重新生成证明。外来`inspection.inspect_docx`对同一生成带PAGE字节仍conversion_eligible=false，外来端口未被放宽。
- `generated_renderer.profile_for(base_word03_profile)`：派生独立 `authoring-generated-sandbox/1`，固定基础完整profile、三个已审Word03源码SHA（renderer/inspection/runner）、本adapter/producer/validator摘要和对应策略。不接受HTTP callable/trusted布尔/路径/任意镜像；没有DB或模型连接参数。
- `generated_renderer.prepare(source,snapshot,manifest,profile)`：纯身份+来源+语法准入，任何异常在Docker前失败。
- `generated_renderer.render(source,snapshot,manifest,profile)`：prepare后才通过已审Word03 `build_profile`复核基础环境；复用其精确 `_arguments/_inspect_isolation/_read_process/_host_environment/_font_gaps`与Docker低层helper，不复制/放宽mount/user/network/image/resource规则。每job唯一名、输入只读、超时、大小限额、相同清理验证。无宿主soffice路径。基础helper变更立即拒，后续合法升级需新候选复审。

## 对应核验的实际实现边界

`inspect_pdf`用PDFium物理页、A4/rotation和逐字符bbox，把固定页眉/页脚区域与主体分开；每页页脚必须精确匹配冻结footer_text+1..N，页眉精确匹配冻结header_text。正文数字不做全页替换/剔除，完整顺序文字和原生列表编号也计入预期正文。低边距导致区域可能重叠、缺字/字体、额外或缺失文字、未刷新页码、超页上限均partial/failed。不因转换返回0升级complete。

本代码有几何/文字核验函数，但当前20纯测试未证明真实PDF几何效果；独立静审和后续真实1/多页/长页眉页脚/正文同值数字/列表/缺字体/超时/页码未刷新/cleanup/fence均待做。章节具体页范围继续unknown；没有靠重复章名猜页。PAGE成功也不自动授格式/暗标/正式评分或完整A2资格。

## 必须保留的基线与反例

- Word03候选 b12e2e456ead217296510ae6fde3f7747198d75adffe42706bb3692c28406fa6 与public profile原字节在external-inputs；静审不会允许执行原Word01/02。
- 首片01静态8部件DOCX在Word03实际失败，原因是OPC关系的prefix序列化互操作；此包structured_docx加入无全局namespace状态的默认namespace序列化。原失败、命名空间最小对照与新静态writer真实3页PDF（保留原header/footer partial缺口）在../candidate-02-work，属于**原Word03通用端口**，不是此新PAGE端口运行通过。
- 原首片01全部字节与56/3证据保持不动；本包不替它补写PDF通过结论。

后续编制build接线仍由Owner在审后另候选完成：API冻结独立profile/生成器身份→Worker关读事务后本端口→发布同原fence与完整来源/上下文最终守卫。main/engine/exporter及Worker新业务表权限本包均未改。RICH表图/Word内容导入不在PAGE白名单中，继续拒绝，须另版本扩展真实能力。
