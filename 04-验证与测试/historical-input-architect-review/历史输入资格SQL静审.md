# 历史输入资格SQL静审

结论：草案B可交PM在独立新库初始化并进行机制验证；本结论不授予应用、历史输入执行、模型或正式评分资格。B的SHA256为 `546ba834b0dfe863ddcf0a37ab89d5d94c1f94e99a050d2822cb1e0a3f49db2b`，路径为 research-backend-checks/historical-input/draft-B/historical-input-qualification.sql。没有对任何库执行此DDL。

草案A（SHA256 `2268c35a4822bf2adef4663485a439317ca6776e65d2ed307066dfe7d0831510`）第147—148行只比较页数、usable和gaps。page_count=2而物理页为{2,3}或{1,3}仍满足，expected_chapter第75行却将现有页集合包装为full-body，可能遗漏第1页或越界页。底层pages表仅保证页码正数和parse内唯一，不能补足完整范围。独立非owner PostgreSQL只读标量反例见 pages-countermodel.json；该证据没有创建伪输入，也不是整个seal_input函数反例执行。

B唯一业务差异在第148行补全部页码位于1..page_count。既有正页数、主键唯一、总数相等与此约束共同保证集合恰为1..N；已核对A／B精确差异并保全A。新库应验合法1..N通过、缺首／中页、越界、页数不符拒绝；应用应在对象写入前同样拒绝。

已静态核查的权威链包括：READ COMMITTED前置；91143→bid→图锁后重读可变head；历史提交、正文、主体当前明确确认决定与精确资料集合；每份资料的历史content_existed／known_to_bidder／effective证明；当前proof与完整祖先heads；最新完整解析及精确全文／评分条款evidence；服务端逐字段重建干净章节JSON并与存储字节摘要、尺寸比较；封口后禁止追加输入子行。历史业务事实时间与当前系统发布／核验观察时间分别核查，没有倒造旧系统发布头。

seal只能由受限函数写入，API只获seal/current入口；PUBLIC执行权撤销，无Worker／CPU内容读取或任意输入执行授权。canonical UUID5、包摘要及解析真实语义仍有明确API／解析生产者信任边界；静审不声称数据库重新证明这些能力。origin含synthetic时保持synthetic，其余只允许uncertain，不由客户端real声明授资格。

仍待新库实际验证：DDL可执行性、合法完整输入正向封口及重放、逐项篡改拒绝、封口并发与撤回竞争、真实角色权限、完整应用对象I/O与事务分离。本次零费用、零对象请求、零消费者、零DDL；按历史提交时间留出的回溯研究模式尚未纳入该SQL执行审查，不改变既有严格训练评测协议。
