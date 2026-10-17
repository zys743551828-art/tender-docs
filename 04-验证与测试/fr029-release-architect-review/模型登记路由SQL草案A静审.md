# 模型登记路由SQL静审

结论：草案A需由Owner补最小B再核，暂不作为最终新库初始化输入。被审[原字节副本](observed-release-draft-A.sql) SHA256 `a18b5f09daefedc11cad57d0fe8f674f59584c674c63a7a5b2f09cc1c21b0bdb`，依赖已审治理D。只做静态调用链和权限核对，没有执行DDL或业务、无付费。

## RLS01：API外部锁入口缺执行权

[第52行](observed-release-draft-A.sql:52)的lock_scope用于API先合并候选/当前/回滚cohort及真实相关组全部bid，按91143→排序bid→图锁顺序取得统一scope；[第156—157行](observed-release-draft-A.sql:156)撤销PUBLIC后未授tender_api该helper执行权。Owner拟定API直接使用该入口的合法路径会permission denied；SECURITY DEFINER内部调用不受此缺权影响，change_route自身仍能取得锁。

影响限于该API正向路径，不是越权。建议仅授此单个helper的EXECUTE，保持CPU/score角色无权；fresh非owner分别验证API合法调用与训练/评测/score直接调用拒绝。Owner已确认补B，A保持原件。

## 输入与用途边界

[第117—125行](observed-release-draft-A.sql:117)依赖SQL三值逻辑，NULL参数可能延后由NOT NULL回滚，动作与null目标的组合需显式定义。Owner已自查拟补B严格校验；不把末端事务回滚误判为已发生路由越权。应覆盖缺参数、null expected_revision、publish null目标、suspend非null目标、无回滚候选及同revision竞争；非法命令0事件/0路由更新。

登记文档由服务器从真实成功训练job/result/protocol/cohort/method生成，再与JSON及实际blob摘要/长度/media核对；CPU角色没有新增原文、标签或对象key读取权。route以candidate/current/rollback全部scope联合排序锁，再核current_cohort，避免先后cohort反序锁。shadow不写design.model_routes；非null business需要real origin、独立certification、accepted模型与scope配对。当前无certification铸造入口，不能把合成软件指标转换成正式资格。

有两个实现边界须明确：baseline的可信API角色仍有design.model_routes/model_acceptances写权，本草案没有撤销；因此资格闭锁是本受控registry/API路径的保证，不能声称抵御任意API-role SQL。后续实际producer必须只认受控认证路由，不能另开宽松路径。当前suspend会清rollback_model_id，是否需要暂停后保留回滚目标应由Owner对齐产品语义；本审查不凭空增加恢复要求。

## 方法身份

逐字复用冻结descriptor、prompt、schema和完整章节判断包，仍把descriptor中的formal_scoring_eligible=false理解为判断器本身无正式资格时，可保留原judgment_method_digest。独立execution_purpose、score authority及收费账本属于执行层；不能伪造研究run/call，也不能靠同SHA授正式资格。包括purpose/port/formal标记在内的任一descriptor字段变化都必须产生新SHA，不允许计算时排除字段或自动兼容旧artifact。正式资格由另审认证与accepted路由决定。

本次未验证blob真实I/O、纯模型加载器、注册/回滚API、影子比较producer和正式score执行。B需留新摘要再核，PM随后才在全新隔离库验证语法与非owner角色；不得热改旧库。
