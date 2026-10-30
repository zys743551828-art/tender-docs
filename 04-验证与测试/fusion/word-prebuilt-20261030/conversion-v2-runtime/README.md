# v2 转换通道回归证据

对应应用提交 `2a64dfe0412b80375026a3e969cd4f2c5e2307ba`。本机 Linux 容器内 103 项执行通道/host 回归见 [JUnit](worker/junit.xml)，静态检查及资源收尾见 [日志](worker/checks.log)、[资源记录](worker/checks-resources.json)。主流程、生成及富排版调度的 54 项回归见 [JUnit](dispatch.xml)。这些是机制回归，不是业务或生产验收。

另有 51 项 prebuilt 单测通过及 Ruff/Mypy/Bandit 通过，当时只有工具回执，未保存 JUnit 或独立日志，本目录不补造该证据。真实 PAGE/RICH 与浏览器链分别见相邻 `conversion-v2-authoring` 和 `conversion-v2-browser`。

[源文件 LF 核对](source-lf.json)记录实测前对本轮修改文件的换行统一与 AST 一致性。[本提交 CI](ci-41100590565.json)为 `2a64dfe` 的 11/11 成功，[前一提交 CI](ci-41092556442.json)为 `2db73a2` 的 11/11 成功。[Kafka 官方 CDN 探测](kafka-official-cdn-head.json)仅证明当时 HEAD 可获取；[CI 实际片段](ci-41100590565-kafka-cache-snippets.json)证明首次缓存未命中、下载完成、SHA512通过并保存缓存。后续前端构建材料提交的 CI 单独跟踪，不借用这些结果。

仅归档受控合成测试元数据和日志，工作区路径替换为 `${TASK_ROOT}`；未收录环境文件、凭据、真实业务原文或缓存。[文件清单](archive-inventory.json)供证据字节核对，属于验收记录，不是新增依赖版本治理框架。
