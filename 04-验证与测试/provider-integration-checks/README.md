# 外部服务独立验收

DeepSeek与指定杭州OSS的连通和适配器范围通过，当前范围阻塞0。最终记录为`final-candidate-02-result.json`，校验为`verification-02.json`，冻结输入为`frozen-inputs-02/`。原始真实调用字节另在`executed-deepseek-adapter.py`和`executed-oss-adapter.py`。这不代表A1评审、RAG、正式评分、OSS浏览器CORS或生产验收完成。

| 实际范围 | 验证结果与证据 |
| --- | --- |
| 只读预检 | 官方DeepSeek模型列表200；当前可用deepseek-flash与deepseek-v4-pro。PM选flash，未调用Pro。指定OSS bucket信息200、杭州Standard、ACL private；readonly-configured-result.json |
| 原生OSS SDK | 本次UUID中文key条件PUT；鉴权GET SHA一致；同key覆盖被409 FileAlreadyExists拒绝且原SHA保持；私有签名中文名下载SHA一致、匿名403；本次对象删除204；oss-object-result.json |
| 实际OSS adapter | 实际prefix隔离后使用逻辑blobs/staging；同字节重放复用、不同字节409且原SHA不变；签名GET及匿名403；实际HTTP使用签名PUT后原生GET校验SHA；两个本次对象分别删除204；oss-adapter-result.json |
| 实际DeepSeek adapter | 唯一短chat：flash、temperature0、max_tokens32、thinking disabled，返回严格JSON `{"ok":true}`，finish stop；75输入＋5输出＝80tokens，记录requestID及usage，正式评分资格为false；deepseek-chat-result.json |
| 独立异常检查 | 最终源16项显式MockTransport／无网络负例通过：HTTP异常无重试／不跟随重定向、超时未知计费、总deadline覆盖慢读、截断保留usage不补发、非法JSON及模型身份拒绝、Schema付费前拒绝、OSS非法地址／locator／授权范围本地拒绝；negative-candidate-02-bound-result.json |

真实chat总计1次；私有600 guard保留，自动回归不会再次发付费请求。最初配置deepseek-chat未出现在模型列表的记录保留，随后按PM决定使用实际可用flash，不自动降级或改用Pro。

付费调用时DeepSeek源为`81f03971…`；最终源新增Schema本地悬空引用／anchor／嵌套$id的付费前检查，以及运行时引用解析错误的安全收口。独立核对实际diff，合法请求传输与响应解析未改变，最终源离线16项复验；没有把旧真实结果改写为最终源重新付费执行。OSS实际执行字节与最终候选完全一致。

全程仅访问官方DeepSeek和用户指定杭州OSS。没有扫描既有对象，没有修改bucket ACL／CORS／versioning；仅创建并定点清理本次`workbench-checks/UUID/`下3个合成对象。未把私有凭据灌入普通QA进程。报告和冻结源的实际密钥值匹配为0，未保存Authorization、Cookie或签名URL。

未验证：实际A1端到端评审及真实检测质量、RAG／Embedding／重排、正式评分与三次判断、预算调用账本接线、浏览器OSS跨域上传、真实投标资料、真人及生产。各项由对应实现和独立用例继续验收。

自动回归入口为新工程`qa/provider_regression.py`。默认offline模式不读凭据、不访问外部服务；read-only仅做模型列表和bucket信息，oss-objects仅增加本次UUID合成对象并定点清理。编排表永远不包含付费chat；离线编排已实际通过16项，见`automation-offline/result.json`。
