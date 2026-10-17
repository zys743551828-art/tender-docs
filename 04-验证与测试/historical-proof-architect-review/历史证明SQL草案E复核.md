# 历史证明SQL整改复核

结论：E在proof/subject追加与受限人工决定的边界内静态通过，可由PM在全新隔离库初始化；不热改旧库。输入[原字节](observed-historical-draft-E.sql) SHA256 `e811920d92478fd6e20a9b0d200c2769396eae92deee142a0cc2fbafd0086c44`，逐字比较D→E，本轮未执行DDL或数据库业务。

HIST01静态收口：confirmed守卫新增实际round所属工程的比较，snapshot.round_no以JSON数值与真实轮次相等，lot以JSON值精确比较且SQL NULL统一JSON null；数值字符串、跨工程轮次、错误标段不符合。名称/标题须JSON string，仍保留历史快照，不与今天profile名称/地区强制相等。pending无完整身份的路径未被收紧。

D的其余被审机制没有变动：真实实交/正文身份、proof typed页和证据/祖先当前头、时间不倒造、首个决定封子集、supersedes当前头及91143→bid→图锁、API受限决定与其他角色无读取/执行权限。input/chapter仍仅私有结构，无input qualifier，不授历史执行或formal资格。

fresh必须实际验证原P1工程＋P2轮次反例拒绝且0decision/head、typed round_no/lot负例、合法历史名称不等当前仍可确认、pending保存及追加新版本补证；同时测确认与子行追加竞争、未来known/撤回/旧parse混挂拒绝、直接铸造头和Worker读拒绝、RR拒绝及正常API合法命令。canonical source UUID由API共享helper构建的信任边界继续保留，不声称本SQL自行重算。静审不替代这些数据库证据。
