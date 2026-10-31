# Word 缺结果文案修正与 UI 回放

仅修改 WordOriginals.tsx 中一条提示，并在既有 missing-result 测试中追加旧断言不出现及中性提示可见断言。缺少结果不能推断转换未实际执行；原资格、API、交互与布局逻辑未修改。

4 项定向测试、ESLint、Prettier 均通过。标准 deploy/Dockerfile.web-build 使用固定 Node 24.22.2 构建成功，独立产物在 dist/；活动 apps/web/dist 的 399 个文件前后逐 SHA 相等。build-summary.json 记录源、recipe、各产物指纹和构建时间，junit.xml 为实际报告。

Chrome 134.0.6998.35 + Playwright 1.51.1，1440/390 像素回放通过。实际查看四张 replay-*.png：中性提示可读、详情隐藏/展开正常、键盘焦点保留，无横向溢出或警告文字截断。技术码默认折叠，Enter 可展开再收起。生产构建资源也核对包含新提示、无旧断言。

**UI 回放不等于业务 E2E。** 浏览器页面为 task 独立入口，直接打包修改后的真实 WordCard 及原样 CSS；不是生产 dist 的完整 App。真实 captured external-word-state.json 仅为投影，id/name/conversion 字段原样保留，其余合同必填字段来自既有单测合成 fixture。详情、资格呈现验证不证明真实重试/上传/下载或 DB 落库。本轮不访问正在验收的真实服务、数据库、对象存储、Word executor 或模型。

官方浏览器工具在唯一自建 2GiB 容器准备；回放前断开外部 Docker bridge，仅容器 loopback 静态服务器，未发布宿主端口。resources.json 证明唯一标签容器精确删除、closed=true。未删除镜像、卷或其它服务。新 dist 未部署；task runner 快照仅是证据，不是产品入口。

准备过程中 task 入口先后补齐包解析路径、Vite ?url 资源语义及 esbuild 异步插件调用；这几次均发生在创建浏览器容器之前。首次格式检查失败后仅格式化获授权的两源文件，最终检查与构建均重新实际完成。未修改其它产品源码或 Git。
