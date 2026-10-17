# 同源OSS传输独立验收

真实同源OSS传输与现有PDF资料链通过独立验收，当前范围阻塞0。结论见`proxy-final-result.json`，摘要核验见`proxy-verification.json`，实际字节在`frozen-proxy-inputs/`。不代表完整A1、RAG、正式评分或生产通过。

## 实际接入与范围

独立release环境为PG55441、Kafka19095、API8802、web5175；使用私有临时成员和非owner API角色。对象操作经真实原生OSS。API8802本次运行的实际OpenAPI为28路径，测试前后字节不变。工作区新的36路径组装未被冒认为8802已升级；36路径只出现在本次故障API8803并另存实际OpenAPI。

对象传输绑定候选02，实际源SHA `cb3b16dd…`。前端绑定50源冻结候选，manifest SHA `64da2b09…`，逐项核验并保存。日志边界另见`log-boundary-observed.json`：实际8802禁access-log；没有声称完整扫描其PTY日志。

## 已执行证据

| 证据 | 实际断言 |
| --- | --- |
| `proxy-http-01.json` | Cookie／CSRF／Origin及同成员新session和他成员拒绝；Host／Forwarded不改变可信授权URL；改token／换upload拒绝；错媒体／编码／魔数415、错长度422、无长度411、超限声明413；实际超额实体413；合法暂存只公布独立key，旧ticket403；parts恢复、显式complete、Kafka复算SHA；同大小错内容隔离；中文原件SHA、Range全量200、私有下载；真实短授权随本次上传4秒有效期自然到期410；真实流读取屏障下refresh／complete／abort竞争不换key |
| `external-browser-01/result.json` | 真实Chromium无mock：同源Cookie／CSRF上传与OSS核验入库，文字／空白／图像三物理页实际canvas，原名与SHA下载，390px／焦点／刷新恢复，UI V2→历史V1第三页与V1原件下载 |
| `publication-races-01/result.json` | 仅QA受控API8803：实际SDK写云成功后暂停，8802三种并发命令各自胜出；释放后旧公布403／409、原staging_key不变、有效版本0。每个候选先查所有uploads／blobs引用为0，再定点删除204。核PID／cmdline后只停该故障进程 |
| `download-boundary-supplement.json` | 既有原件只读：同票据换另一实际版本403、错方法405、缺ticket422；注销后手动重放旧Cookie仍401，未把Cookie自动清除当作服务器撤销证明 |

短传的实际观察是半关闭连接没有完整HTTP响应，数据库状态／key没有前进；没有把它写成服务端已返回408。云写后竞争使用明确故障注入实例，不能冒充未插桩8802的自然时序。正常应用合成原件仍保留其有效数据库引用，没有为清理测试而删除这些原件；仅清理确认无引用的本次3个竞态候选。

附加只读焦点观察：打开原件焦点在关闭按钮；Chrome下载后activeElement暂回BODY，下一次Tab进入dialog内select。关闭原件返回来源按钮的断言通过；不声称下载后始终保持在同一按钮。

## CORS与安全边界

`oss-cors-readonly.json`实际返回404 NoSuchCORSConfiguration，未修改。PM转述的CORS配置写入自动审批拒绝及原因保留在`cors-configuration-decision.json`，dryrun未当已应用配置。接入使用正常鉴权的同源服务端传输，没有重复请求审批或绕过bucket策略。

QA未列已有对象、未改ACL／CORS／versioning、未发新的付费chat。实际密钥值与完整传输ticket／云签名URL在本目录文本产物的匹配为0。私有profile及测试成员不进入冻结目录，普通日志不接收密钥。

## 未验证

完整A1业务与模型检测质量、正式调用账本接线、RAG及本地BGE效果、multipart、高并发／容量、多节点、真实OSS依赖拒绝／超时故障、完整PTY历史日志审计、真人／真实投标资料和生产保持未验证。后台可能做本地BGE索引，不纳入本次RAG通过结论。冻结输入及原有13条应用任务不因本次传输结果被改写。
