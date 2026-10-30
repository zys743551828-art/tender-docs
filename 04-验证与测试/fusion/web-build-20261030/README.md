# 前端锁定版本构建

应用提交 `4108b686c9c5174b6e14ae8f29cfc89c665dd752` 提供标准 Docker 多阶段构建：固定 Node24.22.2 Linux/amd64 manifest，使用已有 npm 锁文件及完整 TypeScript/build 检查，最终只导出静态产物。构建实际于 2026-10-30 18:01:40–18:01:55 UTC 成功。

- [成功日志](build.log)、[产物清单及版本](build-summary.json)、[官方镜像元数据](official-images.json)。清单记录本轮输出字节，不承诺不同机器任意平台字节一致。
- [首次失败](first-attempt.json)：初始上下文漏带测试类型检查必需夹具；最终仅加入11个已跟踪合成 `.ts` 模块，保留原检查。
- [构建配方](Dockerfile.web-build.txt)、[上下文规则](Dockerfile.web-build.dockerignore.txt)、[输入静态核对](recipe-input-check.json)。默认拒绝私有环境、node_modules、其他QA预览；PDF资源从已锁定依赖重建。
- 可执行命令和部署边界见应用 [`docs/build-artifacts.md`](https://github.com/zys743551828-art/tender-app/blob/dev/workbench/docs/build-artifacts.md)。旧 full/1 安装器仍有独立平台前提和 Word 通道接线缺口，构建成功不等于正式部署验收。

官方 Nginx1.30.5-alpine 固定 manifest 已在本机获取；实际服务和浏览器交互/像素结果单独记录，不能从本构建日志推断。归档不含完整dist、秘密配置、真实标书或缓存。
