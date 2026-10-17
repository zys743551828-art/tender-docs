# 技术总满分权威SQL静审

结论：B有一项历史来源集合约束矛盾，需修正后再进入独立新库验证，B不fresh。问题阻塞带不同原件祖先的历史满分权威，不表示当前用途或所有历史PDF均不可用，也不是已经执行证实的越权漏洞。原A/B字节及A吞尾句反例保持原样。

输入SQL SHA256 `3fdce8d604ad1f17bf324330b7d15078b8cc063fa883eaa201852e67d48e9674`；产品协议 `2a2dec284f3013897abebf50318de749a32a82eff15a907608510b0b4cf1ad73`、PRD `16f776ef3115f9bace2ff7cf63bfff6652770199c4432e72965bb1369a693297` 均匹配。完整路径和摘要见 [review-record.json](review-record.json)。本批主要审SQL及产品实施口径，附带读取API同一集合条件，不是八源应用验收。

## TS01：直接证据集合与完整祖先集合被混为一套声明白名单（P2，阻塞历史来源模块）

位置：[qualified_version第129行](/workspace/tender-docs/04-验证与测试/research-backend-checks/technical-scale/sql-draft-B/technical-scale-authority.sql:129) 要求claims.document_version_id集合等于直接technical_scale.evidence文档集合；[第168行](/workspace/tender-docs/04-验证与测试/research-backend-checks/technical-scale/sql-draft-B/technical-scale-authority.sql:168) 又要求每个来源祖先文档版本都必须在claims中。API capture也在 [scale_repository.py:262](/workspace/tender-docs/04-验证与测试/research-backend-checks/technical-scale/sql-draft-B/runtime/packages/model_governance/scale_repository.py:262) 限制选中声明只等于直接证据文档。

场景：系统已支持受控Word招标原件→转换PDF→parse。满分原文有完整PDF物理页证据，直接证据文档集合为{PDF}；真实祖先文档集合为{PDF,Word}。只选PDF声明，第168行拒；选PDF与Word两个声明，第129行及API拒。给Word伪造直接可读证据不能修复，因为真实桥接不创建Word evidence/chunk，确认守卫还要求完整有效parse、真实页/文本与原件。

独立证据：[ancestry-countermodel.json](ancestry-countermodel.json) 枚举两文档的全部4种声明子集，无一满足两条集合条件。驱动 [ancestry_countermodel.py](ancestry_countermodel.py) 仅做集合推导并核SQL SHA；没有执行SQL、构造业务库或声称已PG复现。对于没有不同文档祖先的普通PDF，这个反例不成立。

建议：服务器从所有直接evidence.source_id推导完整source_ancestors的DISTINCT document_version_id全集，与claims做精确双向集合比较；再对每个映射逐版本核同bid、submission、Subject、实际blob digest、当前confirmed decision、current_proofs与content_existed/known_to_bidder/effective历史时点。不能只删除第129行或只检查“有map”，不能让API提交任意祖先白名单，不能给Word伪造可读块。SQL与API须同步。

验收：另新库中完整真实ParentWord→leaf PDF→parse、两版来源的各自有效声明能够确认；遗漏/多余声明、错误Subject/提交、错误digest、祖先声明pending/撤回/过时、事后known_to_bidder或有效窗不覆盖提交均拒；普通PDF正向保留。来源集合、Subject或决定头在确认/冻结竞争中变化应拒，旧权威和引用历史保持可还原。

## 本次静读核对的其余边界

- A的自由scale_description改为完整原文块的两种正向量尺token（未加权原始分、已加权技术总分），整句锚定；不能靠原fragment或吞入“但技术总分现为80”尾句授权60。此为代码差异核对，未执行PostgreSQL regexp函数，不把31项开发pure算成本轮通过。复杂声明可pending属于明确首片范围。
- technical_maximum有限、有界、正Decimal；没有从条款合计、权重、标签、最高已见分或默认100求最大值。直接证据限同项目当前材料的tender/supplement、prebid_content来源与完整块逐字SHA；解析完整、真实全部物理页1..N、精确原件/parse/page/evidence、最新parse及冻结祖先头逐项校验。canonical UUID5仍是可信API共享helper构造边界，SQLtyped映射不冒称重算UUID5。
- current_prebid以服务端created_at为本权威观察点，同时检查当前known/发布效力窗；真实当前身份authority和完整关联匹配、招标/补遗集合变化使旧权威失效，无关正文变化不强迫重复确认。历史用途另绑submission、当前confirmed HistoricalSubject及证明，不套当前profile冒造当年身份。历史业务事实以submitted_at为界，而系统现在发布head只核真实当前窗口，未要求系统2019已经有2026发布头。
- created_at不授API列写权，决定时间在definer内clock_timestamp；版本和证据映射追加，首个决定后禁止再加子行；head更正/撤回由限定RPC带expected_revision与supersedes串行CAS。锁序RC→91143→bid→graph，来源/决定变化不得只靠旧快照继续通过。
- 聚合仅unknown/unsupported、没有可执行表达式；schema及表/函数仅给API最小入口，Worker/研究/训练/评测/score无新增权限。SQL不修改现scoreF、不暴露标签/桶、不建立满分输入/影子数值或formal资格。current已关闭项目仍拒普通权威写入，历史专用权威可追加而不移动原项目/实交头。

本轮实际执行只有文件读取、SHA/diff与离线集合模型；无数据库连接、DDL、新账号、外部I/O、模型、对象或消费者。除TS01外未见新的静态阻塞不等于SQL语法/权限/事务已运行正确；修后仍须PM另fresh，由受限角色验证合法路径、禁止路径、声明词法及历史正向，然后独立审应用候选。
