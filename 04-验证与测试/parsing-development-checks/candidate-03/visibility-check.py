"""Real loopback delta for visibility variants, no provider model requests."""

import hashlib
import json
from pathlib import Path

from packages.parsing.http_port import ParsingHttp


out = Path(__file__).resolve().parent
source = Path("/workspace/tender-docs/04-验证与测试/parsing-independent-checks/candidate-02/visibility-source.pdf")
data = source.read_bytes()
port = ParsingHttp()
result = port.parse(data)
assert result["page_count"] == 5
checks = []
for index, marker in ((0, "INVISIBLE_RULE"), (1, "ALPHA_ZERO_RULE"), (2, "WHITE_ON_WHITE_RULE")):
    page = result["pages"][index]
    assert marker not in page["text"]
    assert all(marker not in block["text_content"] for block in page["blocks"])
    assert "90" in page["text"]
    assert page["quality"] == "partial" and page["gaps"]
    checks.append({"name": marker + "-excluded", "passed": True})
white = result["pages"][3]
assert "WHITE_VISIBLE_RULE" in white["text"] and "30" in white["text"]
assert white["method"] == "tesseract_ocr" and white["quality"] == "partial"
checks.append({"name": "visible-white-on-black-kept-by-rendered-ocr", "passed": True})
plain = result["pages"][4]
assert "VISIBLE_BODY" in plain["text"] and "90" in plain["text"]
assert plain["method"] == "pdf_text" and plain["quality"] == "usable" and not plain["gaps"]
checks.append({"name": "ordinary-black-text-fast-path-kept", "passed": True})
assert port.identity["parser_source_digest"] == hashlib.sha256(Path("packages/parsing/core.py").read_bytes()).hexdigest()
(out / "actual-response.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
report = {"status": "passed_visibility_delta", "transport": "real_loopback_http", "source_path": str(source), "source_sha256": hashlib.sha256(data).hexdigest(), "parser_digest": port.parser_digest, "config_digest": port.config_digest, "identity": port.identity, "checks": checks, "paid_provider_requests": 0, "business_documents": 0, "business_accuracy_accepted": False, "target_baseline_qualified": False}
(out / "real-http-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print({"status": report["status"], "parser_digest": port.parser_digest, "core_source_digest": port.identity["parser_source_digest"]})
