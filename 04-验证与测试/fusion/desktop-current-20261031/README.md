# Windows Desktop full/1 当前版本本机验收

当前版本 `35b9e3c6f84a43ac79a2f21b4a3616ae378f61d8` 已在全新隔离实例中通过现有 `prepare → up → verify → stop`。这是本机 full/1 基线，不表示生产部署、Word v2 或 full/2 已接入。

| 轮次 | 实际结果 | 最终精确清理 |
| --- | --- | --- |
| 01，188e2d6 | 启动通过；测试 wrapper 读取不存在的可选 Health 字段失败，verify 未执行 | 15 容器、2 网络、14 卷，全部确认不存在 |
| 02，188e2d6 | 启动通过；verify 实际执行并因 ModuleNotFoundError 失败，静态定位为未锁定的 ReportLab 夹具生成依赖 | 16 容器、2 网络、14 卷，全部确认不存在 |
| 03，当前版本 | 固定两页合成 PDF 替代运行时 ReportLab 依赖后，正常 verify 通过 | 16 容器、2 网络、14 卷，全部确认不存在 |

第三轮 11 个配置了健康检查的服务均 healthy，configure/init/publish 均退出 0；object-store 正常运行且没有声明 Compose healthcheck。verify 确认 UID 65532、非 owner 数据库角色、同源 HTTP、会话及票据隔离、Kafka Worker 实际解析、2 个物理页、原件下载摘要匹配；coverage 保持 partial，current_body_changed=false，provider_calls=0。模型及自动任务 profile 默认关闭。

第三轮沿用原 59 项精确依赖镜像及原生 Windows 入口；前端 399 个发布文件与前两轮相同，源码绑定仅变化 `deploy/fusion_desktop_verify.py`。固定镜像、完整命令、逐服务状态、资源标签/ID、精确删除及最终空列表见各轮 JSON。临时 worktree 的 Git 操作与删除由 root 负责；测试 agent 未修改 app 或 Git。

`fixture-regression/fixture-regression.redacted.xml` 是 root 已执行的语义回归：禁止 ReportLab 导入后用实际 PDFium 检查原文字及两页，1 pass、0 fail、0 skip。另有本机夹具探针和源字节副本。Ruff、mypy --platform linux、Bandit -ll 仅有通过的工具回执，没有另造日志。root 临时 mypy cache 的精确清理凭据也已归档。

本目录不含私密配置、密码、生产数据或原始日志。JSON 仅脱敏宿主绝对路径并重新序列化；JUnit 仅脱敏 hostname 并重新序列化，执行结果未改。原始文件继续保留于 task evidence；原始摘要与来源见 `source-binding.json`。源文件副本保持原字节。局部 `.gitattributes` 使用 `* -text`，避免暂存时换行转换。

三轮合计精确删除 **47 个容器、6 个网络、42 个命名卷**。五个固定镜像在收尾后只读确认仍存在；原有卷排除于所有删除范围，未读取其内容。后置观察及证据边界见 `preservation-and-cleanup.json`；没有伪造全局 before 清单或原卷字节对比。执行 wrapper 的原字节副本也已附在 sources。

Root另以测试前只读差异清单及当时新构建还原原活动dist的完整预期清单，再逐文件核对当前399文件，字节全部未变，见`original-dist-preservation.json`。

当前应用提交的[CI41113128132](https://github.com/zys743551828-art/tender-app/actions/runs/41113128132)已11/11成功，完整终态元数据见`ci-41113128132.json`。CI成功不改变上述工程/正式业务验收边界。
