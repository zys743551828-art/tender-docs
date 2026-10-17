# 普通生成用途隔离最小增量

固定服务端用途：core_review（默认）、assistant、authoring。非法值在构建provider client和任何预留之前拒绝。用途进入原有profile_digest；默认core_review的profile字段与摘要保持原样，既有成功结果可重用。同operation/logical key改变用途拒绝身份冲突；不同operation的不同用途不会错误复用缓存。未知结果/费用在同用途重试或跨command时仍拒绝补发，purpose不授予新的收费意图。

服务签名：generate(message, lease, *, logical_key, messages, json_schema, max_tokens=2048, purpose='core_review')。调用点必须固定purpose和logical_key，不能接受用户自选评分/工具用途。正式评分资格仍false，未增加Worker权限或付费入口。

开发16项：9项用途增量（6非法值、默认摘要与3用途隔离、2用途未知不重发）＋7原账本机制回归，真实非owner PG、显式MockTransport；合成对象只在测试内存适配器，不是S3/OSS效果证据。ruff/format/mypy/bandit通过。真实provider收费0；assistant/authoring生产调用点不在本候选。
