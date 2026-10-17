"""唯一真实付费短chat，经实际DeepSeek适配器，无自动重试。"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from qa.helpers import ensure
from qa.provider_preflight import ROOT, configuration, safe_error, write


def main():
    output = Path(os.environ["QA_PROVIDER_OUTPUT"])
    result = dict(
        scope="实际DeepSeek adapter的唯一极短结构化chat连通验证；非业务评审/评分/RAG效果",
        passed=False,
        chat_requests=0,
        implicit_retries=False,
        started_at=datetime.now(timezone.utc).isoformat(),
        checks=[],
    )
    try:
        values = configuration()
        ensure(values["DEEPSEEK_MODEL"] == "deepseek-flash", "只允许PM已指定flash模型")
        os.environ.update(values)
        module = importlib.import_module("packages.domain.providers.deepseek")
        adapter_bytes = Path(module.__file__).read_bytes()
        result["adapter_sha256"] = hashlib.sha256(adapter_bytes).hexdigest()
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / "executed-deepseek-adapter.py").write_bytes(adapter_bytes)
        adapter = module.DeepSeekClient.from_env()
        guard = ROOT / ".local/provider-chat-guard.json"
        # 必须在任何付费请求之前原子占位；重启/异常/超时都不能自动重发。
        with guard.open("x") as file:
            guard.chmod(0o600)
            file.write(
                json.dumps(
                    dict(
                        at=datetime.now(timezone.utc).isoformat(),
                        model="deepseek-flash",
                        reserved_chat_requests=1,
                    )
                )
            )
        result["chat_requests"] = 1
        result["billing"] = "unknown_until_response"
        answer = adapter.generate(
            messages=[{"role": "user", "content": 'Return only JSON {"ok":true}.'}],
            max_tokens=32,
            json_schema={
                "type": "object",
                "properties": {"ok": {"const": True}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            temperature=0,
        )
        ensure(answer.data == {"ok": True}, "真实结构化响应应严格符合JSON Schema")
        ensure(answer.model == "deepseek-flash", "实际调用模型保持指定身份")
        ensure(answer.formal_scoring_eligible is False, "连通成功不得授予正式评分资格")
        result["response"] = dict(
            model=answer.model,
            data=answer.data,
            usage=answer.usage,
            request_id=answer.request_id,
            response_id=answer.response_id,
            finish_reason=answer.finish_reason,
            formal_scoring_eligible=answer.formal_scoring_eligible,
        )
        result["billing"] = "provider_usage_recorded"
        result["checks"].append("实际adapter唯一短chat返回有效JSON、指定模型、usage和requestID")
        result["passed"] = True
    except FileExistsError:
        result["failure"] = dict(
            code="PAID_CHAT_GUARD_EXISTS", message="已有唯一调用占位，禁止再次付费请求"
        )
    except Exception as error:
        result["failure"] = safe_error(error)
        if isinstance(error, AssertionError):
            result["failure"]["message"] = str(error)
        for field in ["code", "status", "request_id"]:
            if hasattr(error, field):
                result["failure"][field] = getattr(error, field)
        details = getattr(error, "details", {})
        for field in ["billing_state", "external_attempts", "provider_request_id", "usage"]:
            if field in details:
                result["failure"][field] = details[field]
        if "billing_state" in details:
            result["billing"] = details["billing_state"]
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    write(output, result)
    print(
        dict(
            passed=result["passed"],
            chat_requests=result["chat_requests"],
            billing=result.get("billing"),
            failure=result.get("failure"),
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
