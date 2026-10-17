# 任务草案03与实际任务协议

本批补齐受限中文草案的真实持久层和明确保存链，保留原任务执行与资格边界。不是通用自然语言理解、完整语义补遗或整项FR040验收。

冻结候选 `d2f26e52b3cd9f46585a28a443b67fbea19ada9596359365f641f98a2043f69e`：以F05的310源为基底，6生产文件、3测试文件自有增量，共317输入；原01、02均保留。无新增SQL或权限。

## 真实资源

- GET/POST `/api/v1/bids/{bid_id}/task-definition-drafts`：成员私有分页恢复、创建受限草案。游标绑定成员和项目。
- GET/PUT `/api/v1/bids/{bid_id}/task-definition-drafts/{draft_id}`：恢复及结构化编辑，强ETag、幂等、保留原始请求和不可变修订，created_at不变、updated_at追加。
- POST同资源 `/save`：一次明确保存为模板。未解决项拒绝；保存时重新构造严格TemplateRequest并校验可信来源/章节；一个草案只创建一个模板，不同命令并发只一份，失ACK同键复用。已保存后转原模板修改，不继续改旧草案。保存手动模板不启动run；显式保存定时配置才建立计划，不补跑过去任务。

界面可以在同一次“保存任务模板”操作内先PUT编辑表单再POST save，无额外聊天确认。PUT失败或未获ACK不能继续save；断线后GET恢复服务端草案。未识别的草案仍可打开编辑，ready标记不锁住表单。

普通写入使用统一项目锁/生命周期守卫；归档后旧ACK和读取可用，新草案/新修订/新保存拒绝。草案原文只写成员作用域的追加记录，共享SSE仅有draft_id/revision。草案不作为正式评分输入、正式决定或已执行动作。

## 实际协议纠正

任务目录原GET/POST被目标规划的TaskDefinition形状覆盖，与实际Template模型不一致。本增量的 `packages/tasks/contracts.py` 从实际路由/Pydantic生成，再按已挂载路径覆盖导出，不增加未注册接口。另三个GET提供TaskKindPage、TaskNotificationPage和TaskScheduleRecoveryPage。补遗真实子能力名称改为“已登记补遗关系核对”，语义影响仍unknown。

主main与exporter未在本批修改。集成Owner需要include task_draft_routes.router，并在目标映射后调用extend_tasks(actual)。本目录actual JSON是固定F05 main明确组合本router/extender的预览，不能称现8808已经更新；前端据最终固定组合生成类型。

## 验证及失败保留

固定317输入执行前后相同：83通过（28受限解释器纯检查、9真实非owner PG/ASGI草案场景、2actual契约回归、44既有模板/运行/调度回归）。独立task_drafts01库无broker/消费者；所有外部HTTP硬拒，实际尝试0。计划冻结用固定合成配置标记，不含真实provider key；unknown/reserved账本均为明确手工机制夹具。真实模型、OSS、Kafka和浏览器均未在本批验证。

9文件ruff/format通过，6生产文件mypy通过，bandit无发现。独立测试/架构与前端实际页面尚待。

原01五项新草案先通过；扩展回归曾误用正在运行Worker的workbench08，导致排队断言与缺模型配置两失败（77过），完整XML保留。该库70个由本测试成员创建且无真实上传/实交的合成项目已按生命周期终止、取消并归档，0删除；3个原手工synthetic账本原状态保留。隔离脚本误把账本行数当真实调用数而失败，随后只读核对确认为测试明写记录，诊断/最终隔离证据均在candidate01；未冒真实模型请求。此后01/02/03分别在无broker的新库运行79/82/83通过，不用后验通过覆盖旧失败。
