# 生产 dist 与官方 nginx 真实浏览器补验

020927 通过，exit0、cleanup_closed=true。Windows 主机 ZHANGYANSONG，UTC 2026-10-30 18:09:27.956035 至 18:10:54.750510；本机 UTC+08:00 为 2026-10-31 02:09:27.956035 至 02:10:54.751006。实际浏览器工具 Chromium 134.0.6998.35（准备阶段实际 headless_shell --version，Playwright1.51.1 默认 headless launch 未覆盖二进制/安全参数）。本轮 URL 为 `http://127.0.0.1:8080/bids/69b2287e-5745-45c2-a623-35562f68b1ec/materials`，位于本轮 QA/web 共享网络命名空间内部，没有新增主机 web 端口。

实际拓扑为六个主容器：PG、S3、API、Worker、独立浏览器工具 QA、官方 nginx web；一个 run-owned bridge；一次瞬时 Gotenberg 转换容器；宿主受限执行器进程。执行文件顶部仍残留旧 five-container timeout/retry docstring，未在活跃测试中改字节；覆盖范围以 PLAN、运行回执和本说明为准。这是六容器成功链补验，既有120s失败/重试证据没有重复跑。

nginx 使用 `nginx:1.30.5-alpine@sha256:8f84ed99befc3891b8f329c5c202785278a2cfb7c25107d57fb2a134a3117433`，实际 nginx -v 为1.30.5，web内 wget index.html 返回 HTTP200。web 将生产 dist 和现有 nginx-fusion-full-main.conf / nginx-fusion-full.conf 只读挂载，使用原配置 api:8000 upstream；API alias=api、监听8000。QA 使用 `--network container:<本轮web完整ID>`，浏览器与 APP_ORIGIN 均为127.0.0.1:8080，无 Vite、额外代理、安全放宽 flag 或 polyfill。

[生产构建证据](../../web-build-20261030/README.md)独立保存了 Node24.22.2 固定镜像构建的成功记录（UTC18:01:40至18:01:55）。未再复制12MiB dist；production-build-reference.json 引用该归档 build-summary.json 的 SHA/inventory。本轮浏览器接收的 index.html SHA 为 ed49ef2a033861e1caae1ee4336eaec114a86682fc0f3269f0f9f8cbf899bf17，与已构建产物相同，模块为 /assets/index-Crckzliw.js；真实 secureContext=true、crypto.randomUUID=function，见 production-http.json。

宿主执行器先通过原严格 ready 校验，再做一次真实同源合成中文 DOCX 上传、原件核验、入库、受控转换、PDF采用、默认解析自动刷新partial；桌面及390×844刷新恢复、原Word/PDF两个按钮真实下载并与S3字节相等，返回项目与浏览器前进/后退没有重新上传。数据库只有一次 Word render（succeeded/qualified）和原件+PDF两个文档版本。

当前 PDF 的真实 parse partial 为1页；结构化表格与图页内容尚未核验，只提取物理页文字。受控转换资格不等于 Word 对应检查或审评通过；页面仍明确提示该边界。Worker 原资格函数 validate_profile/admit/font-gap/inspect_pdf 的轻量只读 sys.setprofile 记录均为 Linux pid1 uid65532；唯一窄协议请求为 convert_docx，仅 source/requested_fonts。宿主元数据只有一个 published 转换请求，不接收业务 snapshot/materials/profile/coverage。

实际 inspect 留证：web/API/Worker 均 uid65532:65532、只读rootfs、非特权、cap-drop ALL、no-new-privileges；各source/config/dist bind只读。web /tmp64MiB，API/Worker /tmp512MiB；API另有.local16MiB uid/gid65532 mode0700。QA仅固定工具，无DB/model配置，namespace严格等于本轮web ID。API/Worker真实数据库角色 word_container_api/word_container_worker 均非owner/superuser，不能createdb/createrole/bypassrls。未请求或归档Config.Env值，见 runtime-isolation-inspect.json、角色和sourceprofile证明。

三轮精确清理已事后独立inspect：020410和020729各6主容器+1网络；020927为6主容器+1网络+1转换容器，共22个原始精确ID全部不存在。每轮receipt均closed，成功宿主执行器正常exit0、processed1。镜像和卷未删除，见 all-rounds-cleanup.json。宿主执行器自身未容器化，本轮不代表full Compose全部服务/Word profile安装完成。

前两轮业务未执行：020410在工具inspect复合断言中止，未保存stderr；020729新增诊断实证原Go模板在QA未配置Tmpfs时读取省略的HostConfig.Tmpfs报错。仅将字段读取改为可读缺省值的index；web/API/Worker仍严格要求/tmp，uid/readonly/capdrop/sourceRO守卫不变。两轮均未启动宿主转换执行器或浏览器业务，不能计作产品失败或pass；必要summary/resources/错误及执行版本SHA保留在 failed-attempts，未复制私有日志。

local_config_audit 实际view桌面partial、390原件下载、390PDF下载和390刷新四PNG；root另实际view桌面选择态、390刷新与桌面partial。主内容未遮挡/横溢，390下两个下载按钮清晰可见、可达且实际点击；实测 scrollWidth=innerWidth=390。桌面fullPage捕获会随滚动使sticky侧栏位置偏移、留白或显示skip-link焦点，这属于截图范围限制，不能据此推断真实页面布局缺陷或未截图状态。未为补证据重启。

仅本新增组件成功链已覆盖。完整键盘/弹窗路径、专用取消UI、其它业务页面、page/rich完整浏览器生成、Kafka、模型和生产部署未测试；此前v2超时/双击单重试见相邻conversion-v2-browser证据。未修改应用源码或Git，执行版runner/mjs按原字节冻结，runtime浏览器SHA与本地快照相同，不宣称当前运行字节属于未核对的提交。
