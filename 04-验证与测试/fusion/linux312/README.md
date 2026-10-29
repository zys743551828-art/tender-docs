# Linux Python 3.12基线与OCR验证

固定应用源码为`521579ca582d667dab46bfe5886da289d86b9bd3`。完整基线为**976 passed、865 skipped、5 failed、13 errors**，1859项、40.20秒；失败包含15个真实OCR资源缺口和3个实际OpenAPI导出回归。OCR专用环境以同一源码完成**16 passed、3 skipped、0 failed、0 errors**，3.17秒，覆盖前述15个OCR失败节点。两个运行的独立原始结果均予保留，不能相加推算整套通过。

| 验证 | 实际结果 | 证据 |
| --- | --- | --- |
| 完整pytest，无maxfail/新增skip | 976通过、865跳过、5失败、13错误 | [JUnit](baseline/f00-linux312-baseline.xml)、[完整日志](baseline/f00-linux312-pytest.log) |
| mypy | 134源文件通过 | [日志](baseline/f00-linux312-mypy.log) |
| ruff、format、bandit | 全部退出0 | [ruff](baseline/f00-linux312-ruff.log)、[format](baseline/f00-linux312-format.log)、[bandit](baseline/f00-linux312-bandit.log) |
| OCR四个相关模块 | 16通过、3个隔离PG条件跳过 | [JUnit](ocr/ocr-parser-tests.xml)、[完整日志](ocr/ocr-parser-tests.log) |

## 源码和隔离边界

源码取自明确Git提交的archive流，仅抽取允许目录中的正规文件；`.env`、`.env.*`、依赖目录、私有目录及非正规成员不落入快照。安全源码共7672文件、164261422字节，4个已跟踪环境文件仅登记排除路径，未读取或复制内容。[来源摘要](source-summary.json)保留提交、精确源码tar SHA、逐文件清单摘要及排除清单；完整7672文件清单只留本机忽略证据，不复制到文档。requirements原Git归档使用CRLF，镜像构建context使用LF，两个原字节摘要分别登记，逐行版本完全相同。

检查镜像使用官方Python3.12.15、glibc2.36、linux/amd64，base digest为`sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258`。`tender-fusion-checks:py312`的image ID为`sha256:f21edf5853b0c337de05a4b6af7ee16787b5520d8e4101b34fea6ee2a70b25be`。62个真实wheel共82778492字节，复用8个相容纯wheel/abi3，没有复用cp311专用扩展或安装编译器。按现`requirements-fusion-checks.txt`及基础锁离线安装，pipcheck成功；[wheel回执](runtime/f00-linux312-wheel-receipt.json)、[runtime回执](runtime/f00-linux312-runtime-receipt.json)、[依赖freeze](runtime/f00-linux312-freeze.txt)及[构建日志](runtime/f00-linux312-build.log)可核对。

运行使用`--rm --network none --read-only`，源码从安全tar解至0700私有tmpfs；仅专用空证据目录可写。没有Docker socket、业务配置、密钥、模型网络或真实业务原件。固定326输入沿既定路径只读挂载；Word03 profile采用已核SHA的固定原字节及显式测试profile。正常业务镜像、容器与Word转换服务未变。

## 失败与跳过

[失败逐节点分类](baseline/failures-classified.json)保留18个失败/错误节点的原始堆栈；[跳过清单](baseline/skip-reasons.json)保留865个节点及84组原始理由。外部PG、Word/Docker、OSS及模型等显式依赖条件未满足的节点不计为通过，没有修改skip条件。

15个OCR节点（2 failed、13 setup errors）的直接原因是缺精确词典；原检查镜像也没有Tesseract和测试要求的NotoCJK字体。真实资源补片覆盖这些节点，记录如下。

3个OpenAPI失败来自`test_task_actual_contracts`：未注册整包router的部分FastAPI应用被无条件扩展整包协议，索引不存在路径引发KeyError。最小代码修复限定扩展调用边界，保留已注册包协议的严格校验；原3项与新增空app/完整包router两项在本机Python3.12通过。修复提交`e08813fb31c4fc2b0213af2cd8a46e0f9be90053`以独立安全Git快照在原Linux Python3.12检查镜像中执行这5项，全部通过、无跳过；[JUnit](openapi-e08813fb31c4/openapi-tests.xml)、[完整日志](openapi-e08813fb31c4/openapi-tests.log)及[来源摘要](openapi-e08813fb31c4/source-summary.json)独立保留。快照含7675个安全文件，排除4个环境文件；未使用仍在修改的前端工作树。此结果不计入固定521完整运行。

## 真实OCR资源补片

OCR验证专用镜像`tender-fusion-checks:py312-ocr`的image ID为`sha256:9ff5ec8223f3610e515880bfa6db1fc563463afd885920a1e38ee1a8319e3988`。父镜像与全部Pythonwheel版本保持不变；仅安装真实Debian Tesseract及NotoCJK字体，并加入`assets.json`精确revision的公开词典。Tesseract实际为5.3.0，不能冒认既有说明中的5.5.0身份。[OCR回执](ocr/ocr-runtime-receipt.json)记录二进制SHA、所有字体SHA、dpkg版本、完整Pythonfreeze与词典身份。

| 词典 | 精确字节数 | SHA256 |
| --- | --- | --- |
| eng | 4113088 | `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2` |
| chi_sim | 2469156 | `a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730` |

两项均来自公开`tessdata_fast`提交`87416418657359cb625c412a48b6e1d6d41c29bd`，下载后核精确size/SHA，测试时只复制入私有临时目录。真实篡改反例保留，没有改预期hash或伪造模型。四个parser模块覆盖实际中文扫描OCR、字节/旋转/bbox、表格和隐藏文字缺口、模型篡改及HTTP协议；3项仍因隔离非owner PG缺失跳过。

本结果仅证明明确`pdfium-pdfplumber-tesseract-cpu-v1`组合的合成机制；Docling/PaddleOCR目标、真实标书精度、图理解、恶意PDF生产沙箱和容量均未验收。[OCR构建日志](runtime/f00-linux312-ocr-build-local-parent.log)保留真实安装过程；[本地父digest注册表引用失败日志](runtime/f00-linux312-ocr-build.log)保留无结果的环境准备尝试，成功构建通过已核image ID的本地tag使用相同父镜像。

## 锁定PDFium风险

原锁`pypdfium2==4.30.1`在[官方PyPI元数据](https://pypi.org/pypi/pypdfium2/4.30.1/json)标为yanked，原因为PDFium文字提取回归`crbug.com/387277993`。[原始公开元数据摘要](runtime/f00-pypdfium2-4.30.1-pypi-metadata.json)包含13个官方发布文件及yanked原因，实际选定Linuxwheel SHA与官方文件记录相符。本验证未升级锁、不改指纹，也不据此宣称真实PDF解析质量已通过。

所有归档文件的大小与SHA见[证据manifest](manifest.json)。
