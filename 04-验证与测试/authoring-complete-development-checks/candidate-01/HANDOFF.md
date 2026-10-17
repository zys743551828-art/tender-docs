# 完整 A2 第一小批：TREE/PLAN/双模板严格文本

本候选仅交独立架构/QA；不是完整 A2、正式发布或 Word 安全验收。25 Owner 文件与304精确依赖共329项均有原字节副本，执行前后核对SHA相同。基底来自已冻结 backend07 308输入，未修改其旧证据。完整输入见 inputs.json 与 frozen-tree；早期固定运行及后续模板目录分页前版本保存在 ../candidate-01-preflight-01，映射见 ../preflight-map.json。

## 实际交付

- 稳定 parent/ordinal/chapter/appendix 树：同批别名、新增/移动/改名/类型、复制全新章节与PM ID、删除影响/墓碑、显式子树恢复、完整历史结构预览恢复。删除不改领域决定；复制不继承计划ready/事实/审批。
- 草稿强ETag与修订CAS；预览持久化完整前后内容与可信上下文/来源，确认全局Action只消费一次，拒绝/旧receipt/历史允许项目结束后使用，新写/迟到确认沿既有lifecycle守卫。
- 计划不可变版本：人工编写状态、目标/要点/待核事实/具体引用/页预算，要求依据需当前confirmed及精确原页/确认基准，模板依据精确目录项；计划ready不是复检通过。投影明确计划基础正文是否已变化。目录目标/页建议只成为not_started计划。
- 目录与版式分别不可变族/版本、独立CAS、上一版本链、真实游标目录。应用目录复用同族key、保留未匹配人工内容/改名/计划；恢复只追加新修订。
- 有据人工格式绑定优先于企业版式，未判明条款明确列出；无确认拒绑定、当前格式报告digest须显式ACK、依据/决定变更阻构建/发布。
- 原core快照完整包含树/计划/两个模板/格式证明；普通Worker对五张新业务表仍无SELECT/写权。原人工保存/生成/补丁拒已删除章，正文ordinal不能旁路树操作。
- 实际确定DOCX含层级标题、静态目录、字体字号/边距、封面、页眉页脚文本、强调及列表。构建明确提交双模板版本并绑定已保存修订。发布复查正文/计划/格式来源，真实Word/PDF状态独立。

## 主开发/PM最小挂接

1. 在全部当前router中另 include `apps.api.authoring_structure.router`（12新增操作）。
2. 实际契约复制目标及旧authoring extender后，调用 `packages.authoring.structure_contracts.extend_openapi(actual)`。预览 actual.openapi.json 为固定backend07 app显式加router后导出，173路径/200操作/331schema，所有本地ref可解析、schema语法校验；不是已部署main。
3. 新数据库须包含已审结构SQL原SHA ebb88fada5e4942d338fde54a13884e43741208829a736ff679d9c761e38c94d；本轮仅用PM fresh authoring_complete01，无owner补权/旧库热改。
4. 已有 `export/1` prepare→关DB compute→finish publish hooks保持，不需新Kafka handler。新build_repository统一采用Word受控profile端口；配置缺失或旧协议独立失败，无宿主soffice后门。Word/PDF关系只有renderer coverage complete才verified，partial真实字节保持unverified，现follow_up只会采用verified链。
5. 不要执行 freeze_inputs.py 覆盖候选。回归从 frozen-tree 用外部venv及私有 .local/authoring_complete01-checks.env，经deploy/run.py加载。不得启动一般消费者或付费模型。原API8806未切换。

接口、请求JSON及确认顺序详 frozen-tree/packages/authoring/README.md，严格actual定义在 structure_schema.py/structure_contracts.py。GET structure含真实snapshot/structure ID，历史集合提供恢复入口，前端不需填UUID。Strong If-Match/CAS和CSRF/Idempotency-Key不可省略。新预览含完整before_sections/after_sections和删除影响，不能盲确认。

## 验证与准确边界

固定根最终56 passed/3 skipped：新真实非ownerPG/ASGI14，旧真实PG16，纯新12+旧14。验证树/计划/模板/同Actionreceipt/并发胜出/关闭/撤回/旧入口拒删章/严格HTTP与schema/真实ZIP/Worker普通角色发布DOCX与PDF独立失败。ruff、format、mypy、bandit通过；Bandit仅旧B603 nosec无匹配警告。记录见 evidence/*，初版固定56/3亦原样保留。

3 skip：两个真实PDF成功后的自动采用/编辑后待选择，以及旧本机LO物理渲染例。没有受控Word已复审profile时不得回退宿主来造通过。Word01/02安全已阻断，观察依赖只作配置加载字节，不计其安全验收；本候选未运行Docker/真实PDF正向转换。公开合成 DOCX SHA2a55fbf83f243169fd36b00e5cf85c548df0e40f895bf7677a3a23a650b42b0a，真实8个OOXML部件；artifact记录是非ownerPG/关TX生成/显式内存对象，未冒真实PDF。

本批动态页码明确PAGE_NUMBER_FIELD_UNSUPPORTED，实际章节页范围null/unknown，不以预算代替实测。安全可实现后续路径另见 ../可信生成正文页码与转换协议草案01.md，须独立生成器/完整语法与来源证明/受控端口/实际PDF页脚验证后另候选，不放宽外来Word域策略。

新A2高频机制与artifact用显式内存对象；旧导出回归确实使用既有OSS写入/回读少量自有合成DOCX，resource-scope.json以精确fresh业务范围列已知摘要/键（部分条目来自内存fixture，不能把全部条目冒OSS独立证据）。无桶扫描、配置变更或删除已引用历史。零付费模型、无Kafka消费/一般relay、无外部发布。

表格/图片、实际章节预览/页定位、选区AI与Word结构内容导入仍在后续切片；本批严格拒表图任意HTML，不拿Word预检paragraphs充当无损导入。构建快照/手工编辑/原快速PDF流程与旧生命周期语义保留。独立测试/架构/产品/用户评审尚待PM排期，本Owner自测不能替代它们。
