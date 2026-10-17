# 合成评审夹具：只用于界面机制验收

入口是本候选冻结的 tests/test_review_integration.py，参数变体 `test_real_freeze_and_full_core_pipeline_with_offline_model[True-False]`。显式 `REVIEW_FIXTURE_ONLY_OUTPUT` 才在完整新稿复检及闸门允许之后立即停止，保留当前正文、人工确认、全部原件、来源与两轮历史；不继续后续报告、实交更正、来源撤回或身份更正测试。普通全回归不设置该变量。

路径：真实非owner账号引导→API登录/建明确主体项目→PDF签名或同源传输→Worker SHA核验→版本CAS入库→真实Parser03原页→API控制面冻结→模型端口显式MockTransport提取/核心判断→人工规则确认→同稿nonce机制→新稿上传、解析与复检→提交闸门。Mock共4次，不是正式评分三次判断；0远端模型、0付费、无Kafka消费者，直接驱动本项目权威Outbox收据。未直接SQL写matrix/review_results或造绿色评审结果。数据库只用API/Worker非owner角色，成员/登录初始化为测试准备。

`REVIEW_FIXTURE_ONLY_OUTPUT` 写新建600私有JSON，包含合成说明、bid/review/member UUID、真实manifest、body ID、gate_allowed及允许Origin；不包含口令、Cookie、CSRF、签名URL或云locator。现有两个QA全权限成员可从实际项目列表进入，不需要复用测试成员口令。输出路径若已存在则拒绝覆盖。

开发在隔离backend-checks库已执行，结果见 developer-checks-05/fixture-only.xml；仅S3开发传输端口，不能声称8806/OSS/浏览器已验收。OSS同源PUT分支沿已认证TestClient Cookie+Origin/CSRF及PDF长度/类型；该分支的真实外部环境执行由PM另绑定。

## 在固定执行根使用

PM选择目标固定根和相匹配的私有环境。必须加载该根的 apps/packages/contracts 和方法摘要，不能用moving05领域代码给正在运行04的API生成“当前”评审，否则方法身份不同，闸门应拒绝。

1. 从本候选归档复制测试驱动到独立临时文件，例如 `/tmp/review_fixture_driver_05.py`；只复制测试文件，不修改固定根生产源码或现有数据库SQL。
2. 在目标固定根作为工作目录，使用现有后端解释器和 `deploy/run.py --env-file <私有目标配置>` 加载API/Worker两非owner角色；这是合成集成测试，不按单一生产role裁剪。保持OSS prefix/provider绑定，不切现有数据库的对象存储配置。配置必须已有Parser03 URL/token，代码不会打印它。
3. 设置 `RUN_REVIEW_INTEGRATION=1`、`RUN_PARSING_WORKER_INTEGRATION=1`、`REVIEW_FIXTURE_ONLY_OUTPUT=<全新600私有JSON路径>`、`PYTHONPATH=<目标固定根>`。
4. 执行 pytest，显式 `-c /dev/null --rootdir <目标固定根> -o pythonpath=<目标固定根> --assert=plain --tb=short -q '/tmp/review_fixture_driver_05.py::test_real_freeze_and_full_core_pipeline_with_offline_model[True-False]'`。避免原工作区pyproject的pythonpath把moving模块带进固定根。
5. PM私下读取JSON中的UUID并交界面测试；真实HTTP GET manifest/Gate核对运行服务与该固定根一致，再安排报告/标准登记。不要把JSON或原件正文贴到公共日志。

需要真实PG、已配置对象存储及Parser03服务。缺能力明确失败，不启动任何实际收费消费者来补齐；任何失败保留原机制证据，不能把未执行说成通过。
