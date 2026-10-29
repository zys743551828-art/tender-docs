# 项目dev分支整合

后续五仓统一使用dev并跟踪origin/dev。不同仓库保留各自历史与提交，不要求SHA一致；未删除旧分支，未强推，未创建/审批PR，未改分支保护。应用主源码仍是workbench，原backend/frontend供体dev继续保留。

## 来源核对

| 仓库 | 原dev | 纳入来源 | 合并方式 |
|---|---|---|---|
| tender-app | 45a5921011fa22f400482f73710e96a32b916082 | render-acceptance-20261026：7d3ed69e4fc93e13f1443cf2aeef467987d561bf | 保留两边历史的merge |
| tender-docs | dbbce7098754ae3fc39014c16c9ab3d1ee2665af | docs/workbench-sync-20261028：239f1b6601acf0f42820418002557e731a40c36c | 正常merge，无冲突 |
| tender-research | 9ed745c10874819060d792deb1b9e47216f1e60c | fusion/bid-workbench：a32229f | 正常merge，保留dev研究记录；不是把旧work覆盖dev |
| tender-design | 不存在 | fusion/bid-workbench：eeaabe8925357a9175c5fda3dee23bd9c8eae646 | 从既定融合基线创建dev |
| tender-datasets | 不存在 | fusion/bid-workbench：abc657ee594f5ef46f57265d1f65e67e36270f8d | 从既定融合基线创建dev |

关联范围来自项目README与既有融合计划；不对用户其它仓库操作。原本地work分支较旧，未作为覆盖远端的依据。应用另一worktree保留原work状态。

应用四个冲突位于供体ReportPage及其测试、SubmitPage测试、App测试。保留dev已演进的关键问题数量/建议数量、priority=qualification入口和导航断言；来源分支的其它自动合并改动保留。供体backend/src与tests相对原dev无变动；研究src/tests也未变动。

## 推送与检查副作用

应用可见Actions只有Workbench CI，内容是静态、单元及隔离原生测试，没有部署步骤；本轮将push/PR检查范围加入dev。其它四仓可见Actions为空，源码没有工作流。仓库webhook读取返回GitHub403 Resource not accessible by integration，未重试/扩权；不能声称检查过未知外部集成。现有dev保护标记为false，未改变任何保护。

## 环境问题与验证口径

首次npm默认缓存不可写，改用/tmp缓存安装锁定依赖成功；首次研究测试因缺numpy收集失败，按项目依赖建立独立.venv后重新执行。

供体前端首次175失败/153通过，根因为jsdom AbortSignal与Node fetch类型不一致，未进入mock API。修复仅在测试setup统一Node取消控制器，不忽略取消；新增2项传输回归验证正常信号与已取消信号。最终供体前端37文件/330测试及格式、lint、typecheck、构建通过。供体后端127通过/180跳过（无专用PG）；src/tests Ruff/格式、mypy 82源码及Bandit通过。扩大扫描到既有tools时55项lint和3文件格式失败，其相对原dev无改动，保留问题，不以忽略规则或无关重排冒充通过。

研究168通过/1跳过，Ruff check通过；新安装Ruff检查原有格式时33文件失败，src/tests与原dev一致，此次没有批量重排。设计10个HTML本地链接无缺失；数据3个JSON解析通过，未改原件，未进行真实评分或真实用户验收。

## 后续范围

测试专用、不可晋升生产的认证协议方向已获同意；本轮优先完成分支整合，没有实施该新协议。当前正式资格守卫继续生效。指定Word镜像、真实模型质量和生产验收边界不因合并改变。


workbench前端196文件/1980测试及格式、lint、typecheck通过。相对来源分支的合并增量diff --check通过。相对旧dev扫描全部新导入的workbench时，既有CRLF字体来源、ASCII PDF尾部空格等会产生历史空白告警；原来源文件字节未改，未为清理告警改写冻结资产。


## 最终兼容版本与复验

应用dev为288b458511a0717bb4edabbf087f6b4245ad11b3，双父提交分别为45a5921和7d3ed69，已推送且包含原源提交。研究dev为e19a06894256bc9fad3ed80ee238ef4bd17bbebb（9ed745c与a32229f合并）；设计dev为29b7295ad629a7cf4835a6114505f9c098a4cfe3；数据dev为ee9925044786a34bcda3d958e5fb8d1fdbb93d47。文档本提交为dbbce70与239f1b6的合并，包含本页及实际应用代码指纹。

合并后workbench后端全量4662通过、0失败/错误、2432跳过，998.96秒；前端1980通过及构建、全仓Ruff/format/mypy（777源码）/Bandit通过。前述供体前端330通过及构建，供体后端127通过/180跳过，研究168通过/1跳过均来自本轮合并树，未使用旧日志替代。本轮未运行真实模型、生产库或部署；未重跑未改动的原生专项，跳过不算验收。40项源码/测试/文档同步与3个反例通过。

应用推送已实际触发[Workbench CI 40908765787](https://github.com/zys743551828-art/tender-app/actions/runs/40908765787)，记录时queued，不提前声明通过。其它四仓没有可见Actions工作流。日志位于本轮执行环境/workspace/scratch/dev-*；机器汇总见本页附带的dev证据目录。

整合后首轮远程CI终态为failure，原生helper的安装权限遗漏和后端20分钟超时已定位；后续版本化修复a703f35见[正式修复记录](../04-验证与测试/dev-CI原生权限修复.md)。整合提交288b458与当时本地通过记录保留，不再代表当前远程CI已通过。
