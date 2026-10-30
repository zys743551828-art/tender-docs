# Word 当前状态与窄屏浏览器补验

在用户电脑 ZHANGYANSONG，2026-10-30 23:43:39–23:46:20（UTC 15:43:39–15:46:20）执行。应用源码沿用 `d0fb75ad4b6f55ad44b8ec35c93d19425f821d09`，未因本轮重复运行默认套件。

交互通过：真实同源上传合成 DOCX、原件核验、受控转换、协调采用当前 PDF、下载原件及 PDF 并核对私有存储字节；解析 API 与页面均显示 partial；桌面刷新恢复该状态；390×844 窄屏真实下载和刷新恢复。API、Worker 实际连接角色均非数据库 owner，且无 superuser/createdb/createrole/bypassrls。

视觉通过：实际查看 `word-desktop-parse-partial.png`、`word-390-pdf-download-viewport.png`、`word-390-refresh-viewport.png`。桌面材料列表和 Word 状态区域、窄屏下载区域未见文本遮挡或横向溢出；窄屏 document scrollWidth 与 innerWidth 均为 390，两个下载按钮实际可点击。

边界：本轮未覆盖转换失败后重试、所有弹窗的键盘焦点或全部业务页面；没有调用模型、运行 Kafka、验证原 full Compose 的 Word 安装或证明真实标书合格。转换 complete 与默认解析 partial、Word 交付资格未取得分别显示。截图为合成数据。

资源回收：一个临时网络、三个主测试容器及一个转换容器均按精确 ID 复核不存在，Worker 正常退出，无强制终止回退。清理明细和实际执行时间见 `summary.json`。源码与浏览器运行器身份核对见 `source-profile-match.json` 和 `acceptance-source-freeze.json`；原完整运行器快照保留在本机对应执行目录，上一轮入口说明见[桌面主链](../browser/README.md)。
