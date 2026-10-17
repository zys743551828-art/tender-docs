# RICH 表图与受控生成协议01（待静审，不执行DDL）

承接冻结A2 PAGE/build02 5cb2bd6f…；旧候选和外来Word03不改。专属 packages/authoring 新模块、rich_assets.sql、authoring rich router/测试由编制Owner；共享main/engine/exporter由PM组合，Word接收/结构导入投影由解析Owner独占。本协议不是已实现或安全放行，SQL审过才请PM独立fresh；实际新RICH转换端口须具体静审后执行。

## 内容与保存

在现有严格PM doc、稳定node ID、原子保存CAS/幂等/来源守卫上追加版本2；旧文本输入仍可保存，旧修订保留原digest与原字节。table只在doc下，tableRow只在table下，tableCell只在row下，cell内段落/列表，不嵌套table；表10,000有效格/最多100列、200行、文档仍10,000节点/1MiB。行列合并要求完整矩形occupancy检查，拒重叠、越界、孤立continuation、不等宽和空行。table attrs有id,column_widths_twips；cell attrs有id,colspan,rowspan,header；宽度为正且总宽不超当前版式可用宽。caption为明确普通文本，不能靠文字替代表格。

image只在doc下，attrs含id,asset_id,asset_revision,asset_version_id,width_twips,height_twips,alt,caption。不接受URL、路径、dataURI、任意MIME或来源资格。图片真实内容归独立资产版本；修改图片与复制章分别生成真实新asset版本/PM节点ID，章节复制可明确复用同项目只读资产版本，不复制适用事实。同项目active当前版本才能新保存/恢复/构建；撤回或head变更拒新用，不删历史bytes。原有source_refs对图片说明的事实引用仍独立校验，图片存在不等于事实确认/正式评分证据。

现有复用核查：apps/api/main.create_upload 和 packages/uploads.service 均调用 Word inspection.filename_type，明确只接PDF/DOCX；multipart复用同一资料批次/文件角色/版本链，不能把image冒充source_original PDF或Word。authoring当前无image资产端口。可复用既有storage及不可变blobs的对象上传/回读与下载签名能力，不新造全业务文件管理；两表仅承担本项目编制图片当前资格和精确版本链，不冒充资料文件或正式来源。原图和规范图均复用blobs。

## 两表与权限

具体SQL packages/authoring/sql/rich_assets.sql：bid-scoped authoring_image_assets只有head_revision/head_version_id API列更新；authoring_image_versions不可变，前版链及完整(bid,asset,revision,id)FK，head延迟约束保证同项目精确版本；head只能沿真实前版连续推进。active/withdrawn均追加版本。原图与规范图分别FK既有blobs，尺寸/像素上限固定；归一化proof冻结输入输出digest、Pillow版本/算法、EXIF处理与范围。版本另含严格source_refs（上传自有图片可空，不赋事实资格；从Word/原页派生必须携带其原件/parse/evidence精确引用），API与Worker复用现有assert_sources同校原文祖先；祖先撤回阻止新保存/新构建/最终发布，历史原图仍可还原。此处原图是上传证据追溯，不是正式评分来源。

API可SELECT/INSERT二表、仅head列UPDATE；Worker仅二表SELECT（最终fence内查head/撤回所需），无DML，不读新业务主体/会话/commands，research/score无新权。Worker复用既有blobs读能力。所有写入同bid锁→Command/asset锁→生命周期可写；旧ACK/历史读取不因结束而消失。API与Worker最终守卫均锁bid后按asset ID排序锁head；head表有SELECT/UPDATE权限的差异：Worker只SELECT不得FOR UPDATE，使用现有bid事务锁序列化API撤回，普通查询在RC取当前head。

## 图片接收与事务边界

新独立API只接有界PNG/JPEG原始字节，最大8MiB，实际解码格式须匹配允许集；动画、多帧、过大尺寸/解压炸弹、SVG/WMF/EMF/HTML不接。Pillow沿已锁公开12.3.0，EXIF方向明确处理，剥去元数据，转换单帧RGBA/RGB规范PNG，最大16MiB/1万边长/1600万像素。上传原字节只作鉴权下载attachment，不交Word/LO；只把再编码规范PNG嵌入。

接收先短事务鉴权/主体/项目可写→关闭DB→有界CPU解码/再编码与对象PUT/回读SHA→最终同bid锁重新鉴权/生命周期+Command幂等登记blobs/asset/version。对象key由服务器scope+请求身份+内容摘要确定；同key异payload拒绝不覆盖既有字节。失败/中断可留下未登记临时自有对象，记录真实可恢复状态，不假装数据库原子覆盖对象网络；已登记/历史引用对象不自动删。客户端不能选择桶、key或profile。无供应商/模型调用，未登记byte不能取得引用资格。

## 快照、预览与构建

保存/恢复/建议应用前严格校验image资产当前精确head/active和normalized blob；把去重完整资产清单(原/规范blob身份、key、digest、尺寸、MIME、normalization proof、asset/version身份)随原core draft_snapshot扩展冻结，不要求Worker读树/计划新表。构建冻结/Worker最终publish都重核assets和source_refs；历史快照只读及旧产物可下载，撤回后新构建拒绝，不整体404历史。

Worker prepare短事务读快照和core元数据→关闭连接→storage GET规范图并核SHA/实际PNG/尺寸→独立deterministic rich writer3产真实OOXML，表格w:tbl/grid/gridSpan/vMerge，图片内嵌media/drawing且仅包内关系→受控Docker→最终fence/生命周期/资产及来源重核。无I/O跨bid锁。表图DOCX-only也做完整容器/引用校验，缺资产不以alt或空白代替后称成功。

RICH生成另3.x producer/profile，不能放宽已审PAGE-only2.x；完整快照+规范图逐字节重assemble，所有part/content-type/relationships/tags/attrs封闭集，PNG仅引用manifest中精确digest。单PAGE字段仍唯一动态能力；外来Word仍全域拒。实际新RICH沙箱代码先静审再运行。PDF对应资格区分正文文字、表格结构/合并、图片asset/drawing与实际可见性；光有text digest不能宣称图表对应完整。无法验证的图/表显式partial，不冒verified。预览展示实际DOCX/PDF产物和精确格式结果，不拿DOM重绘冒Word版面。

目录/各章实测页范围后续用明确受控静态标记/两阶段固定点生成与PDF逐页确认，不用计划页预算推测。选区AI继续同Action/来源/预算/未知不重付协议，Word内容导入只消费解析Owner精确原blob/version preview，unsupported gaps先展示再明确选择，不复制外来活动内容到可信生成通道。

## 独立验收边界

需要真非owner PG：全部合法INSERT/连续head/withdraw CAS；错项目/错前版/错head/revision skip拒；Worker无新DML/无业务身份；原图/规范图真实SHA与界限。要实际DOCX表格合并/图片parts和Docker多页PDF视觉/文字验证，再检取消迟到/来源或资产撤回发布零effects。新机制tests硬阻真实requests/httpx/模型，显式memory对象；真实OSS单独准确scope。0付费/0消费者。纯规则/SQL静审不能计完整A2通过。
