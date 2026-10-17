# FR029登记与受控路由：开发中

本增量不修改StageA候选01（3929d000…）的20个输入。新增6操作来自`apps.api.model_registry_routes.router`，actual扩展为`packages.model_governance.registry_contracts.extend_openapi`；未接共享main、未live，已在PM另建fresh非owner环境自测，尚未独立应用验收。

- GET `/api/v1/model-registry/capabilities`
- GET `/api/v1/model-registry/training-jobs/{training_job_id}/model-preview`
- POST `/api/v1/model-registry/models`
- GET `/api/v1/model-registry/models/{model_id}`
- GET `/api/v1/model-registry/routes/{lane}/{route_key}`
- POST `/api/v1/model-registry/routes/{lane}/{route_key}/changes`

候选身份由服务器训练job稳定派生；DB derive完整模型JSON、scope和原训练身份，不接客户端参数/标签/metric/accepted/origin。实际对象原字节SHA、大小和server registration document分别核验；仅支持有界惰性JSON，无pickle或脚本。preview/read/storage/CPU与最终登记各自短事务，对象PUT/GET不跨SQL事务，最终重新核来源及强ETag。

`artifacts.infer/compare`只实现可复用的无费候选推断和影子数学/隐藏投影，不是当前review完整三判断producer，也未注册影子比较API或持久化shadow_comparisons。候选推断不能自行成为accepted预测；缺基线点分则差分null。隐藏时不序列化value/interval/difference或原始特征。

shadow与business路由分开；shadow lane不改design.model_routes。首次路由revision=0/model=null；CAS发布、暂停、回退追加event且revision持续增加。候选、当前及回退模型所有cohort相关bid先合并排序锁，再取图锁；不逐模型锁图后补其他bid。回退仍核当前来源和scope。暂停保留原current作为恢复目标；恢复前仍重核，不表示该对象永远可用。该语义待独立产品验收。对象可读不等于来源合格；当前失效的候选可保留只读历史，但禁新路由使用。

business非null依赖独立certifications、真实来源及accepted acceptance。此草案无任何业务角色可写certifications，也没有签发函数；故经本registry API/authority的非null正式发布持续拒绝。baseline tender_api原有直写design.model_routes/acceptances权限未由本增量改变，不宣称抵御任意API-role SQL；后续score producer必须只认本受控cert路线。正式certification须额外绑定final exposure前冻结的业务门槛协议、真实用途来源、报告、观察和回退验收，不能从StageA合成误差或训练协议直接生成。

SQL A/B各归档`research-backend-checks/fr029-release/draft-*`，B为80d5c942…；架构最小静审后PM在tender_checks_model_release01全新初始化，未改旧D/release/combined库。当前仅显式内存对象与MockTransport（每个合成研究样本3次账本判断），0 native OSS、0外部付费、0 Kafka消费者。CPU只从单用途600配置读取对应DSN，经purpose_profiles固定-I子进程白名单进入冻结StageA runner；不加载通用应用配置或另一CPU用途配置。子进程真实成功与8个环境反例共同证明该边界，不把主API profile中存在的通用keys继承给CPU。

本范围自测：21 artifact、11 actual request/response contract、8 purpose profile、3综合fresh PG/API（总43项）；另旧D缺扩展明确503/0PUT单例1过。综合PG覆盖session/CSRF/幂等、字节篡改、scope、shadow首次/后续CAS、暂停/回退、同版本并发一成功一412、正式非null拒、跨role拒、RR拒、非法raw envelope拒、来源撤回留档禁新用/禁旧登记重放、两个独立cohort全bid锁等待后身份更改导致发布拒，以及对象I/O时0 idle-in-transaction。独立QA/架构应用review和产品/用户review尚未通过。

首次fresh失败是合成夹具的engineering_type=null被载入器误拒，修为明确[null]范围且只匹配unknown，不扩成任意工程类型。随后测试复跑暴露fixture错误假设scope路由每次revision=0，已按真实持续revision修断言，不清库重置历史；原失败JUnit与现有路由历史均保留。SQL B未修改。历史请求/DDL在另一候选线，不把其请求语法检查计入本release交付。

本阶段仍未交付review-based影子比较持久化、正式certification签发/业务接受、score authority/账本/完整三判断生产者、专用Kafka调度和真实质量验收；这些继续保持unavailable或未配置，6操作不代表完整FR029完成。
