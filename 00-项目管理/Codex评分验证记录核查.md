# Codex评分验证记录核查

项目是全新建设，当前 dev 和 Codex 是两版候选代码，不存在必须迁移旧业务数据的前提。选型应比较领域模型、功能完整性、实际质量与验证结果；已运行演示环境不赋予任何一版必须被继承的架构地位。

Codex 分支确有实际分值实验、评分执行独立验收、统计训练评测及技术满分权威验证。其记录同时区分真实案例探索、合成样本、Mock 模型调用和正式精度资格。准确结论是“评分经过多层验证，真实独立官方成绩精度尚未验收”，不能概括成“评分没有验证”。

## 1. 分支与核对范围

只读核对远端 heads，归档分支与本地 codex-handoff 一致：应用 `27bc63909dd176676ea918beeee745daebdb8f6a`，文档 `f1e5c36c1a818668c3f61a111a0443494515bae9`，研究 `a32229fd13fcce6c075ad04d811df33594df59ce`，资料 `abc657ee594f5ef46f57265d1f65e67e36270f8d`。没有发现另一个更新的远端 Codex 候选分支。

本次读取归档原始报告、结果 JSON 和 JUnit；评分执行和 TechnicalScale 的 final-result 字节 SHA-256 与交接 evidence index 完全一致。没有重新调用供应商、启动测试环境、执行 SQL 或将归档结果称为本次重跑通过。

## 2. 真实案例分值实验

CASE-001 输入包括 219 页招标文件与 50 页技术标。实验实际调用模型，保存请求、响应、原件 SHA 与引用检查；属于研究侧整份正文评分探索，不是新 workbench 正式评分执行器的端到端精度验收。

| 实验 | 实际返回分值（技术满分 5） | 其他结果 | 原记录 |
| --- | --- | --- | --- |
| 全文基础组 | Flash 三次 4.5；Pro 一次 4.5 | 4 次均返回数值；引用校验另列，分数重复不证明准确 | [baseline](https://github.com/zys743551828-art/tender-research/blob/a32229fd13fcce6c075ad04d811df33594df59ce/results/llm-fulltext-baseline.json) |
| 页／证据 ID 绑定组 | Flash 一次 4.5；Pro 一次 4.2；思考 Pro 一次 4.2 | 另两次返回但拒绝估分、一次失败，不能只保留成功数字 | [id-bound](https://github.com/zys743551828-art/tender-research/blob/a32229fd13fcce6c075ad04d811df33594df59ce/results/llm-fulltext-id-bound.json) |
| 思考预算探针 | Flash 3.8 | 单次配置探针 | [budget-probe](https://github.com/zys743551828-art/tender-research/blob/a32229fd13fcce6c075ad04d811df33594df59ce/results/llm-fulltext-budget-probe.json) |
| 图表事实辅助组 | Pro 4.0 | 属于该实验约定的输入范围，不外推完整图纸理解 | [visual-facts](https://github.com/zys743551828-art/tender-research/blob/a32229fd13fcce6c075ad04d811df33594df59ce/results/llm-fulltext-visual-facts.json) |

以上 12 条 case_calls 中有 9 个数值输出，范围 3.8—4.5；两次拒绝估分，一次失败。不能把这一范围当作同模型、同提示词、同输入条件下的重复波动指标。

资料库登记用户报送本例技术分 **4.26／5**、门槛 **4.39／5**。其中 4.2 与报送分相差 0.06，4.5 相差 0.24；这是单例数值对照，不是独立精度统计。案例原记录明确官方成绩出处、聚合方式及最终提交稿对应关系未独立核验；不把用户报送改写为已核验官方标签。[案例记录](https://github.com/zys743551828-art/tender-datasets/blob/abc657ee594f5ef46f57265d1f65e67e36270f8d/CASE-001_广德_污水管网改造及新建/README.md)

## 3. 新系统评分执行与统计计算验证

| 验证层 | 记录和结果 | 已验证内容 | 适用边界 |
| --- | --- | --- | --- |
| ScoreExecution01 独立验收 | 12 项定向检查；状态 passed_scoped_actual_software_mechanisms_only | 实际 PG／ASGI／普通 Worker／评分执行子进程；完整两页、两项各三次判断、六条成功绑定复用、未知费用防重发、取消／撤回后不发布、用途权限隔离、影子结果绑定 | 模型为显式 hard Mock；真实供应商调用 0；formal_eligibility unavailable，业务 prediction null |
| StageA 开发训练评测 | JUnit 50 项、0 失败、0 错误、1 跳过，即 49 通过 | 实际独立 CPU 训练与评测；Ridge 手算解、训练侧标准化、保序回归、分组区间、分母及原量尺、项目分组防泄漏、标签隔离与暴露记录 | 合成样本；开发机制测试，不是实际官方标书精度，也不等同独立 QA |
| TechnicalScale01 独立验收 | 10 项定向应用机制通过 | 技术满分原页依据、Word→PDF→真实 CPU 解析、来源祖先、确认／更正／撤回、时点和跨 parse 防错 | 使用合成“70 分”资料；评分分母接线与正式模型资格未由该批验收授予 |
| 统计泄漏实验 | 192 合成样本、24 组；随机划分 MAE 约 0.079，组间划分约 13.969 | 演示分组泄漏和小样本区间的影响，验证应按项目与来源分组 | 数字是合成机制结果，不是实际产品 MAE |

原始入口：

- [评分执行独立验收](https://github.com/zys743551828-art/tender-docs/blob/f1e5c36c1a818668c3f61a111a0443494515bae9/04-验证与测试/score-execution-independent-checks/candidate-01/独立验收.md)，[final-result](https://github.com/zys743551828-art/tender-docs/blob/f1e5c36c1a818668c3f61a111a0443494515bae9/04-验证与测试/score-execution-independent-checks/candidate-01/final-result.json)。该 JSON SHA-256 为 `afea60c119d107cfb022d181f790e4d2f7070384529dfd08eb8d55d3e299fefb`，与交接索引一致。
- [StageA 实际 JUnit](https://github.com/zys743551828-art/tender-docs/blob/f1e5c36c1a818668c3f61a111a0443494515bae9/04-验证与测试/research-backend-checks/fr029-development-checks/candidate-01/engineering-junit.xml)，[执行范围与端口](https://github.com/zys743551828-art/tender-docs/blob/f1e5c36c1a818668c3f61a111a0443494515bae9/04-验证与测试/research-backend-checks/fr029-development-checks/candidate-01/self-check-result.json)。
- [TechnicalScale 独立验收](https://github.com/zys743551828-art/tender-docs/blob/f1e5c36c1a818668c3f61a111a0443494515bae9/04-验证与测试/technical-scale-independent-checks/candidate-01/独立验收.md)。其 final-result SHA-256 为 `891dbbb5417a5c420957a9bf2bbc81a3b1d7ec6ee0129e77e136989783a3484e`，与交接索引一致。
- [统计泄漏与区间实验](https://github.com/zys743551828-art/tender-docs/blob/f1e5c36c1a818668c3f61a111a0443494515bae9/04-验证与测试/architecture-checks/scoring-result.json)。

## 4. 对代码选型的影响

Codex 的评分不是停留在方案：已有实际案例模型输出、执行与训练软件、数值算法测试、独立机制验收和原始证据。分支评价应计入这些成果。当前 dev 的合成逐项评分和候选核验也有价值，但不能因其更近一次运行就推定整体评分实现更成熟。

按架构合理性和功能完整性选择，Codex 可以作为新系统主线，当前 dev 的已验证机制按模块对照后择优接入。接入需要统一代码接口和契约，这是代码整合；不以迁移旧业务数据库、旧模型资格或生产数据为必要工作。具体运行、组装和软件缺口仍按各自证据核对，不能用独立模块验收代替整包验收。

真实官方成绩独立验收继续单列。归档 [score-readiness](https://github.com/zys743551828-art/tender-research/blob/a32229fd13fcce6c075ad04d811df33594df59ce/results/score-readiness.json) 明确 verified_official_pairs 为 0，技术分仅有一个用户报送案例；[研究补充交接](https://github.com/zys743551828-art/tender-app/blob/27bc63909dd176676ea918beeee745daebdb8f6a/workbench/docs/handoff-20261017/research-and-product.md) 对真实精度同样保留未验收结论。这不抹去已有评分验证，也不授予正式精度通过。
