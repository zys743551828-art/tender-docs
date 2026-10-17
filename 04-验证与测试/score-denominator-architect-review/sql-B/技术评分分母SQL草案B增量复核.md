# 技术评分分母SQL增量复核

结论：**SB01时点缺口静态闭合，B可由PM仅在另一个全新独立检查库初始化后做实际机制验证。** 不热补旧ScoreF/ScaleC或统一应用库；当前不宣称DDL、权限或派生业务已验收。

精确输入SHA256 `c3f3b86260e7c967e4e14ab63865955ad6c71df74bb43791743d435c9388bab2`；A原件 `21a41e3937a81dd2c8967360e7ef86b3ba1bf8dcffba4dbf0ecb164bf0811e96` 保留。逐行 [差异](A-B.diff) 除草案注释，仅改变strict时点谓词：版本创建时间和当前head所指同版本confirmed决定的created_at均须<=实际submission时间。既有current_version/current_binding仍核当前确认状态、决定ID及头修订，不能用任意历史确认替代。

以原SB01的事前header/事后confirm构造反模型，B时间谓词拒绝；两者均事前才允许strict。8组离线布尔组合验证current用途不新增这条strict限制，见 [结果](offline-time-truth-table.json)。这不是实际PG或API复现。historical分支和current晚人工确认语义没有修改；权限、表、锁序、来源和结果绑定也没有变化。

实际新库验收仍需：合法API/RPC正向、所有非用途角色拒绝、事前header/事后确认的严格负例与事前双条件正例、current晚确认正例、来源/身份/决定变化失效以及publish竞争。数字分母可用不等于聚合受支持、模型精度或formal资格；旧unknown调用和原三判结果不可改。

本轮未执行DDL、连接数据库、创建资源或调用外部服务。
