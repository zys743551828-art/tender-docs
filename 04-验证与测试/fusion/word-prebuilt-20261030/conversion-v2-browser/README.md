# v2 窄转换协议真实浏览器验收

`word-conversion-v2-browser-20261031-012527` 通过，exit 0，cleanup_closed=true。本轮在 Windows 主机 ZHANGYANSONG 的 Docker Desktop 执行：UTC 2026-10-30 17:25:27.320989 至 17:29:28.137924；主机本地 UTC+08:00 为 2026-10-31 01:25:27.320989 至 01:29:28.137924。实际页面 URL 为 `http://127.0.0.1:5179/bids/32b6f185-657b-4dbb-8eb0-2c58be4eff07/materials`，只在本轮 QA 容器内部存在。

同源上传简单合成中文 DOCX，宿主执行器缺席时原客户端真实等待 120.064728 秒，原 WORD_EXECUTION_TIMEOUT 传播到实际失败 step receipt。页面显示失败、可重试、没有可采用 PDF。宿主 ready 后真实双击重试只产生一次 POST/202 和一个新 operation；转换成功、PDF 采用、当前 PDF 默认解析自动刷新 partial；桌面和 390×844 刷新恢复、两次实际下载对 S3 字节一致，项目返回及浏览器前进后退未重复上传。最终只有两个文档版本及两次 Word render（一次失败、一次 qualified 成功）。

默认解析的真实 partial 为 1 页，结构化表格与图页内容尚未核验，仅提取物理页文字，不等于完整检查或 Word 审评通过。

v2 边界证据：Worker 内轻量 sys.setprofile 只观察原函数调用，不替换函数、不改返回值、不保存参数值。worker-qualification.json 记录 validate_profile、render.<locals>.admit、_font_gaps_from_facts、inspect_pdf 均在 Linux Worker pid=1、uid=65532 运行，observer_errors 为空。两次生成的请求均为 word-conversion-execution/2、convert_docx，arguments 只有 requested_fonts/source，没有 snapshot/materials/profile/coverage。host-executor-report/requests 只有一次真实 published convert_docx 元数据；宿主按冻结实现只执行隔离转换，不执行上述业务资格函数。

API 和 Worker 均使用既有 py3.12.16 candidate 镜像。runtime-isolation-inspect.json 来自运行中两精确 ID 的实际 inspect：uid65532:65532、只读 rootfs、非特权、cap-drop ALL、no-new-privileges、/tmp512 MiB、分目录源码 bind 只读，API 另有 .local16 MiB uid/gid65532 mode0700 tmpfs，无 docker.sock。实际数据库角色 word_container_api/word_container_worker 均非 owner/superuser，不能 createdb/createrole/bypassrls。QA 和宿主执行器未传 DB/model 凭据。

主 run 1df5198fc2ad4887a335bc330133bdc4 的 5 容器、1 bridge 网络，以及转换 run 3cd350c5ee7d4973b6e929f3eab4589d 的 1 Gotenberg 容器，回执全部 removed，且事后逐精确 ID inspect 均不存在。宿主执行器正常 exit0、processed1，正式 stop/recover helper 收尾，见 cleanup-exact-inspect.json。宿主编排组件本身未容器化；本轮不代表 full Compose Word profile 已安装。

应用产品源先按仓库规范归一 LF，再核对 frozen-inputs.json 中 host/codec/prebuilt SHA。fresh profile 的 adapter digest 为 be44fcf07b5efee8482f4b3893a3bf99666ae296853c89bda145c90e198c1924，profile digest 为 de7fc0903d2cd85678f1c5705af8249e36bef064d5d137784db13ba57b9b1747。API/Worker 内实际读取的源码字节和 profile 均匹配。运行开始时是未提交新版本，不声称运行字节等于旧 HEAD 2db；实际字节证据与最终提交由 root 对照。浏览器运行脚本 SHA 与本地快照一致。此归档及运行未调用 Git，未改应用源码。

local_config_audit 实际查看了归档五 PNG：failure390、keyboard focus390、retry loading390、refresh390、desktop partial。友好说明与折叠详情清楚，重试焦点可见，等待态不重复展示可用重试，下载按钮可见；未见窄屏水平溢出或遮挡。390 实测 scrollWidth=innerWidth=390。完整键盘/弹窗路径、专用 Word 取消 UI（不存在）、其它业务页面、page/rich 的完整真实浏览器生成链、Kafka、模型、生产部署不在此轮覆盖范围。

之前 012258 在 tooling 阶段收到源码 LF 归一暂停指令：只停止带本轮所有权标签的唯一 QA，让宿主 runner 正常 finally。1QA+1N 全清理，没有创建 API/Worker/PG/S3、profile 或转换器。它属于 operator 中止，不计产品失败或 pass，另留 initial-tooling-* 记录。旧 004309 成功证据保持原样。

本目录只留必要 JSON、源码快照、五 PNG 和本轮小型合成 DOCX/PDF，不含全量日志、env 或凭据；样本 SHA/字节数见 download-evidence.json。
