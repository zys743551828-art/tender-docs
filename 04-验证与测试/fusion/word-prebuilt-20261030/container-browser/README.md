# Word 容器化失败、重试与真实浏览器验收

本轮 `word-container-browser-20261031-004309` 通过。主机为 `ZHANGYANSONG`（Windows Docker Desktop）。UTC 2026-10-30 16:43:09.001684 至 16:47:09.701030；主机本地时间 UTC+08:00，2026-10-31 00:43:09.001684 至 00:47:09.701030。

实际浏览器 URL 为 `http://127.0.0.1:5179/bids/bb1221d8-699c-4c63-a87a-e3893fe8c73a/materials`，只在本轮 QA 容器内存在。

同源上传合成 DOCX 后，原客户端在宿主执行器缺席时真实等待 120.053887 秒并记录 WORD_EXECUTION_TIMEOUT。失败页面没有可采用 PDF；启动绑定本轮 Worker ID/run label 的宿主执行器并确认 ready 后，双击重试仅产生一次 POST/202 和一个新 operation。受控转换成功、PDF 自动采用、默认解析自动刷新为 partial；390×844 下载两个文件后与实际 S3 字节相同，刷新、返回项目及浏览器前进/后退没有重复上传。数据库有两次 Word render（首次失败、第二次 qualified 成功），只存在原件和当前 PDF 两个文档版本。

当前 PDF 的 partial 为真实边界：1 页；结构化表格与图页内容尚未核验，本切片仅提取物理页文字。未将其表述为完整检查通过。

API 与 Worker 使用新 py3.12.16 candidate 依赖镜像、只读源码分目录挂载、uid65532、只读 rootfs、cap-drop ALL、无 docker.sock；API 额外使用 16 MiB 私有 .local tmpfs。实际数据库角色分别为 word_container_api 和 word_container_worker，均非 owner/superuser，不能 createdb/createrole/bypassrls。隔离配置与保留的运行时证据范围见 isolation.json。

5 个主容器、1 个 bridge 网络、1 个瞬时 Gotenberg 转换容器均有精确回执且已逐 ID inspect 确认不存在；宿主执行器正常退出 0。见 cleanup-exact-inspect.json。宿主执行器仍是宿主受限编排组件，不是容器化组件；这不是 full Compose Word profile 已安装的证明。

源码 freeze SHA 与 root 提供的冻结值一致；新提交 2db73a29f6a48ef737f1a299d60619c408f9be4d 在本轮期间创建，未把起始 HEAD 冒称该提交。运行时浏览器脚本 SHA 与本地快照一致。详见 frozen-source-snapshot.json、acceptance-source-freeze.json、API/Worker source-profile 和 runtime-browser-source.json。归档及本轮测试未调用 Git。

root 与 local_config_audit 实际查看了失败窄屏、键盘焦点、重试等待、窄屏刷新、桌面 partial 五张截图：友好说明、折叠技术详情、清晰焦点和下载区正常，未见横向溢出或遮挡。重试按钮真实禁用过；等待态不重复展示可用重试。完整键盘/弹窗路径、专用取消 UI（当前不存在）、全业务页面、Kafka、模型及生产部署均未覆盖。

初轮 003238 因 runner 的 tmpfs 遥测 docker cp 读取失败而中断，未启动宿主执行器，其遥测不完整、不是 passed；初轮也已全部清理。保留 initial-incomplete-summary.json，不将本轮结论回填为初轮通过。

归档只保留必要 JSON、脚本快照、五张截图和小型合成 DOCX/PDF，不含运行日志或配置凭据。本轮样本与之前 browser 样本 SHA 不同，故保留本轮字节；见 download-evidence.json。
