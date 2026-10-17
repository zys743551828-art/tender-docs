# Word主桥接结构复核

结论：本候选的Word接收→转换→API采用→PDF解析桥接，在所读范围内无新增架构阻塞。可以提交PM作本切片集成决策；本结论不代表完整Word交付检查、RAG质量、正式评分或生产恢复通过。

输入 [candidate-01](../../../../tender-workbench/docs/word-mainflow-development-checks/candidate-01.json)，SHA256 `9b609a89975c31ab945aacbbe8f63639edd37d51b622ee92c788146270cd7168`；实际执行根为候选指定runtime-preview-02。335份固定文件在独立检查前后全部匹配，21份自有文件与另存快照匹配。actual OpenAPI精确摘要 `e1f7c5389386d7a9224ffc469bd878500a79e819780fee8c581781f73758e602`，165路径/192操作/315Schema。输入和验证方式见 [result.json](result.json)；没有使用moving源码或更改live。

## 关键结构结论

1. **入口与原件身份**：single/multipart/保留字节重新接收路径共用格式判定，同源Content-Type和前缀来自不可变服务器upload.filename；核验仍以完整实际字节SHA、尺寸和Word03 inspection为准。new_version和commit检查逻辑文件原格式，不能把Word版本插进PDF文件链。默认Word新接收/修改关闭503；历史只读不以关闭开关抹掉已存原件。用户补传PDF仅写同bid/同role的user_declared_same_draft/unverified，不借相同blob继承受控关系。

2. **新Word不得借旧PDF资格**：[main.py:869](/workspace/tender-workbench/docs/word-mainflow-development-checks/runtime-preview-02/apps/api/main.py:869) 在材料提交事务内将Word body选入当前material_assignments、明确清空current_body_id和current_review_id；旧PDF和评审保留历史。reviews.on_material_commit使尚未冻结旧请求superseded，不再借空body继续旧请求。pending_body来自现有材料/文件head/实际DOCX类型，没有另建竞争业务头。Word parse只留下明确未转换缺口，不伪造可读块或向量。

3. **Worker发布与API采用分工**：Word handler在关闭读取连接后取原件、转换、写产物；共享engine的document lane、代次/fence/取消/生命周期/显式结束批次守卫先于publish。Worker只能登记产物/关系和一次转换收据，不写current_material/current_body。完整受控关系要求controlled_qualified，partial产物保留但不能被completed_pdf采用。API协调器tick与commit早完成路径复用 [word_materials.adopt:177](/workspace/tender-workbench/docs/word-mainflow-development-checks/runtime-preview-02/packages/domain/word_materials.py:177)，没有Worker借API角色的问题。

4. **采用CAS与真实字节**：API预读完成后锁外GET候选PDF，核尺寸、全SHA和PDF前缀；进入短RC事务取得bid锁后，重读最新转换候选并逐项摘要比较、核当前profile及选择上下文。上下文含Word文件head、当前材料/设置/规则/事实头、Word SHA与角色；body还要求current_body为空。新稿、人工PDF、材料变化或profile变化使旧输出只记录needs_selection，不抢当前正文。材料新快照、指针、PDF解析Outbox、auto_review意图与采用审计同事务完成。协调器对象GET无业务连接；commit预检GET前rollback，业务写锁在后段取得。现有retained对象HEAD发生在预检事务内，但未持bid写锁；不将此片称为所有对象I/O均零数据库连接。

5. **来源链与撤回**：[word_lineage.ensure_controlled_ancestry](/workspace/tender-workbench/docs/word-mainflow-development-checks/runtime-preview-02/packages/domain/word_lineage.py:13) 在bid→图锁顺序下验证真实受控关系和两端blob类型/SHA，使用共享original_source_id形成Word root→PDF root→parse child的祖先链。Word root只提供来源，不建立文本chunk；证据依旧来自实际PDF物理页。既有Word或祖先withdrawn、未来known/效力窗或非法用途拒采用，不通过重新解析复活；官方结果继承official_label。图锁保持到调用事务提交，采用/解析发布不会在撤回后无锁写入。尚未登记任何来源head的原件如何撤回不在当前新UI能力中，QA撤回场景明确先建立真实祖先，再撤回，不能扩称所有上传前后撤回入口已齐全。

6. **与评审/RAG的边界**：转换complete只表示对应转换收据具备资格；真正可读来源仍须实际PDF解析，完整材料/页覆盖与正式评分干净输入/完整相关章节/三次判断仍走原冻结协议。Word原件不存在模型直接读取的替代通道；无需向量完成即可发布真实可读页证据，但不能以原件root已published宣称已完成全文核对。

## 独立证据与未验证范围

本轮独立执行5个离线检查：实际字节准确、同尺寸SHA篡改、尺寸变化、SHA自洽但非PDF前缀、默认接收503；前4项使用明确内存字节端口。过程禁止DB连接、网络及子进程，无Docker/OSS/模型/Kafka/DDL或服务升级。没有重复Word03域检测和W04排序大全。

另只读核对并绑定测试角色的 [11场景最终结果](../../word-mainflow-independent-checks/candidate-01/final-result.json)，SHA256 `b5ea12903f381722d1da765063a84427cef131f91d6bbfc75cbcc9a7c1087529`，执行源码/JUnit/observations摘要逐项匹配。该角色实际用非owner PG、真实会话ASGI、Memory字节、真实无网Docker与本地CPU解析验证早/晚采用、取消/关闭、原件与PDF回读、迟到不抢、撤回及最新失败不回旧green；它不是我本轮重新执行的测试，也不是实际OSS/Kafka或浏览器验证。开发额外一次真实OSS链只保留其原报告身份，不计成QA新增证据。

剩余验收：独立Kafka进程恢复、实际OSS与浏览器主流程、复杂真实Word保真/缺字体语料、完整必要来源覆盖和正式评审链、生产渲染容量与恢复。测试中的最新失败为显式disabled-render端口，撤回为已建立祖先夹具，不能冒称自然renderer失败或未登记原件撤回均已实测。当前live仍原PDF-only固定版本，本报告没有改动或升级它。
