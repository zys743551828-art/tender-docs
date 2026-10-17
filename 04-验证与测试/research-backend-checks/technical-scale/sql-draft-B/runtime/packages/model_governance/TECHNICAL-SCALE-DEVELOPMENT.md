# 技术总满分权威增量（待独立SQL审查／fresh）

本片代码有7个独立router操作：能力、用途上下文、一次登记／确认、列表、版本查询、更正、撤回。入口为apps.api.technical_scale_routes.router；actual扩展为packages.model_governance.scale_contracts.extend_openapi。尚未接共享main、未有此领域数据库、未跑实际业务PG／HTTP；纯检查不能当权威资格通过。

当前用途由服务器当前身份和招标／补遗材料推导；历史用途先选原实交、已确认HistoricalSubject与精确material、claim，不需要先造review或HistoricalInput。当前关闭／归档仍拒写；历史档案只追加独立研究权威。原提交、项目头和created／known／obtained不变。

先短事务捕获bid→graph下的身份、原件／最新parse／精确页block、全祖先头、历史proof／claim决定及用途头，关闭连接后核原件实际字节，再短事务重新捕获和强CAS追加版本／来源映射／人工决定。confirm一次提交即可；不补第二轮聊天确认。无provider或对象PUT。

SQL只给API版本列级INSERT（不含created_at）、追加证据映射和受限确认／撤回RPC，head由函数CAS。历史与当前用途有效头分别维护；决定后不可追加映射。CPU／score／普通Worker没有新schema或表权限。版本只保存引用和人工登记事实，不授模型输入／训练／评分权利。

首个声明解析只支持完整原文证据块的明确技术总满分与量尺，如“技术总分满分为60分，量尺为未加权原始分。”。量尺目前明确只支持“未加权原始分”和“已加权技术总分”，能力／上下文如实返回。其他句式、复杂换算、跨块声明保留待核对，不能选一个数字或任意scale_description尾句洗白歧义。

原A已复现“量尺为未加权原始分，但技术总分现为80分”自由尾句可吞入描述仍认60；原失败、A字节已保全。B改正向完整量尺token，并与API对齐完整1..N物理页、当前known不能未来、完整祖先同bid和历史每祖先版本claim。canonical UUID5 source↔parse关联由API共享helper构造，SQL再核typed原件／parse／页／block／parent和全部head；这个API可信构造边界不冒称抵御任意API-role SQL。

首增量聚合只保留unknown／unsupported；有据满分和量尺不等于聚合可执行。接口的maximum_usable还需新SQL真实资格通过；数值模型、业务发布、历史输入／评分manifest引用、覆盖分母接入均未交付，已有评分候选仍max=null/features与预测unavailable。不能取条款和、官方标签或默认100补分母。

当前31项纯检查及ruff／format／mypy／bandit通过；没有实际新DDL、PG／登录ASGI、原生OSS、收费模型或消费者验收。草案B需架构静审后由PM另建fresh，后续实际对象／来源撤回／组与身份竞争／权限／声明词法正反例及相邻状态需真实非owner执行，再冻结应用候选。此文和SQL B不是正式模型或历史留出质量证据。
