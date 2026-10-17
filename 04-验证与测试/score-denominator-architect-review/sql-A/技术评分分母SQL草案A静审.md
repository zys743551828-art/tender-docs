# 技术评分分母SQL静审

结论：**A有一项严格研究时点阻塞，暂不执行A的fresh DDL；修正后仅需增量静核。** 输入归档 SHA256 `21a41e3937a81dd2c8967360e7ef86b3ba1bf8dcffba4dbf0ecb164bf0811e96`，未执行SQL或连接数据库。此为代码逻辑静审，不是实际PG漏洞复现或功能验收。

## SB01 / P1模块阻塞：严格模式未约束当前确认决定的真实时间

定位 [score-denominator.sql](/workspace/tender-docs/04-验证与测试/research-backend-checks/score-denominator/sql-draft-A/score-denominator.sql:96)。`strict_prebid` 仅比较 `technical_scale.versions.created_at <= submitted_at`。前面的 `technical_scale.current_version` 证明当前head指向confirmed决定且来源当前合格，却没有证明该决定在提交前已经形成。ScaleC的apply_version将决定真实时间记为clock_timestamp，版本header时间与决定时间是两个独立字段。

最小离线反模型：10月1日创建尚未决定的版本，10月2日提交，10月3日才由当前决定确认；保持其余来源、身份、章节范围均合格。A现有版本时间谓词为true，但“事前已经存在已确认权威”的谓词为false。证据 [strict_time_countermodel.py](strict_time_countermodel.py) 和结果JSON。该反模型验证谓词遗漏，不声称当前实际API已经走通该时间序列。Owner已明确strict真事前包含当前confirmed decision时间，接受修复。

修正：在strict分支从当前head取真实confirmed decision，要求其created_at也<=实际submission时间；绑定的decision和head revision必须与freeze/current_binding所保存和重核的一致。保持current review允许同源事后人工确认的独立用途语义；不把strict限制误加给historical_backtest。验收：事前header/事后confirm的strict绑定不可用，两个时间都事前且当前有效可用；普通current晚确认和历史专用分支仍按各自权威允许；后续更正撤回使current不可用。

## 其他已读边界

两张独立append-only表没有改旧ScoreF max=NULL约束或原结果。API只读表，通过受限definer函数冻结/核验/发布，其他Worker/训练/评测/评分角色无新通用读写；PUBLIC函数权限撤销，固定search_path。目标身份与完整评分规则从ScoreInput/HistoricalInput/严格cohort成员派生，不接客户端满分/规则白名单。cohort路径先全范围排序bid，再图锁；scale先核samebid再调用其权威锁入口。当前有效来源/身份/章节由已审基础守卫重核。

分母仅有据“未加权原始分”可用；已加权量尺不默换口径。条款分合计仅用于检测超过有据总满分的conflict，不拿条款和推造总满分。数值有有限/正数约束；current_binding重核完整scope和确切scale决定/修订，失效投影不能继续当当前可用。派生产物引用原已成功三判的items/result digest，不创建调用或放开unknown补发；formal与prediction固定unavailable。

未验证：DDL语法/真实LOGIN权限/锁竞争、严格正反例、来源撤回与结果发布竞争、actual API/Schema/派生数值算法。后续应在修订稿静审后由PM独立新库初始化，再用非owner执行，不热补旧F/ScaleC。此次不授模型或正式评分资格。
