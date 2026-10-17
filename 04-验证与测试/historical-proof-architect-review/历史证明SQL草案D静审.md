# 历史证明SQL静审

结论：D需先修HIST01，再以新冻结稿复核，暂不作最终fresh输入。被审[原字节副本](observed-historical-draft-D.sql) SHA256 `12c412c3f8f097c3cdbfdff000854e2bc0ba39937c575d53ffe21f08391932d6`。本轮只静审，与产品协议c951503…核对；未执行新DDL或数据库业务、无付费。

## HIST01：确认主体未核所选轮次真实归属

定位：[subjects字段与CHECK第11—24行](observed-historical-draft-D.sql:11)、[confirmed守卫第183行](observed-historical-draft-D.sql:183)。bidder、round、engineering_project的单列FK各自有效，JSON只核对应ID相等；confirmed进一步只检查非null和证明资格。

最小静态反例：实际登记R2属于工程P2，subject选工程P1、轮次R2，snapshot逐字填相同P1/R2并有合法的精确引用证明。各FK和现有CHECK成立，守卫没有round.engineering_project_id=P1的条件。此处人工证明不能覆盖数据库已明确的轮次归属矛盾，后续历史归组/作用范围会自相矛盾。[合成反例](historical-D-typed-identity-countermodel.json)隔离了该缺失谓词，未执行SQL，不能写成真实数据库复现。

建议在confirmed守卫核真实round→engineering_project关系，并对snapshot的typed round_no/lot与选定轮次作JSON类型及值比较，null统一；保存pending仍可无完整主体。历史名称、标题、地区等证明快照不能再拿当前bid_profile替代。Owner已确认修新稿；D原字节保持。

验收：真实非owner API选P1＋R2确认拒绝，0新增decision/head；round_no为错误数字/字符串、lot不一致拒绝；正确历史主体＋有效证明可确认；空身份可保存pending；修改今天profile不改已冻结历史名称。API不得以当前profile回填历史unknown。

## 其他已核边界与后续条件

首个decision后facts/proofs/proof_heads禁止新增，保存pending也封集合；子行trigger先取得同91143→submission bid→图锁再查decision，可防确认和追加子行并发扩张已确认集合。后续编辑追加supersedes当前头的新版本；跨scope或旧版本不能直接替换当前头。仍须fresh实际事务竞争验证，不把源码存在当锁实证。

subject固定真实submission/body/digest/提交时点；claim同bid/document字节摘要并明确fact kind及unknown，known/received须当前confirmed subject和有据证明。当前proof核精确parse/evidence/页/逐字、最新parse、全祖先集合及head/digest/policy/known/效力窗，不倒写业务原件或系统known时间。parse-source canonical UUID由API共享helper构造，SQL未重算UUID5；这是明确的受控capture信任边界，后续API测试须包含旧parse子source挂新evidence拒绝，不能声称SQL独自证明全部映射。

decisions与heads由受限definer写，API不能直接铸造confirmed头；固定search_path、PUBLIC撤销、仅API执行两个命令。研究/CPU/score/普通Worker无新schema原文或资格读取权。新input/chapter结构仅私有追加数据，没有可执行input qualifier；本稿不能开启回测或formal资格。后续资格仍须完整范围、历史可见证明、身份/组和当前authority头全重核，模型content与证明/标签隔离。

未实施的历史输入资格链不作为本次DDL缺陷；应维持不可用能力门，并在完整受限qualifier另审前禁止借这些表进入原研究路径。SQL语法、真实角色合法路径、pending恢复和锁竞争尚待新候选fresh独立验证。
