"""Only explicit in-process MockTransport; answers remain visibly synthetic."""

import json
from typing import Any
from uuid import uuid4

import httpx


def install_api_profile_only():
    """Allow real job/profile freezing without giving API a generation capability."""
    from packages.domain.providers.deepseek import DeepSeekClient

    def deny(request):
        raise RuntimeError("Fixture API profile port cannot execute generation")

    client = DeepSeekClient(
        api_key="explicit-offline-fixture-not-a-credential",
        transport=httpx.MockTransport(deny),
    )
    DeepSeekClient.from_env = classmethod(lambda cls: client)


def answer(manifest, mode):
    result: dict[str, Any] = {
        "answer": "显式离线合成机制回答：请核对原文。本回答用于界面验收，不代表真实模型效果或正式评审。",
        "citations": [],
        "issue_suggestions": [],
        "tool_proposals": [],
        "gaps": ["显式 MockTransport；零真实模型请求；未执行 Kafka；非业务准确率验收。"],
    }
    sources = manifest["context"]["sources"]
    documents = [s for s in sources if s["provenance"]["kind"] == "original_document"]
    if documents:
        source = documents[0]
        ranges = source["quote_ranges"]
        if ranges:
            span = ranges[0]
            quote = source["text"][span["start"] : span["end"]][:50].strip()
            if quote:
                result["citations"] = [{"source_key": source["source_key"], "quote": quote}]
    if mode == "knowledge_action":
        targets = manifest["context"]["target_bid_ids"]
        if not targets or not documents:
            raise ValueError(
                "knowledge_action requires trusted selected project and original document"
            )
        result["tool_proposals"] = [
            {
                "tool": "knowledge.candidate",
                "target_bid_id": targets[0],
                "arguments": {
                    "title": "显式合成浏览器知识待核候选",
                    "content": "合成原文仅供机制核对，尚未人工核验。",
                    "scope": "仅测试，待人工核验",
                    "source_version_ids": [documents[0]["provenance"]["document_version_id"]],
                },
            }
        ]
    return result


def client_for(manifest, mode, record):
    from packages.domain.providers.deepseek import DeepSeekClient

    def transport(request):
        record({"event": "mock_request", "mode": mode, "real_provider_attempts": 0})
        if mode == "unknown":
            raise httpx.ReadTimeout("explicit offline fixture unknown", request=request)
        value = answer(manifest, mode)
        return httpx.Response(
            200,
            json={
                "id": "explicit-mock-" + uuid4().hex,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(value, ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 20, "total_tokens": 32},
            },
        )

    return DeepSeekClient(
        api_key="explicit-offline-fixture-not-a-credential",
        transport=httpx.MockTransport(transport),
    )
