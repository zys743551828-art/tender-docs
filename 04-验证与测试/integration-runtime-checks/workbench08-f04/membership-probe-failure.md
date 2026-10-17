# 成员列表探针失败说明

首次只读组合探针已通过HTTP、310源、7进程与前端资产比对，但Kafka成员列表出现 `_DESTROY`。后续只读诊断保存在 membership-readback-followup.json；此失败不删除。

原因是探针把 AdminClient 写成临时对象，异步Future等待前其生命周期已结束；持有client引用后重跑成员列表及完整组合绑定。不是broker丢数据或消费者验收失败，也不能用此说明替代实际重跑结果。没有业务写入、DDL、对象操作或模型请求。
