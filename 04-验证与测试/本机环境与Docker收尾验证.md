# 本机环境与 Docker 收尾验证

执行环境为用户授权 Windows 电脑 `zhangyansong` 的 Docker Desktop Linux daemon，29.8.2、linux/amd64。原项目位于 `D:\workspace\tender`；五个原工作树仍是旧融合分支且无未提交改动，远端五仓已统一 dev。应用/文档使用本机新建隔离 dev 克隆，原工作树、私有环境文件和历史仓库保持原状。没有操作另一台电脑，也没有继续使用云端终端执行。

## 本轮结果

| 检查 | 实际结果与范围 |
|---|---|
| 后端静态 | 在本机已有 Python3.12 Linux 检查镜像中，完整 Ruff、format、mypy、Bandit 范围通过 |
| 配置、Kafka、资源机制测试 | 27通过、0失败、0跳过；包含配置真实子进程与失败/取消/所有权拒绝检查 |
| PG启动 | 原固定 PG17 镜像，实际 server_version 170011；独立 configuration_check 库，普通非owner LOGIN经run.py连接，1通过 |
| Kafka真实检查 | 本轮新建无外网 Kafka：旧6主题只读拒绝；用当前初始化器补齐9主题；只读复检9主题、分区、副本及保留配置通过 |
| Docker真实收尾 | Windows宿主驱动真实Linux容器/网络/卷，成功、失败、取消3项通过，精确inspect确认资源不存在 |
| Windows纯机制 | UTF-8模式及显式工作目录临时路径下16项通过；与Linux用例重叠，不累加为独立覆盖 |
| 原提交CI | 6e9a714addfd7933d055f2e4ea48b82b9648b814的[CI41033641600](https://github.com/zys743551828-art/tender-app/actions/runs/41033641600)已completed/success，11项全部成功；属于原提交证据 |

本轮临时资源使用唯一标签和精确回执，所有已结束运行均 `closed=true`。镜像沿用本机缓存，无付费模型、真实OSS或原业务数据库连接。没有用本轮机制验证宣称同事环境恢复、真实模型质量或用户签收。

## 失败与修正

首轮临时Kafka在禁用网络后解析容器主机名失败；改为显式回环监听后重跑通过，首轮3个临时容器也已收尾。真实收尾测试首次发现daemon对缺失网络返回 `network <ID> not found`；精确目标判定已兼容该形式，同时继续拒绝把socket缺失/权限错误当作资源不存在，3项真实验收复验通过。

Windows默认GBK与沙箱临时目录限制分别导致过收集/夹具失败，UTF-8与显式本轮临时路径复验通过。完整Windows类型检查报告POSIX专用API不存在，完整类型检查在本机Linux目标环境通过；没有放宽POSIX权限守卫。Linux首次静态快照遗漏qa目录导致导入分组误判，补齐同源qa后完整静态通过，没有改无关源码迎合错误快照。

新隔离克隆受宿主CRLF展开影响，源码指纹先出现FR001差异；逐字节核对Git blob后只恢复未修改文件的原LF，并给活跃Python源目录显式LF属性。原冻结源码、归档期望及用户原工作树未改。正式指纹只刷新经审查的deploy变化及新增测试映射。

## 历史容器清理

原始清单共99个项目容器：六组full-desktop历史检查78个，tender-fusion 8个，bidwb 13个，均停止。先在较窄授权下删除63个只读根/空可写层测试容器，保留15个有运行日志/配置/套接字变化的依赖容器及其余21个环境容器。用户随后对原始99个范围及未挂载可写层丢失后果明确确认，按原ID清单继续逐项清理；镜像、数据卷、绑定目录和本轮资源不在删除范围。

精确删除/保留与前后身份校验记录随本页配套证据保存。历史数据卷和镜像保留不等于原实例已恢复，重建与生产恢复仍需独立验收。

## Word03

本机精确inspect冻结镜像 `sha256:327daa8fae7178d61f93142f146b098467b345e10997b9eb79f63bd58e5c8f3c` 返回No such image。项目范围内找到的两份tar均为源码归档，没有Docker save manifest，未找到原镜像归档。已有bidwb-word:7.4.7为根供体另一转换服务，未替代、未改冻结常量。指定真实转换与版式验收未运行；最小输入仍为[原镜像归档或可信不可变引用](Word03来源与复现核查.md)。

最终原始99个历史容器全部不存在：先前63个，本次36个，保留/失败0。Docker容器数由99降至0，108个卷及16个镜像的身份集合摘要前后完全一致；没有删除网络或宿主文件。[精确审计](fusion/local-docker-20261030/historical-cleanup.json)与[运行证据](fusion/local-docker-20261030/evidence.json)保留原始身份和结果。

应用代码固定提交 `b21cb8c6c7637ad5fcdec8e8c7169b89c9de6f41`。完整静态、27项机制、PG1项、真实Docker3项与Kafka场景分别记录；新提交远程CI状态单独跟踪，不用原提交11/11替代。

新提交CI为[41037957952](https://github.com/zys743551828-art/tender-app/actions/runs/41037957952)，[时点元数据](fusion/local-docker-20261030/current-ci-snapshot.json)记录in_progress，不能报告为全部通过。
