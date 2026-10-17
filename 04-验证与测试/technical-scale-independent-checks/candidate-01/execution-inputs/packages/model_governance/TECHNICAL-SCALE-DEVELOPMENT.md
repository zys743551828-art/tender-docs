# 技术总满分权威增量（独立应用候选01）

本片代码有7个独立router操作：能力、用途上下文、一次登记／确认、列表、版本查询、更正、撤回。入口为apps.api.technical_scale_routes.router；actual扩展为packages.model_governance.scale_contracts.extend_openapi。主应用／live尚未接线。C经独立架构静审，由PM在technical_scale01仅新fresh完整17SQL／7限定LOGIN；本候选在该库跑真实非owner PG与登录ASGI。不能由软件夹具推导真实资料或模型资格。

当前用途由服务器当前身份和招标／补遗材料推导；历史用途先选原实交、已确认HistoricalSubject与精确material、claim，不需要先造review或HistoricalInput。当前关闭／归档仍拒写；历史档案只追加独立研究权威。原提交、项目头和created／known／obtained不变。

先短事务按RC→全局组协调锁91143→bid→graph捕获身份、原件／最新parse／精确页block、全祖先头、历史proof／claim决定及用途头，关闭连接后核原件实际字节，再短事务重新捕获和强CAS追加版本／来源映射／人工决定。confirm一次提交即可；不补第二轮聊天确认。无provider或对象PUT。

SQL只给API版本列级INSERT（不含created_at）、追加证据映射和受限确认／撤回RPC，head由函数CAS。历史与当前用途有效头分别维护；决定后不可追加映射。CPU／score／普通Worker没有新schema或表权限。版本只保存引用和人工登记事实，不授模型输入／训练／评分权利。

首个声明解析只支持完整原文证据块的明确技术总满分与量尺，如“技术总分满分为60分，量尺为未加权原始分。”。量尺目前明确只支持“未加权原始分”和“已加权技术总分”，能力／上下文如实返回。其他句式、复杂换算、跨块声明保留待核对，不能选一个数字或任意scale_description尾句洗白歧义。

原A已复现“量尺为未加权原始分，但技术总分现为80分”自由尾句可吞入描述仍认60；原失败、A字节已保全。B改正向完整量尺token，并与API对齐完整1..N物理页、当前known不能未来、完整祖先同bid和历史每祖先版本claim。canonical UUID5 source↔parse关联由API共享helper构造，SQL再核typed原件／parse／页／block／parent和全部head；这个API可信构造边界不冒称抵御任意API-role SQL。

首增量聚合只保留unknown／unsupported；有据满分和量尺不等于聚合可执行。接口的maximum_usable仅在该版本当前有据确认且完整来源／身份／证明仍有效时可用；数值模型、业务发布、历史输入／评分manifest引用、覆盖分母接入均未交付，已有评分候选仍max=null/features与预测unavailable。不能取条款和、官方标签或默认100补分母。

本应用候选保留45项纯检查，另真实新库PG／登录ASGI检查覆盖7路由生命周期、幂等重放、来源撤回、存储IO最终CAS、pending空分母、关闭／归档历史研究独立分支、Worker越权和API旧RR快照拒绝。每个普通资料夹具均经实际新上传→CPU解析→仅Mock条款提取→offline补录→合成历史Subject／proof→HistoricalInput。没有原生OSS、收费模型或Kafka进程。ruff／format／mypy运行模块5项／bandit通过；测试中的assert仅作为测试断言，不作为业务守卫。

## C：完整祖先历史证明集合

B的TS01静态反例已独立保全：直接quote仅PDF，真实祖先含Word时，claims既要等于PDF集合又要覆盖Word，无法满足。C由服务器从全部直接evidence.source_id的完整来源祖先推导DISTINCT document版本集合，并与claims精确比较；逐映射验证同bid／实交／Subject、真实原件blob摘要、当前决定与proof、存在／本方知晓／生效时点及区间。Word原件无需也不会伪造可解析evidence／chunk。

API同样使用canonical _parse服务器返回heads推导完整集合，增加祖先原件名／版本供可信选择；所有祖先实际blob字节均在事务外核对，最终重捕获不能只核PDF而漏父原件。SQL和API分别拒缺少／多余声明、父版摘要／主体／实交错配、父版pending或proof撤回、事后知晓或时点逆序。C精确SQL SHA ba5bd56c4ae03628cfac01d085cf8eb10fa95b094d3d554f2e866db10bb5dfad，静审和仅新fresh已通过。真实Word→固定Docker network-none转换→受控PDF→CPU解析→全祖先来源→各版合成历史声明→Scale确认长路径已执行；DB核Word没有parse／evidence／chunk，缺父版claim／多余claim均拒，确认后撤回Word根使分母null。转换器校验固定镜像、无网络、只读挂载、非root、资源上限和清洁环境；不继承provider或桶凭据。

C不会把新生成PDF的实际created／known改早，也不会把Word证明静默复制为PDF旧事实。若2026首次生成PDF、仅有Word当年的证明，仍须依产品协议提供该精确表示的合法历史可见权威；派生表示的新资格分支未实现。后续显式合成测试的充分Word＋PDF双版本声明仅验证机制，不能声称真实PDF字节当年已存在或真实历史资料资格通过。


## 接线与交付限制

独立router为`apps.api.technical_scale_routes.router`，actual扩展为`packages.model_governance.scale_contracts.extend_openapi`（只对应7实际操作）。当前列表最多200，暂无分页。所有对象GET均在业务事务外；存储读取期间独立连接可取得graph锁并撤回根，最终CAS拒绝且不留版本。来源或头变化后确认重放拒，不把历史201响应当当前资格。

Score候选32893…及F SQL28e9…的12源仍原样冻结，技术max=null CHECK未修改；本增量不会让旧score input或旧judgments自动取得分母。后续真实Scale→HistoricalInput／Score manifest与覆盖分母链接须另版本／SQL静审／fresh。官方聚合执行、历史表示权威、回溯训练四时点实际authority、真实标签质量、独立模型cert和正式上线仍未交付。
