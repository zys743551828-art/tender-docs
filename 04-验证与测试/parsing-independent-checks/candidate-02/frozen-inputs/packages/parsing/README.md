# 本地PDF解析端口

当前可运行profile是 `pdfium-pdfplumber-tesseract-cpu-v1`：PDFium真实渲染、pdfplumber文字定位及有边框规则网格、Tesseract中文／英文扫描页OCR。它是资源受限开发环境中的明确替代profile；Docling／PaddleOCR目标基线尚未在本环境安装或通过资料验收。不能把本profile的合成测试改写成目标基线或真实市政标书精度结论。

输入为原始PDF字节及SHA256；输出绑定原件、解析器、配置摘要，并包含物理页、旋转后的point尺寸、页级方法、原文块／bbox、网格单元格、启发式章节及明确缺口。坐标原点为旋转后页面左上角，单位point，不是0至1比例。空白页通过实际渲染区分；无可见文字的隐藏文字层保留缺口。普通数字文字页无需重复OCR。有文字层但含图片时保留图片未理解缺口；当前不会声称已OCR这些嵌入图中文字。

可见／隐藏文字混排时，不能用整页有墨迹证明全部文字可见。PDFium按字符关联文本对象，检查不可见／仅裁剪的renderMode及fill／stroke透明度；发现隐藏或无法核实的文字对象，就放弃整页PDF文字层和基于该文字层的表格内容，转为真实渲染OCR并明确保留`NONVISIBLE_TEXT_LAYER_EXCLUDED`或`TEXT_VISIBILITY_UNVERIFIED`缺口。隐藏错误条款不会作为PDF文本证据进入结果。这一处理没有证明所有遮挡、白底白字等PDF视觉歧义均已解决，真实资料及复杂视觉质量仍未验收。

扫描页真实OCR成功仍保留扫描布局／表格／图理解未验证。复杂合并表格不猜rowspan／colspan；跨页表不自动拼接，启发式章节不是经过核验的目录。规则表格旁的独立工程图仍标未理解。文字层识别不等于读懂图纸；`quality_benchmark=not_business_validated`始终明确当前业务质量资格。标准提交所需读取资格由上层结合这些缺口判断，不能删除缺口来通过闸门。

运行前安装 `requirements-parsing.lock.txt` 的CPU运行依赖和Tesseract；当前使用系统Tesseract 5.5.0，运行身份绑定实际二进制摘要。公开词典安装需显式执行 `python -m packages.parsing.fetch_assets --directory .local/parsing-models`；安装遵循环境代理／CA，URL固定公开仓库commit，校验精确字节数和SHA。请求期间不联网下载。启动检查本地词典，身份同时记录解析源码、依赖、全部系统字体摘要、模型及预处理。生产应在固定镜像内安装完整锁；当前轻量venv复用了系统已有包，不能称已完成独立镜像复现。

私有600权限文件 `.local/parsing.env` 保存 `PARSING_RUNTIME_URL`、`PARSING_RUNTIME_TOKEN`、`PARSING_ASSET_DIRECTORY`。启动：

```sh
.venv/bin/python deploy/run.py --env-file .local/parsing.env -- .venv-parsing/bin/python -m packages.parsing.server --port 8812
```

服务只监听127.0.0.1，禁用访问日志／OpenAPI文档，所有路由校验Bearer。`GET /identity`取得方法身份；`POST /parse`发送application/pdf原字节，必须携带`X-Source-Sha256`。最大原件100MiB、1000页、单页渲染1200万像素、单次OCR45秒、整文300秒后剩余页明确未读取；并发原生解析只允许一个，其余返回429。它尚未完成恶意PDF隔离、生产容器沙箱和容量验收。

主API／Worker只需导入 `packages.parsing.http_port.ParsingHttp`；该模块仅依赖httpx及标准库，不加载解析重依赖。它验证服务loopback地址、绑定方法身份和响应中的字节摘要、页顺序、尺寸、文字摘要及bbox范围；本地令牌和文档不经过环境代理。调用应在Worker的compute阶段，在受限publish事务内写入结果，不能持数据库图锁等待OCR。

`qa/run_local_check.py --output-dir <新的证据目录>`通过真实TCP生成并解析六页合成PDF，含中文扫描、空白、规则表格、工程图和旋转页；明确指定新目录并拒绝覆盖已有证据。结果为部分读取，故意保留未理解图像范围。`tests/test_parsing_local.py`与`tests/test_parsing_http.py`验证真实解析及协议反例；运行HTTP测试需执行环境允许本地socket。测试依赖pytest8.4.2、reportlab4.4.9和NotoSansCJK字体；字体摘要记入实际证据。没有真实业务资料或专业标注，不声明真实精度、完整图理解或生产就绪。
