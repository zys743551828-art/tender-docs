# 训练治理SQL草案C静态复核

结论：本SQL可进入全新独立库初始化，无剩余静态阻塞。此为静态放行，不代表SQL已执行、受限LOGIN合法路径或业务资格已验收。A/B原记录和原阻塞保留。

输入draft-C/model-governance.sql SHA256 `95cd518b35f88cc7208d6add50dc0b19bc1297715169a549a9252c023da85ef0`，逐字保存在observed-model-governance-C.sql，交接范围另存并绑定摘要。无既有设计表／角色增权；只新schema、三CPU用途组及API-only映射。

## 两项原阻塞的静态闭合

MG01：assert_lease在current_cohort获取资格锁后重新SELECT当前job FOR UPDATE，重查当前kind/用途/状态，并用当前generation核lease.owner/fence/generation/有效期；renew复用同守卫，不凭旧租约续活。claim原锁后重读继续保留。需fresh实证旧代在图锁等待期间被改代后read/renew/finish全部拒绝。

MG02：新增API-only不可变chapter/parse/evidence bindings，cp绑定真实提交review/body与冻结规则包；每个parse核最新、完整页/无缺口、原件blob/node、直接父边和source闭包；evidence核项目、parse、物理页、ordinal/digest。正文全证据、必要评分规则集合、规则依据及官方核验依据均比较精确集合。身份复核实交冻结snapshot、实际authority、最新epoch及完整association，不能同UUID改地区/轮次名称后继续用。

canonical UUID由可信API冻结共享RAG helper，SQL实际核原件/parse/node/父边；不允许CPU自报来源。完整原始三判断和FeatureBuilder清洁性仍须API导出实现核验，不能只凭这些FK推定真实训练资料完整。

## 时间、曝光与权限

known_at与系统资格窗已分开：prebid知道时间不晚于submitted/chapter as_of；official_label知道时间不晚于成绩known_at；全体不晚于cohort冻结。head窗口在冻结chapter as_of或cohort创建点及当前有效，不倒要求2019资料在2019已具有本系统2026发布头。fit/development/calibration标签及提交严格早于test_start，final提交不早于边界，跨用途相关组／项目／同正文／模板／近重复检查保留。

open_final从服务器真实samples完整相关组推曝光成员，同时current_cohort扩大有序bid锁范围。按sample/group/project/body/template/near全局唯一，换实验、manifest顺序或组内子集不应绕过。read_final仅接受非当前顶层事务创建的暴露：需先提交暴露再读标签，回滚打开事务不能先取得标签；已提交记录不可删。

train/eval无表SELECT/写入、无bucket key，仅目的匹配且owned lease的受限RPC。所有函数固定search_path，PUBLIC撤销；score仅schema USAGE，尚无业务执行/读取能力。API仍是可信编排边界，能读写导出映射，所以实现必须提供真实purpose过滤及CPU白名单环境，不能继承通用.env/API/owner/S3/provider。合成origin跨protocol/cohort/model不可升级，finish要求formal_eligibility=unavailable；无正式发布能力。

## fresh阶段必须执行

由PM以owner仅建全新库/LOGIN，业务测试用非owner：两用途正向及互相越权/任意表/标签拒绝；同事务open/read拒绝、提交后读成功、rollback/失败/取消不清曝光；组内不同子集及跨实验仍重复曝光拒绝；旧代等待锁后拒读/续/提交；新parse但head不变拒绝；同UUID关联变化拒绝；精确body/rule/official evidence、完整必要规则、历史资料现在发布与未来known/候选/撤回的正反例。另证明CPU/HTTP/对象期间无挂事务。

API冻结须核源研究run结果真实成功及三独立有效判断、必要评分规则的实际有效集合、相同输入FeatureBuilder/时间划分、无final标签训练泄漏，不能将worker自报JSON报告直接授正式资格。当前SQL只提供训练/评测存储/RPC，不覆盖正式验收发布/影子/score handler，也不证明业务误差、覆盖或区间资格。
