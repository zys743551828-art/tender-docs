# FR01103：历史业务与当前系统发布时序

03只修来源时间口径并提升来源权威版本，保全02 manifest、冻结副本、37项机制证据及01原反例。

来源 node.known_at 是业务可知时间，必须 ≤ 结果业务 known_at cutoff、观察 as_of 和真实当前时刻。source head 的 valid_from/valid_until 是系统发布/有效窗，仅对观察 as_of 与当前时刻检查，不与2019业务时间比较。官方2019结果作为2026全新系统的新资料，服务器可以在当前已发布状态核验；不要求或补造2019系统发布头。as_of=2019 仍不可用这个2026头冒充历史系统资格。

其他正向 canonical/角色/摘要/精确parse-page-evidence/完整祖先检查、RC与 bid→graph90127→heads 锁序、同key当前有效性重投影、不得旧头回退均延续02。服务器 authority schema 升为 official-source-authority/v2，因此旧v1冻结digest不能自动取得新语义下当前资格；原历史 verified 保留、当前 unusable，显式新核验才生成v2，绝不改写历史事件。

开发39项通过：完整02的37项，加真实非owner PG/HTTP 2019业务known/2026系统published正例（核验及verified_only 1/1、历史as_of不倒造），以及v1旧authority不得新用、显式重新核验v2正例。未来来源known、晚于业务cutoff、当前未生效/过期/撤回及并发锁回归仍通过。所有资料为合成机制夹具，0外部/模型/付费调用，不授预测资格，不代表完整FR011交付。无新SQL/权限/共享源改动。
