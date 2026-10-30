# 本机容器客户端 PAGE/RICH 运输验收

本档归档成功轮次 `authoring-word-transport-20261031-003128`。实际执行为 **2026-10-30 16:31:28.096366–16:31:39.894824 UTC**；目录名使用本机日期时间。PAGE、RICH 均为 `succeeded / complete`，gap 为空；RICH 真实两轮，最终目录数字核对与固定点成立。

## 拓扑与资格边界

客户端为新的 Linux runtime 容器，UID/GID 65532、只读根、network none、IPC none、512 MiB `/tmp` tmpfs、cap drop ALL、no-new-privileges。只读挂载 packages 和合成 fixtures，无 Docker socket、数据库连接或模型调用。Docker 配置及内核实际 `/tmp` tmpfs 均有证据。runtime 标签与本次 inspect 身份仅描述本机候选，不冒充可获取的仓库 manifest digest。

**executor 仍在可信 Windows 宿主的既有 Python 3.12 环境运行**，经固定 Docker exec 管道服务 pin 住 ID/label 的客户端。Gotenberg 8.39.0-libreoffice 使用固定标准仓库摘要，每份 DOCX 独立启动 network none、非 root、只读根的转换容器，原来源、字体、全文、目录、表图、图注和像素资格检查保持。此轮验证两个 lane 的真实远程字节往返，**不表示正式 full Compose 已接入运输层，也不表示 API/Worker/协调器完整业务拓扑均已通过本轮验收**。

## 可核验材料

- [执行摘要](summary.json)、[角色与隔离](roles-isolation.json)、[资源清理](resource-cleanup.json)。宿主处理2请求后正常停止；PAGE 1个、RICH 2个转换容器及客户端均按本轮精确身份清理，receipt 均 closed。
- [PAGE客户端完整结果](client-results/page.json)、[RICH客户端完整结果](client-results/rich.json)：RICH全文、目录值、固定点、表格几何/文字/栅格、图片像素/所属节、图注、对象顺序均 complete，原 gap 与测量字段保留。
- [宿主就绪身份](host/ready.json)、[宿主停止摘要](host/summary.json)、[宿主请求](host/requests/)、[三份转换收据](host/receipts/)、[客户端收据](client-resources.json)。不包含全局容器扫描或本轮之外清理结论。
- 最终 [PAGE DOCX](artifacts/page.docx)、[PAGE PDF](artifacts/page.pdf)、[RICH DOCX](artifacts/rich.docx)、[RICH PDF](artifacts/rich.pdf)，及 [PAGE第1页](artifacts/page-1.png)、[第2页](artifacts/page-2.png)、[RICH第1页](artifacts/rich-1.png)、[第2页](artifacts/rich-2.png)。
- [迭代文件对应](iteration-artifacts.json) 保留每轮DOCX/PDF的大小、SHA与可读取归档文件。唯一首轮文件在 iterations；第二轮与最终文件逐字节相同，直接复用最终 artifact，不复制第二份相同二进制。
- [运行源码冻结摘要](source-correspondence.json)、[provider profile](provider-profile.json)、[profile准备收据](profile-resources.json)、[任务runner快照](snapshots/run_authoring_word_transport.py.txt)、[host快照](snapshots/word_execution_host.py.txt)。两个快照SHA等于本次运行冻结摘要。快照仅为执行证据，**不是产品启动入口**，不得直接据此操作正式环境。

## 主代理实际像素复核与样本范围

主代理实际查看了四张最终PNG：[复核记录](visual-review.json)。本稀疏合成样本的目录、正文、页码可读，中文单元格、表格边框、图及图注清晰，无明显重叠或截断；RICH目录指向2，第二页页码2可见。

这是小型受控PAGE及单节表图RICH样本，不证明任意复杂标书、跨页复杂表格或所有样式无退化，也不证明与旧Word03全字节等价。旧资格与历史执行证据保持各自范围。

## 启动前失败轮次

[失败轮次](preflight-rounds.json)保留准确状态：002538在prepare阶段发生默认沙箱临时目录清理权限错误；002612在prepare阶段发生Docker npipe权限拒绝；002634在host-start阶段因Desktop的Mounts未列出tmpfs而被严格peer检查拒绝。前两轮未创建客户端/转换容器；第三轮零转换且客户端精确移除。随后runner临时目录改至本轮受控工作区，按既有授权使用宿主Docker访问，并仅修正tmpfs表示兼容，同时核对实际内核挂载。这些失败均不能当作转换通过，成功结论只对应003128。

## 公开化处理

JSON宿主工作区绝对路径替换为 `${TASK_ROOT}`；保留容器固定路径、相对来源和非敏感身份摘要。未归档私有.env、Docker config、凭据、Cookie、票据或真实业务原文。[脱敏扫描](sanitization-scan.json)、[归档清单](archive-inventory.json)仅覆盖本档受控材料，不表示扫描或清理了其他私人目录。
