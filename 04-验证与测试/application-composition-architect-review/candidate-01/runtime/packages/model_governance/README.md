# FR029模型治理开发中

当前模块处于独立软件开发，不表示正式评分、模型精度或发布资格通过。研究03、报价01/02/03各冻结输入不改。新SQL只用于经审的全新独立库，绝不热补既有运行库。

实际产品闭环仍有已确认缺口：普通2026上传没有有据历史存在/知晓时间入口，历史offline事实也不能合法关联当前新建回测输入。故“真实历史资料普通上传→合法历史回测”当前不可达。本阶段历史正例是明确SQL构造的合成机制证据，绝不据此称研究可上线。差距与待产品/架构收口的独立authority见`fr029-development-checks/历史资料新入库可达性缺口.md`；不会倒改原上传/source.known或把2026新评审挂成2019事前评审。

## 已可执行

- `features.build`：沿现有三判断真实字段计算原始档位百分比、档位众数占比、档位跨度及引文占比。全必要规则满分合计必须等于已核实技术满分；缺规则/未读/错引文/三次不独立/无明确档位均unavailable和null，不补零。
- `statistics`及`training.run`：有界JSON均值、中位数、办法分组收缩、原始档位、岭回归、保序PAVA；项目等权，预处理只拟合当前fold训练数据。协议须明确家族、参数网格、组折、种子、开发选择、并列顺序与重拟合策略。最终标签不能进入训练导出。
- `leakage`：项目、模板、正文、近重复及服务器相关组闭包不跨用途；fit/development/calibration的提交与标签必须早于最终时间边界，final项目在边界后。关联组必须在折划分前归一，防同项目不同group ID漏入不同fold。
- `evaluation`：独立校准组最大绝对残差、有限样本向上分位秩的80/90区间；独立组不足则区间null。统计保留冻结全分母、失败/拒绝/无标签、项目/样本误差、原量尺、分层、基线配对与组重采样；缺重复整轮/排序/评委标签时相应统计unavailable。
- `purpose_runtime.launch`：固定Python隔离模块的CPU子进程，仅继承对应单一训练或评测DSN和LANG/LC_ALL/TZ，不继承通用.env/API/owner/S3/provider/PYTHONPATH或未知未来secret。模块不导入domain.runtime、供应商端口或对象库。独立LOGIN无设计表和exports表读取权，仅读自身lease绑定RPC。训练/CPU时数据库连接已关闭；短心跳、读写及最终重核各自短事务。纯CPU可重做，已成功结果不重做。
- 评测先由API计算并冻结无标签最终预测，评测角色另事务追加全局底层来源曝光并提交，再读封存标签。曝光created_xact必须不同于读标签事务；失败/取消不能撤销已提交曝光，变manifest/新任务也不能重开同一项目/正文/模板/近重复闭包。

实际router为`apps.api.model_governance_routes.router`，实际扩展入口`packages.model_governance.contracts.extend_openapi(actual)`。目前9个操作：capabilities、training-protocols POST/GET、cohort-previews POST、cohorts POST/GET、training-jobs POST、jobs GET、evaluations POST。未include到主应用、未live，不将计划路径计为实现。

API只接受服务器research_run/protocol/训练结果身份、明确calibration组及原因，不接受客户端labels/features/metrics/origin/accepted/allowed-source。原dataset仅由API编排读取；CPU仅得训练用途行，final labels为独立导出。来源守卫共用RAG canonical helper，重核真实成功call归属、完整必要规则、完整章节、精确parse/evidence、全部祖先、当时可见性及当前效力；对象读取和特征构建在事务外，最终同一新鲜短事务重核再登记。

当前API仅登记明确synthetic研究协议；所有训练及评测结果`formal_eligibility=unavailable`，synthetic永久research_only，不能修改为real。该软件链不把合成误差0变为真实资格。真实来源登记、最终业务门槛、观察及正式接受/发布仍未开放。

## SQL与实际验证边界

`deploy/sql/model-governance.sql`目前与草案D相同，SHA `0b7dc8863fb83703116189fb3e6bcf442ed5fae2250d50993189e04aefa6f51a`。A/B/C/D各自归档。C首次fresh初始化因PL/pgSQL CASE括号语法失败，extension事务回滚；D仅修该处，PM已在另一全新库`model_governance02`初始化成功并引导独立train/eval LOGIN。开发不加载owner或init配置跑业务。

独立fresh库仅真实PG；章包及manifest对象为显式内存dict，DeepSeek端口仅显式httpx.MockTransport，0外部OSS和0付费模型请求。不声称私有配置中旧OSS_KEY_PREFIX对应物理对象隔离；如需native探针，先由PM配置正确OBJECT_STORE_PREFIX。本模块CPU子进程始终没有桶凭据。

已证明真实session/CSRF→5个合成独立项目→15个Mock独立账本判断→纯CPU训练→先冻结final预测→提交曝光→评测的正向；同事务标签读拒、提交后合法读、跨用途/原始标签表/exports表/route写均拒。亦覆盖真实图锁等待后旧generation续/提交拒、最新parse/同UUID更名/candidate/撤回失效、未选关联成员完整曝光、重复曝光、RR失败关闭、完整冻结future known head仍拒、current_registration窗口与historical known分开，以及原页读/真实CPU入口0idle事务。最终本范围49过1旧schema预期skip，另旧schema显式503单例独立通过；不把软件自测或静态SQL审查写成独立/业务验收。

## 明确尚未完成

- 本模块专用Kafka CPU调度接线、异常状态投影及操作恢复UI；当前测试以显式bounded launch执行专用CPU任务，不运行普通消费者。
- 模型JSON blob登记、业务确认门槛与独立报告绑定、观察/影子、scope CAS正式发布与回退持久化。
- 当前项目独立score authority/预算账本、完整相关章节三判断生产者与业务prediction写入；score角色当前没有业务权限，新增SQL须另审fresh。
- 真实官方标签/材料、方法质量、独立时间组精度、区间覆盖宽度、整轮稳定性、观察及实际发布资格。

以上继续按已静审FR029协议实施，不以接口存在或合成软件测试代替真实业务效果。
