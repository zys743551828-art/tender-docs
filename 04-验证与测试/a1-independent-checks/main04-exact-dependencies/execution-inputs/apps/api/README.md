# 后端运行与交付边界

这是全新系统。运行配置由私有`.env`或`deploy/run.py --env-file … --role api|worker`注入；显式profile存在时不再补读根`.env`。API、普通Worker、研究Worker、正式评分只读使用各自非owner LOGIN。初始化和运行分开，业务不使用owner或把API凭据发给普通Worker。

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
.venv/bin/python -m apps.api.bootstrap --login demo --name 演示成员
.venv/bin/python -m apps.api
.venv/bin/python -m apps.worker relay
.venv/bin/python -m apps.worker consume
.venv/bin/python -m apps.worker reconcile
```

bootstrap隐藏口令；自动化可`--password-stdin`，不提供生产HTTP绕过。API8800，web5173同源代理。Cookie `session`为HttpOnly/SameSite=Lax，HTTPS时Secure；全部写请求验证Origin/CSRF，领域命令再校验幂等与真实强ETag。口令、正文、签名URL、应用传输ticket不记录；uvicorn关闭access log。

当前代码注册了T0/PDF、评审准备及核心核对、人工规则／事实／效力、RAG、authoring、knowledge、identity、research、SSE的实际路由。注册只表示有真实处理器，不表示各能力均通过独立验收。实际接口由`python -m apps.api.export_openapi`或`/openapi.json`导出，目标计划中未实现的接口不出现在actual路径中。各独立模块有各Owner的候选及限制。

T0包含会话恢复/撤销、真实游标项目分页、PDF批次与单文件100MiB、字节核验、显式verified字节重绑、新文件/新版本CAS入库、历史版本、原页证据、下载及操作取消。DOCX直接上传仍415。OSS大于8MiB时选择原生分片，S3保留single；分片接线有独立原生OSS候选，本批整体仍待独立验证。GET upload/layout提供数据库权威总片数、片大小、整文件大小及SHA，跨浏览器从批次发现上传后读取，不依赖本地grant缓存；授权仍用实ETag与Cookie/CSRF。S3开发使用签名传输，OSS使用同源私有传输，不改变桶CORS/ACL。文件head修订与版本序号分开；保留已核验字节不等于用户已同意新目标。

A1.1代码提供一键评审持久准备请求：解析和要求准备完成后，独立API控制面冻结真实稿件/规则/事实/来源全祖先与方法身份，再派核心核对→汇总两阶段任务。用户离开不会阻止推进。自动开关开启时合并未冻结最新稿；关闭时旧请求过期。冻结后输入不改写。读取入口有review-state、单轮manifest、历史列表、矩阵、问题、规则及证据。准备请求取消使用review-state强ETag，已冻结轮次使用operation取消；共享解析不因单次请求取消而终止。

```sh
# 独立API控制面，只能注入API角色；正式启用前先核对模型预算和目标环境。
.venv/bin/python -m apps.api.review_coordinator
# 普通Worker角色；与document消费者分group，处理已冻结准备/核心任务。
.venv/bin/python -m apps.worker consume-review
.venv/bin/python -m apps.worker consume-export
```

普通模型每次外调前先原子登记model_calls并检查当前generation/fence/owner/live lease/取消及预算。一次请求的准备和评审共享最多10次、最大输出token预留总和32768，单次不超过8192；SDK没有隐式重试。成功结果有实际对象SHA，可重放复用；reserved/unknown不自动补发，结果迟到不能越过取消或fence发布。实际usage/requestID用于追溯，不以普通chat接通授予正式评分资格。调用资料不超过明确字符范围，超限失败并列缺口，不截断后声称全文已读。

核心核对覆盖完整已解析正文，不用普通RAG片段代替完整评分。已知critical跨轮保留且按来源身份去重；新轮零发现、失败、关闭可选检测都不解决旧问题。只有有理由驳回或字节确已改变的新稿对同一有效规则完成明确复核才关闭。四层覆盖分别为source_scope/core_extraction/judgment/scoring，人工确认单列；不以运行比例冒充真实漏提率。正式评分/三次判断及模型准入尚未在该核心切片完成，不生成假分数。

配置本地解析端口时，新入库派发document/2并将其parser/config摘要绑定input_digest，服务身份改变会拒绝旧任务重放；旧document/1保持保守字层文字提取与真实缺口。新端口提供真实OCR/字层段落/规则网格表，物理页、bbox、结构及partial/gaps原样入库。扫描版式、图纸理解、复杂表合并和生产解析基线仍有独立验收范围；没有能力就不标完整。CPU端口不是Docling/Paddle已准入。

Kafka真实手动offset、固定group、每分区单在途；发布收据/effect/Outbox/audit/SSE同事务，外部网络在bid锁外。多阶段依赖完整登记，必需步骤全部成功才结束整轮。失联先废旧fence，再升代次；依赖失败/进程接管共用3次阶段尝试，最终持久失败和死信。未来/未发布同代Outbox不被对账抢跑。普通执行器拒绝研究处理器，研究执行器仅用研究角色。

开发机制验证示例：

```sh
.venv/bin/ruff check apps/api apps/worker packages/domain
.venv/bin/ruff format --check apps/api apps/worker packages/domain
.venv/bin/mypy apps/api apps/worker packages/domain
.venv/bin/bandit -q -r apps/api apps/worker packages/domain
.venv/bin/pytest tests/test_providers.py tests/test_domain.py tests/test_review_identity.py tests/test_actual_contract.py
RUN_MODEL_LEDGER_INTEGRATION=1 RUN_REVIEW_INTEGRATION=1 RUN_RECOVERY_INTEGRATION=1 RUN_CANCEL_INTEGRATION=1 .venv/bin/python deploy/run.py --env-file .local/backend-checks.env -- .venv/bin/pytest tests/test_model_ledger_integration.py tests/test_review_integration.py tests/test_recovery_integration.py tests/test_cancel_integration.py --assert=plain
RUN_SLICE_INTEGRATION=1 .venv/bin/pytest tests/test_slice_integration.py --assert=plain
```

机制库不能启动Kafka消费者/relay与其它库共group。评审纵向机制使用真实PG/S3/ASGI及明确MockTransport，直接驱动权威Outbox，不作为Kafka或模型效果验收。解析HTTP另用RUN_PARSING_WORKER_INTEGRATION=1显式启用，配置私有不打印。真实Kafka T0另测；真实模型业务、完整A1人工确认/补漏/事实/提交及生产恢复容量仍需后续独立测试，不能由开发自测替代。

A1候选02追加了人工写入子集：已有规则单条confirmed/deferred只改变确认资格，不自动增加模型调用；有理由rejected追加新规则包并保留被结束条款及原决定。显式勾选的pending规则可整笔确认，任一条款失效则全部回滚。人工补漏为一次“补充并确认”，新包/真实人工确认/等价原决定沿用/旧轮过期同事务；迟到提取不得覆盖人工新包。事实新增/补替为一次“保存并确认”，撤回和确认推进不可变事实集，决定沿用须字段及证据效力严格等价并留audit映射，读模型显示原决定与carryover，来源变化回pending且原确认历史保留。事实依据的原件/页块/来源祖先也加入评审冻结根，缺真实依据不具备当前资格。

候选02还补充GET项目builds的真实游标分页及跨浏览器发现。项目列表先筛选profile/stream/review_slot齐全的业务单元再分页，不让独立数据库机制夹具中的base-only行拖垮全部项目；坏单资源依然404。原对象不删不修。研究新增来源资格SQL缺失时明确未配置，不能把其观察代码当作当前数据库可执行验收。补遗整份/局部效力命令、build follow_up自动采用、独立一致性/残留检测、gate/提交/报告及FR034主体复用仍需后续候选，注册的人工接口不表示整个A1已完成。

跨命令的同输入reserved/unknown调用同样阻止新收费：控制面按准备/评审内容身份阻止重排，最终调用账本再按同项目、stage逻辑键、实际request/profile摘要核对跨operation的未知调用。执行nonce本身不授予重新付费资格，原账目不改写。

候选03增加显式补遗效力卡：GET source-effectivity后，以该投影强ETag提交整份替代、局部替代或保存待核验。逐字原页与实际原件字节、来源资格、包头及影响范围均校验，失败不写部分包；机器校验证据一致性，不冒称理解全部法律范围。局部只结束所选义务；整份仍由其他文件共同支持的条款保守待核验；明显“其他条款不变”等局部引文拒绝整份动作。合法保存后新包和新增人工确认一次完成，需用户明确复评；重复未决说明仅留note。已结束条款的精确语义和证据身份保留效力审计，后续提取不能将其复活；RAG可用具体evidence_impacts在最终使用前拒绝将旧义务当现行要求，历史原页继续可查。

03的构建接续由独立API角色运行`python -m apps.api.build_coordinator`：只有成功PDF、同构建Word的verified controlled_render关系、真实字节及构建时冻结的全部head／草稿／快照仍一致，才同事务采用正文、生成新资料快照、排解析及统一持久评审准备请求。生成期间编辑或从历史草稿构建只保留历史产物并显示needs_selection，Word成功不能掩盖PDF失败。Build.follow_up的adopted表示采用并登记请求，review_id只有真实冻结轮次才出现；不等于核对完成。none不因项目auto_review而改为评审。该进程不调用生成模型，但后续review控制面／消费者仍需遵守独立的收费授权与预算边界。

03同时接入FR034显式主体登记／默认／工程轮次选择和有据纠正，创建请求必须携带实际选中的主体ID才绑定已确认主体，未选择仍为占位；不按近似名称或默认值暗绑。Bid.identity和新评审证据根冻结稳定身份，纠正使当前评审资格失效但不重写旧包／提交／成绩。规则明确批量上限500。跨material复用提取时保留父包到真实extraction effect的可核查审计关系，同输入再次请求不重复安排提取。Gate／标准实交／独立线下事实／报告、独立矛盾与范围残留检测仍待后续切片；研究缺新资格SQL时能力明确不可用，不启动研究或普通收费消费者来假装验收。

04候选继续实现核心提交闸门与真实实交事实。GET `bids/{id}/submission-gate`检查当前输入/来源/三层核心覆盖/人工确认及所有未解决关键风险，评分独立；正文缺失返回null和具体阻断。标准POST `submissions`持当前Bid强ETag、幂等键及实际稿/依据附件，回读原件SHA，再锁内重算闸门；不把提交之后补做的评审写成事前通过。独立`submission-facts`忠实记录已发生错传或未完整检查事实，不经过闸门、不生成通过标签。已有实交只能通过`submissions/{id}/corrections`显式追加更正并CAS移head，原事实不改；项目/单条/稿件列表均可服务器刷新恢复。新证据固定`identity`、完整登记阻断快照及FR011狭义风险快照，缺提交前权威观察时计数unknown/null，不补今日0，不推断官方否决因果。`evidence_ref`当前明确为本项目已核验document_version附件，始终标人工声明且`platform_submission_verified=false`；具体产品收口待Owner复核，见docs/a1-submission-report-product-review.md。

报告先GET `reviews/{id}/report-context`获取本轮真实受检稿、展示/来源修订及强ETag，再POST `report-exports`。真实生成Word/PDF评审报告和PDF原页文本批注；DOCX批注暂不支持。PDF文本note只给真实物理页范围，无法定位正文则首页索引明示，不能当逐字高亮。冻结报告包含四层覆盖、规则解释/人工状态、风险生命周期、逐字证据页、历史受检身份及不可用/隐藏预测，文件用途始终`review_report`/`annotated_review_copy`，不移动当前正文。展示投影内容改变时，GET只读投影下一展示修订，POST在bid→graph锁内分配并审计；同context/修订可复用既有任务，迟到发布仍重验context/fence，失败可按新政策重新生成。历史产物保持可下载；列表和单GET可跨浏览器恢复。转换沙箱未认证缺口保留，不以LibreOffice可运行授予生产沙箱资格。

04还提供GET `bids/{id}/upload-batches`真实批次发现、UUID稳定分页和状态筛选，不依赖localStorage或伪造创建时间。报价F03、FR011指标与知识索引router/handler为各Owner独立模块接线，仍受各自配置/独立验收闸门；旧库无新研究/知识RAG表时不得owner热补或假称已配置。FR004独立跨章节矛盾、FR005范围残留、完整两轮比较和正式三次评分资格仍未完成，普通核心判断与开关存在不代称这些功能。
