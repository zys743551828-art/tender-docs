# AI投标工作台

全新系统的源码工程。产品与技术主记录在[文档工程](/workspace/tender-docs/README.md)，团队职责、当前范围和依赖由[实施任务板](/workspace/tender-docs/00-项目管理/实施任务板.md)维护。

当前实现的是T0底座的首个PDF资料切片：登录、项目、私有上传、字节核验、批次和版本、后台操作、PDF解析范围及物理页证据。核心评审、RAG、完整评分研究、Word转换／导出和提交闭环按后续切片交付。未配置的评审返回明确不可用，不产生演示风险或预测分数。设计放行不等于实际软件、模型质量或生产验收通过，实际结果见`qa`及文档工程的实现验收记录。

## 运行

要求Python 3.12、Node 24、Docker Compose，至少6GB可用内存。服务仅监听本机。固定镜像的PG17＋pgvector、Kafka和SeaweedFS S3用于独立开发环境，真实OSS及生产容灾另行验收。

在本目录创建后端环境并安装锁定依赖：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
python deploy/configure_local.py
docker compose --file deploy/compose.yaml --env-file .env up -d
docker compose --file deploy/compose.yaml --env-file .env ps
```

PG与Kafka健康后初始化**全新空库**。数据库初始化遇已有schema会拒绝，不会清库；不要在已保存资料的库重跑。

```bash
.venv/bin/python deploy/initialize_database.py
.venv/bin/python deploy/initialize_topics.py
docker compose --file deploy/compose.yaml --env-file .env exec -T object-store weed shell -master=localhost:9333
```

在存储管理终端输入`s3.bucket.create -name=tender-private`，完成后用`Ctrl-D`退出。该管理动作创建私有bucket；应用存储身份只有此bucket的读写及列举权。然后执行：

```bash
.venv/bin/python deploy/initialize_object_store.py
.venv/bin/python deploy/check_environment.py
.venv/bin/python deploy/check_compose.py
.venv/bin/python -m apps.api.bootstrap --login your-login --name 你的姓名
```

成员口令由终端安全输入。本会话准备的本地成员访问资料保存在被忽略的私有文件`.local/dev-access.json`，只用于本机开发。`.env`含随机配置，权限600，不提交Git。正式部署为每个进程分别注入所需能力身份，初始化owner仅用于建库。

分别在四个终端运行：

```bash
.venv/bin/python -m apps.api
.venv/bin/python -m apps.worker relay
.venv/bin/python -m apps.worker consume
.venv/bin/python -m apps.worker reconcile
```

API为`http://127.0.0.1:8800`。另开终端运行前端：

```bash
cd apps/web
npm ci
npm run dev
```

浏览器打开`http://127.0.0.1:5173`。项目详情深链接为`/bids/:bidId/materials`，页面刷新保留所指项目。PG端口55439、Kafka19093、S3 19000；API与Worker均使用非owner LOGIN。

## 切片范围

| 能力 | 当前边界 |
| --- | --- |
| 登录及项目 | 同源Cookie／CSRF、服务端会话撤销、幂等创建、分页及持久化 |
| 上传与版本 | PDF单传、SHA256及实际长度核验；明确用途、新文件或新版本目标；verified原件显式重绑定后再次CAS提交 |
| 后台执行 | PG Outbox→Kafka→独立Worker，手动offset、租约／fence、去重及失联对账；实际测试覆盖以证据为准 |
| 解析及证据 | PDFium文本页和物理页证据；OCR、复杂表图及未读范围明确显示缺口，解析不是评审完成 |
| 其他格式 | 本切片只接受PDF；外部Word先导出PDF。未接受的DOCX不显示“已保存” |
| 评审及模型 | 没有配置真实模型，不返回假风险／分数；完整A1、RAG及评分研究仍有各自实施与验收任务 |

`packages/contracts/implemented.openapi.json`只列实际注册接口。`packages/contracts/openapi`等是已审完整目标快照，其存在不代表全部目标能力已实现；实施补充见[契约说明](packages/contracts/README.md)。

## 测试与独立验收

```bash
.venv/bin/ruff check apps/api apps/worker packages/domain deploy tests
.venv/bin/ruff format --check apps/api apps/worker packages/domain deploy tests
.venv/bin/mypy apps/api apps/worker packages/domain deploy
.venv/bin/bandit -q -ll -r apps/api,apps/worker,packages/domain,deploy
.venv/bin/python -m pytest
```

后端真实PG／Kafka／S3纵向自测按`tests/test_slice_integration.py`的显式开关执行，未启用时跳过不计通过。前端运行`npm run format:check`、`lint`、`typecheck`、`test`、`build`。独立应用验收按[qa说明](qa/README.md)执行。

QA使用独立配置`.local/qa.env`、独立数据库、Kafka及存储；通过`deploy/run.py --env-file .local/qa.env -- <command>`注入配置，不在命令行传口令。对应端口为PG55440、Kafka19094、S3 19002、API8801和web5174。可用`python deploy/configure_local.py --profile qa`创建新QA配置，已有配置会拒绝覆盖。QA基础设施启动时给Compose传该配置，再按相同初始化步骤运行；前端用`API_PROXY_TARGET=http://127.0.0.1:8801 npm run dev -- --port 5174`连接QA API。

所有测试资料为明确的合成输入。真实模型质量、真实投标人员试用、正式评分精度和生产运行恢复各自验收，机制验证不授予这些资格。
