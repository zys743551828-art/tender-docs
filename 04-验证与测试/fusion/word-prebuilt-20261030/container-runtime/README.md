# 本机依赖镜像与执行隔离验证

在 ZHANGYANSONG 的 Docker Desktop Linux amd64 环境，实际完成 `workbench/deploy/Dockerfile.runtime` 构建。镜像固定官方 Python3.12.16/base digest、标准构建锁及既有 59 项运行版本，安装阶段 `pip check` 成功；没有自行构建 Word 转换镜像，也没有添加编译器、Office、OCR、浏览器或 Docker 客户端到运行镜像。

- `runtime/`：实际 Python/依赖身份、最终镜像无 setuptools/wheel、非 root 只读无网络运行，以及 57 项 Linux transport 用例通过的原始回执。
- `journal/`：只读源码分目录挂载，独立 `/workspace/.local` tmpfs 内真实生成 mode0600 上传日志；6 项执行分派用例通过。目录权限为0700、uid65532，不连接数据库。
- `host-initial/`：初版宿主执行器 28 项 Linux 用例通过，无跳过；mypy、lint、格式、Bandit 均通过。
- `host-tmpfs-fix/`：真实 Desktop 容器揭示 tmpfs 不在 `Mounts` 数组后，兼容 `HostConfig.Tmpfs` 并强制实际 mountinfo 核验；新增直接相关的3项回归及质量检查通过。没有无变化重跑原28项；最终源码身份以该目录 `frozen.json` 为准，真实 peer 另由 PAGE/RICH 与浏览器链验证。
- `decode-deadline/`：独立复核后增加响应解码完成时的 deadline 检查；新增 Linux 用例证明解码跨过时限时拒绝成功并写入撤回标记。该1项通过，资源已回收；原57项未因这两行边界防护重复运行。

各测试资源均按回执清理；保留本轮构建镜像作为待集成交付材料。`runtime/image-inspect.json` 保存实际本地 RepoDigest，它没有被发布到 registry，不能把构建输出 config digest 当作仓库引用。新机器需按配方构建并检查自己的镜像身份。

这些证据只证明依赖镜像、通道和本地隔离，不证明原 full 入口已安装 Word、完整业务、真实模型或生产验收。执行拓扑仍含可信宿主进程，不能表述为全部代码容器化。取消只是撤回等待，迟到结果被丢弃，原转换按已有有界清理完成。
