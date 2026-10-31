# Desktop full/2 工程交付核查

本轮在授权的 Windows 电脑与 Docker Linux/amd64 引擎执行。三条正常业务路径与本轮完整生命周期矩阵均已通过。应用提交为 `6446ba0ad98beb46a0c3942295215aa59dbc5df5`；[CI41174917023](https://github.com/zys743551828-art/tender-app/actions/runs/41174917023) 11/11 项全部成功。本页不代替正式业务、模型质量或生产验收。

## 当前覆盖

| 路径 | 已取得的真实结果 | 边界 |
|---|---|---|
| 普通常规安装 | 新项目 venv、标准依赖镜像、prepare/up、指定合成口令 HTTP200、status、verify 成功 | 独立测试实例；未连接原业务库 |
| 外部 Word | 上传、受控转换、采用、Kafka 解析刷新、真实原页、原件/PDF 下载通过 | 解析仍显示 partial；Word 交付资格未因此授予 |
| 人工 PAGE | 普通 API 的 DOCX/PDF 均 succeeded | 保留 TOC_PAGE_NUMBERS_UNKNOWN、ACTUAL_SECTION_PAGE_RANGES_UNKNOWN |
| 人工 RICH | 普通 API 的 DOCX/PDF 均 succeeded；navigation_only 两页、两轮固定点、无导航 gap | 不代表任意复杂标书的全部版式通过 |
| 桌面与 390px | 实际生产页面截图、原页 canvas、刷新恢复及下载字节通过 | 主审已查看当前原页与窄屏刷新像素；窄屏不替代桌面最终内容核对 |
| 焦点与慢速预览取消 | 既有本机证据复用，见[焦点/取消](../word-prebuilt-20261030/focus-cancel/README.md) | headers 后取消已观察；未证明部分正文已传输或 Word 引擎立即取消 |
| 生命周期 | 普通 stop/up、单执行器故障恢复、拒绝自动重绑定及显式重绑定全部通过 | 普通重启独立通过；未测试非正常断电或强杀恢复 |

使用固定官方 Gotenberg 8.39.0-libreoffice，不自建 Word 转换镜像。应用依赖镜像使用 Python3.12.16、pypdfium2 5.14.0；宿主新 venv 使用已有 Python3.12.14。安装和构建命令集中见[使用说明](../../../使用说明.md)。外部模型标识属于用户可选配置，当前仅 DeepSeek 实现；本轮没有付费模型调用。

## 修复与回归

- [基础有限回归](regression/README.md)：18 模块 527 通过。随后改变的模块分别做定向复验，不能把历史源码记录改写为最终源码全量验收，也不能把重叠用例数相加。
- [Desktop 只读源码映射](desktop-bind-regression/README.md)、[Windows 文件时间戳](binding-ctime-regression/README.md)：真实安装反例与保留的失败记录。
- [渲染器结果身份与退出竞态](renderer-chain-stop-regression/)、[PAGE 路由与 mailbox](page-mailbox-dispatch-regression/README.md)：保留数据库方法身份约束、源验证和版式断言，修复生产接线。
- [安全存储诊断](safe-store-diagnostics/README.md)：仅增加已知错误码、HTTP 状态和操作名，原验证及权限不变；此前一次 ClientError 的细分原因尚未证明。
- [结果缺失提示](missing-result-ui/README.md)：中性文案、定向测试、固定 Node24.22.2 构建及真实浏览器回放。回放不是业务 E2E，真实主链另记。

TEST-DESKTOP-WORD-BOOTSTRAP 对应角色隔离、明确模型声明与私密 stdin 输入；TEST-DESKTOP-WORD-LIFECYCLE 对应两个绑定执行器、状态与显式恢复；TEST-WORD-PAGE-DISPATCH 对应合法 PAGE 生产者协议路由及冻结声明匹配。TEST-WORD-PREBUILT、TEST-WORD-EXECUTION-HOST 和 TEST-DESKTOP-VERIFY 延续各自原范围。FR-010、FR-037、FR-039 的 authoring 目录变动是 PAGE 接线及 PDFium API 兼容；FR-032 的 deploy 目录指纹随安装配套更新，备份/恢复 SQL 和生产恢复准入没有修改。

依赖固定采用生态锁文件、明确版本与部署镜像 digest。现有业务来源、转换资格和证据摘要继续承担来源与数据一致性校验，不另建逐依赖摘要登记框架。旧 Word03 记录保留原身份，新输出不冒充旧字节基线。

## 收尾状态

本轮实例已精确回收：16 个容器、2 个网络、14 个测试卷；登记的 16 个宿主进程身份全部退出，固定镜像保留。详见[普通安装与生命周期记录](installation-lifecycle/scope-result-summary.json)和[真实 Word 浏览器/API 验证](normal-word-browser/README.md)。早期失败保留其原结论；历史一次 S3 ClientError 在最终矩阵中未复现，不能倒推其原因为何。

已移除 774 个重复历史前端源码和 2 个可重建缓存，保留当前 24 个规范输入（其中 11 个 TS 模块参与构建）与必要验收记录；源历史可从固定 Git 提交恢复。未删除用户未跟踪数据、原工作区、私有配置、镜像或业务卷。应用 CI 11/11 全部成功，见[终态回执](ci/summary.json)；文档版本以本仓 `git rev-parse HEAD` 为准。
