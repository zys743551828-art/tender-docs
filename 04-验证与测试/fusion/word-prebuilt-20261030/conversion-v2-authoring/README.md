# v2：Linux Worker 内生产、准入和资格检查

成功轮次 `authoring-word-conversion-v2-20261031-012639`，**2026-10-30 17:26:39.197848–17:26:55.393364 UTC**。目录名使用本机日期时间。PAGE、RICH 均 `succeeded / complete`，gap为空；RICH两轮固定点和最终目录数字核对成立。协议为 `word-conversion-execution/2`。

## 本轮实际分工

Linux客户端实际调用 `prebuilt_dispatch.generated/rich`，PAGE在客户端重新生成DOCX/manifest，RICH在客户端生成并验证每轮DOCX。来源准入、profile/source proof、字体替代/字形判gap、原PDF全文及表图图注检查、目录循环和最终资格均留在UID65532的Linux进程。客户端保持只读根、network none、IPC none、512 MiB tmpfs、cap drop ALL和no-new-privileges，无Docker socket；只读挂载packages、合成fixtures和一个纯deploy helper源文件。

可信Windows宿主executor仅收到3个 `source + requested_fonts` 原始转换请求。host没有收到snapshot、materials、profile或iterations，也不负责authoring/PDF资格。每份DOCX独立启动固定标准仓库摘要的Gotenberg 8.39.0-libreoffice，原network none、非root、只读根和资源上限保持。宿主正常停止，PAGE1份及RICH2份转换收据、客户端收据均closed。

这仍是本机独立受控业务函数验证。**原full/1部署入口和peer尚未装配本运输方式**，本档不授予完整API/Kafka/数据库生产拓扑资格，也不证明任意复杂标书、跨页复杂表格或与旧Word03全字节等价。旧v1档案保持原范围，不被本轮覆盖。

## 执行位置与窄请求证据

- [执行摘要](summary.json)、[角色隔离](roles-isolation.json)、[精确清理](resource-cleanup.json)。runtime镜像记录的是本机标签及实际inspect身份，不冒充可获取的仓库manifest digest。
- [PAGE Worker trace](worker/page-execution-locations.json)：实际PID25、UID65532；[RICH Worker trace](worker/rich-execution-locations.json)：实际PID97、UID65532；均CPython3.12.16。trace记录实际filename、函数、源码SHA、PID/UID及call事件计数。只读`sys.setprofile`未替换函数或成功返回；contextmanager等恢复也可能触发call，转换次数以请求和收据核对。
- [host trace](host/execution-locations.json)：实际_dispatch、convert_in_isolation、_font_facts各3次；authoring生产、准入、字体判gap及PDF检查均无host调用记录。
- [三请求元数据](host/narrow-request-metadata.json)逐一对应Worker输入、DOCX字节大小/SHA和请求字体；[原host请求记录](host/requests/)保留operation、request bytes、发布及清理状态。没有复制RPC原始文档payload。字体原事实确由Worker的原判定函数消费并通过，元数据保留实际trace和无字体gap结果；**fc-match返回的families/charset原始字符串没有另存，本档不补造**。
- [host就绪](host/ready.json)、[host停止](host/summary.json)、[三转换收据](host/receipts/)、[客户端收据](client-resources.json)。所有资源身份均属于本轮，无全局扫描或清理声明。

## 内容、版式与源字节

- [PAGE完整结果](client-results/page.json)、[RICH完整结果](client-results/rich.json)。原全文、目录数值/固定点、表格几何/文字/栅格、图像像素/所属节、图注及对象顺序检查均保留，最终complete、gap为空。
- 最终 [PAGE DOCX](artifacts/page.docx)、[PDF](artifacts/page.pdf)、[RICH DOCX](artifacts/rich.docx)、[PDF](artifacts/rich.pdf)，及 [PAGE第1页](artifacts/page-1.png)、[第2页](artifacts/page-2.png)、[RICH第1页](artifacts/rich-1.png)、[第2页](artifacts/rich-2.png)。主代理已实际查看本轮四PNG并通过本稀疏受控样本的[像素复核](visual-review.json)：目录/正文页码正常，RICH中文表格边框、绿色图及图注无明显截断或重叠，目录到2与第二页页码2相符。该复核不扩展为任意复杂标书或跨页复杂表格资格。
- [迭代文件](iteration-artifacts.json)保留两轮完整对应；首轮独有DOCX/PDF在iterations，第二轮精确复用最终artifact，未省略独有字节或迭代证明。
- [实际源码冻结](source-correspondence.json)、[新provider profile](provider-profile.json)、[profile准备收据](profile-resources.json)、[任务runner快照](snapshots/run_authoring_word_transport.py.txt)、[host快照](snapshots/word_execution_host.py.txt)。profile按归一LF后的新源码重新建立，两个快照SHA等于本轮冻结；只报告实际源字节，不声称旧HEAD等于新实现。快照是证据，**不是产品启动入口**，保留实际执行字节，不作换行归一。

## 首次失败及恢复

[012555失败记录](failed-round.json)：PAGE客户端import时缺少`deploy.disposable_docker`，host处理0请求、0转换并正常停止，客户端已精确移除。随后仅在task runner加入该一个纯helper文件的只读挂载，未更改产品源码、原断言或隔离配置，再以fresh profile运行012639通过。失败轮次不当作成功资格。

## 公开化处理

JSON宿主工作区绝对路径统一替换为 `${TASK_ROOT}`，保留容器固定路径、相对源码和非敏感身份。只归档受控合成材料，无私有.env、Docker config、凭据、Cookie、票据或真实业务原文。[脱敏扫描](sanitization-scan.json)覆盖本档文本、DOCX XML、PDF文字/metadata和PNG metadata；[文件清单](archive-inventory.json)核对归档字节，不扫描私人目录。
