# dev远程CI原生权限修复

首轮dev提交288b458的[CI 40908765787](https://github.com/zys743551828-art/tender-app/actions/runs/40908765787)已终态failure：4成功、3失败、1取消。成功为非owner API、fresh schema/SCRAM、固定来源编辑/Word、前端；失败为dispatch、scoring、recovery；默认后端因超过20分钟上限取消。GitHub check注解明确超时，不把取消当测试通过。

## 定位及影响

远端日志重定向到Actions存储地址时返回Forbidden，未绕过网络，也未重试Webhook403。通过固定PG17镜像和校验过的SeaweedFS4.48，在禁外网容器中调用原extended_recovery_runtime复现material恢复失败。跟踪器只输出方法名与审计字段差异，不改权威函数：material_execution.live_send_current的caller_execute期望false、实际true。

原SQL遗漏对内部helper的PUBLIC EXECUTE撤销。原审计正确拒绝，不能改成接受true。此文件在来源7d3ed69与合并288b458完全一致，属于本次dev CI暴露的来源缺陷，非四个前端冲突处理导致。远端scoring/dispatch也在调用相同审计的原生步骤失败；因远端堆栈不能下载，其同因判断仍须由修复后CI验证。

## 修复与版本

- 原material-execution-attempts.sql字节保留，SHA256固定49bc4c53da9c041c1f038a1521ea638a6002570295204c5b933b1be166b00f21。
- 新全新安装文件material-execution-attempts-v2.sql仅补该helper的PUBLIC撤权；不授新调用权限，不改发送、预算或结算规则。Python执行方法显式推进/3并绑定新SQL；依赖方法摘要相应变化，旧profile不被静默重签。
- 当前完整恢复格式/3、预测私有对象扩展/4采用修正SQL。旧/1、/2归档仍按原文件清单验证，禁止新旧清单混用；helper_acl=false仅用于历史清单核对，当前snapshot不允许降级。恢复仍隔离只读，不恢复旧队列、不授生产资格。
- 原生回归直接断言PUBLIC无EXECUTE、受限Worker不可直接调用内部helper，现有严格审计不放宽。归档回归绑定旧文件摘要并覆盖四格式接受准确清单、拒绝错误清单。
- 默认后端job从20增至45分钟，包含依赖、OCR资产和web构建预算；命令与测试集合不变。其它job不改。不是以取消或过滤用例掩盖错误。

## 当前验证

修复后原完整恢复入口6通过/0失败/0跳过（schema4、authoring1、material1），原scoring入口21通过/0失败/0跳过，包含新增私有helper断言；归档兼容切片22通过，执行/恢复定向切片49通过。全仓Ruff/format/mypy/Bandit通过。容器只运行临时PG与S3，schema和原件为显式合成，未连生产、未调用真实模型；本地PostgreSQL来自仓库固定镜像，与CI恢复任务的Ubuntu17.11包环境有差异，远端终态必须另外确认。

预测原生最终8通过/0失败/0跳过（42.15秒），含当前/4恢复及Worker/API重启拒绝保护。本地默认全量4663通过、2433跳过、0失败/错误（969.86秒）。其收集后新增的4个归档兼容用例由22项切片独立覆盖；远端最终提交的默认全量亦成功，覆盖最终文件状态。修复提交a703f35f75013cb238a36c6235a8aac858f42da3已普通推送dev，新CI终态success，8项全部通过。详见本页证据目录dev-ci-acl-20261029。


## 最终远程终态

应用dev远端核验为a703f35f75013cb238a36c6235a8aac858f42da3，工作区干净；[最终CI 40913317582](https://github.com/zys743551828-art/tender-app/actions/runs/40913317582)已completed/success，与该准确SHA绑定。未手动重试旧任务，正常修复提交自动触发新运行。旧首轮失败及取消记录保留。

| CI任务 | 最终状态 |
|---|---|
| Backend quality and default tests | success |
| PostgreSQL 17 non-owner API tests | success |
| Material request preflight and execution ledger on PostgreSQL 17 | success |
| Material outbox dispatch on PostgreSQL 17 and Kafka 4.1.2 | success |
| Fresh authoring schema and independent SCRAM role permissions | success |
| Fixed-source editing and Word export on PostgreSQL and S3 | success |
| Extended-schema backup and quarantine restore on PostgreSQL 17 and S3 | success |
| Frontend quality, tests and build | success |

原始终态元数据见[final-run.json](fusion/dev-ci-acl-20261029/final-run.json)，本地分母见[local-full-summary.json](fusion/dev-ci-acl-20261029/local-full-summary.json)。原生恢复6、scoring21、预测原生8均零失败零跳过；这些是隔离合成机制验收，不是实际付费模型、指定Word03转换、生产恢复或真实模型准确性验收。默认跳过项继续未验收，不能用总CI通过替代它们。

现有tools lint及研究格式问题不在本工作流扫描范围内；本次没有改动这些无关文件。远端日志存储下载Forbidden的限制仍存在，未扩权或绕过，但不再阻塞本轮终态核验。Webhook403未重试；未部署、改保护、创建PR或运行付费模型。
