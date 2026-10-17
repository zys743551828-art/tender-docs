# 本地检索与证据链

该模块实现真实CPU BGE-m3分块／向量、本地中文分词、PG向量与词法精确检索、RRF、可降级的真实重排、来源资格与引用校验。正式评分使用独立的完整章节和三判断协议；查证片段不成为完整评分输入。

## 运行

模型运行依赖与后端分离，权重只放被忽略的私有目录。首先创建`.venv-rag`，从`https://download.pytorch.org/whl/cpu`安装锁定CPU Torch，再安装根目录`requirements-rag.lock.txt`。`deploy/fetch_rag_assets.py`下载两个固定官方revision，验证权重SHA，保存全部六项资产清单；运行阶段不下载模型、不执行远端自定义代码。目录存在额外未验证模型文件时拒绝加载。

私有配置需要`RAG_RUNTIME_URL=http://127.0.0.1:8810`、随机`RAG_RUNTIME_TOKEN`、`RAG_ASSET_DIRECTORY`。新环境配置器生成这些字段。模型进程只接收模型角色环境，不应持有数据库或云服务秘密：

```sh
.venv/bin/python deploy/run.py --env-file .local/external-release.env --role model -- .venv-rag/bin/python -m packages.rag.server
.venv/bin/python deploy/run.py --env-file .local/external-release.env --role api -- .venv/bin/python -m packages.rag.coordinator
```

API控制面仅用受限API LOGIN，扫描新解析并持久安排`rag-index/1`任务；Outbox／Kafka文档topic和受限Worker处理任务。业务接口为`POST /api/v1/retrievals`，客户端只指定项目、普通界面上下文、查询和数量，不指定正式评分用途、来源白名单、业务时点或模型profile。

Worker运行还需要正常的relay、document consumer及reconcile。新空库初始化在基础角色脚本后执行`deploy/sql/vector-runtime-grants.sql`，只给Worker所需的三个纯向量函数；业务不使用owner。现有早期测试库不会自动成为完整RAG环境。

## 身份与并发

- 分块使用实际Tokenizer的原文offset，默认800／1200／120 token，保留物理页和原始块；不把中文字符数当token。
- 原件有稳定root来源，每个parse有确定子来源与完整祖先关系。`parse_source_id`和`original_source_id`供其他模块共用。撤回归一到原件根，重新解析不能恢复已撤回来源。
- 索引分为只读准备、无数据库连接的模型计算、短事务发布；发布仅使用已准备的词法与向量数据，不持图锁调用HTTP模型。相同parse存在其他分块方法时拒绝覆盖。
- profile绑定模型revision、权重、全部配置／词表资产及运行依赖。查询和索引须同一profile、1024维、有限值和L2归一化。
- 白名单向量先物化再精确排序，避免HNSW过滤改变默认路线。词法和向量各50候选，融合前20可重排，最多提供8个证据包。重排失败明确记录RRF降级，不标作成功重排。
- 每次应用在同一短事务内先锁定项目上下文，再持来源图锁核验head与最终chunk资格，直到写入effect后提交。来源／祖先变化、排除组变化或正文／资料head变化均拒绝继续应用。幂等重放也重新核查当前范围。
- 引用ID对应实际提供的来源、parse、物理页和逐字摘录。逐字校验不等于已证明模型的语义主张；未验证主张不能据此成为正式事实。

## 验证与范围

`tests/test_rag_core.py`和`test_rag_http_port.py`是显式机制测试；`RUN_RAG_REAL=1`启用`test_rag_real_integration.py`，需要真实本地模型、独立测试库和受限LOGIN。`tests.probe_rag_pipeline`核验已实际上传的合成PDF、Kafka索引effect、当前ASGI查证和可逐项还原的引用。

当前公开检索仅接本项目文档，历史上下文也限本项目；企业知识发布与跨项目适用由后续领域接口提供，不能靠请求扩大白名单。扫描、表格和图页的未读范围来自解析记录；能索引可读文字不代表整份资料全部读懂。真实公司资料召回／引用质量、业务检测精度和正式评分资格独立验收。
