# 交给下一位AI或工程师

请接续全新AI投标工作台，当前是工作检查点，不是最终版。先切换5个仓库的 `handoff/2026-10-17` 分支，再读 `tender-app/workbench/docs/handoff-20261017/START_HERE.md`、六个角色交接、`BACKLOG.json`、`RUNTIME_AND_PRIVATE_STATE.md`、本目录 `github-receipt.json`。不要只克隆main。

源码在tender-app/workbench；原tender-app根backend/frontend是历史实现。产品PRD、全新技术方案、详细设计在tender-docs，历史资料不能改写成新方案通过。没有旧系统迁移需求；Kafka+独立Python Worker、PG17+pgvector/RAG、干净完整相关章节三次独立正式评分判断是当前基线。PM负责整体，产品Owner负责方案，架构/测试/用户独立验收。

先用发布inventory核31,201文件SHA，分清固定候选、moving source和实际运行实例，再恢复有依赖顺序的待办。Word03/前端12/RICH02均有未冻结或未原生独验部分；预算SQL02/helper04存在阻塞，03/05尚未实现，不能直接执行。不要批量运行初始化/删除容器卷/格式化Kafka/覆盖原失败。

当前唯一次新增云授权是7个自建合成对象322B、固定prefix、最多7PUT+7GET；未消费，旧driver03会14GET且动态prefix，禁止直接execute，新的限次helper/driver还未写。真实付费模型授权仍未获得，不能启动paid消费者或用别的runner绕过。原新Kafka19098为恢复空间park，数据/config保留，旧broker不能随意停。

源代码/方案/测试证据均可从GitHub取得；密钥/口令/数据库/私有备份留在原环境，按交接安全转交或重新配置，不能凭clone宣称原实例完整恢复。当前原 /workspace/tender-workbench 独立Git仍未有提交，真正已发布路径是 /workspace/tender-app/workbench；不要在两个目录独立编辑造成分叉。

建议新环境目录（需要有这5个私有仓库读取权限；以下仅取回源码，不创建服务/数据库）：

```bash
mkdir -p /workspace
cd /workspace
gh repo clone zys743551828-art/tender-app -- --branch handoff/2026-10-17 --single-branch
gh repo clone zys743551828-art/tender-docs -- --branch handoff/2026-10-17 --single-branch
gh repo clone zys743551828-art/tender-design -- --branch handoff/2026-10-17 --single-branch
gh repo clone zys743551828-art/tender-research -- --branch handoff/2026-10-17 --single-branch
gh repo clone zys743551828-art/tender-datasets -- --branch handoff/2026-10-17 --single-branch
python tender-app/workbench/qa/verify_handoff.py --workspace /workspace --inventory tender-app/workbench/docs/handoff-20261017/publication-inventory.json
```

已有同名目录时先盘点，不覆盖。若要恢复原固定绝对路径，只在不存在 `/workspace/tender-workbench` 时建立到`tender-app/workbench`的链接；原环境仍有私有状态时不要删除旧树。后续具体初始化、profile、端口、身份、费用与资源门均先读主入口，不因上述clone成功授执行资格。

命名澄清：BACKLOG中的ASSISTANT-SQL03为设计任务WIP，SQL03文件尚未写入/冻结；WORD03-FINAL指Word补PDF声明/保留字节增量03，不能与已审外来Word intake03 `b12e2e45…`混同。架构师仅只读复核交接语义，没有重做远端SHA验证或业务测试。
