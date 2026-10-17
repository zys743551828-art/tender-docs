"""Closed actual knowledge RAG protocol; no alteration of frozen target contracts."""

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from packages.domain.runtime import DomainError
from packages.knowledge.contracts import (
    HASH,
    ID,
    POS,
    PURPOSE,
    TEXT,
    TIME,
    array,
    obj,
    ref,
)
from packages.knowledge.contracts import (
    schemas as knowledge_schemas,
)


def schemas():
    derived = obj(
        {
            "kind": {"const": "derived_knowledge"},
            "offset_basis": {"const": "knowledge_content"},
            "approved_references": array(ref("KnowledgeSourcePosition"), minItems=1),
            "verbatim_original": {"const": False},
        }
    )
    original = obj(
        {
            "kind": {"const": "original_quote"},
            "offset_basis": {"const": "approved_excerpt"},
            "reference": ref("KnowledgeSourcePosition"),
            "verbatim_original": {"const": True},
        }
    )
    provenance = {"oneOf": [ref("KnowledgeDerivedProvenance"), ref("KnowledgeQuoteProvenance")]}
    hit = obj(
        {
            "unit_id": ID,
            "unit_source_id": ID,
            "unit_kind": {"enum": ["knowledge_text", "original_quote"]},
            "knowledge_id": ID,
            "knowledge_version_id": ID,
            "knowledge_source_id": ID,
            "index_id": ID,
            "text": TEXT,
            "span_start": {"type": "integer", "minimum": 0},
            "span_end": POS,
            "offset_basis": {"enum": ["knowledge_content", "approved_excerpt"]},
            "provenance": provenance,
            "rank": {"type": "integer", "minimum": 1, "maximum": 8},
            "reference_only": {"const": True},
            "project_fact_created": {"const": False},
        }
    )
    result = obj(
        {
            "trace_id": ID,
            "profile_id": ID,
            "policy_digest": HASH,
            "coverage": {"enum": ["partial", "unavailable"]},
            "hits": array(ref("KnowledgeRetrievalHit"), maxItems=8),
            "gaps": array(TEXT),
            "route": array(TEXT),
        }
    )
    request = obj(
        {
            "query": TEXT | {"maxLength": 10000, "pattern": r"\S"},
            "purpose": PURPOSE,
            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
            "knowledge_ids": array(ID, minItems=1, maxItems=100, uniqueItems=True),
        },
        ["query", "purpose", "limit"],
    )
    reference = obj(
        {
            "citation_id": ID,
            "trace_id": ID,
            "bid_id": ID,
            "unit_id": ID,
            "unit_kind": {"enum": ["knowledge_text", "original_quote"]},
            "knowledge_version_id": ID,
            "quote": TEXT,
            "unit_text": TEXT,
            "unit_span_start": {"type": "integer", "minimum": 0},
            "unit_span_end": POS,
            "target_kind": {"enum": ["project", "section", "operation", "review"]},
            "target_object_id": ID,
            "applicability_reason": TEXT,
            "provenance": provenance,
            "project_context": ref("KnowledgeProjectContext"),
            "reference_only": {"const": True},
            "project_fact_created": {"const": False},
        }
    )
    operation = obj(
        {
            "id": ID,
            "bid_id": ID,
            "kind": {"const": "index-knowledge"},
            "state": {
                "enum": [
                    "queued",
                    "running",
                    "cancel_requested",
                    "succeeded",
                    "failed",
                    "cancelled",
                ]
            },
            "input_digest": HASH,
            "handler_version": {"const": "knowledge-index/1"},
            "result_ref": {"type": ["string", "null"]},
        }
    )
    index = obj(
        {
            "index_id": ID,
            "knowledge_version_id": ID,
            "profile_id": ID,
            "operation_id": ID,
            "state": {"enum": ["queued", "ready", "failed", "superseded"]},
            "expected_unit_count": {"type": ["integer", "null"], "minimum": 1, "maximum": 1000},
            "published_unit_count": {"type": "integer", "minimum": 0, "maximum": 1000},
            "unit_manifest_digest": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
            "current_ready": {"type": "boolean"},
            "desired": {"type": "boolean"},
            "error_code": {"type": ["string", "null"]},
        }
    )
    return {
        "KnowledgeDerivedProvenance": derived,
        "KnowledgeQuoteProvenance": original,
        "KnowledgeRetrievalHit": hit,
        "KnowledgeRetrievalRequest": request,
        "KnowledgeRetrievalResult": result,
        "KnowledgeRetrievalHistory": obj(
            {
                "retrieval": ref("KnowledgeRetrievalResult"),
                "evaluated_at": TIME,
                "current_reasons": array(TEXT),
                "historical_bytes_preserved": {"const": True},
            }
        ),
        "KnowledgeUnitReferenceRequest": obj(
            {
                "unit_id": ID,
                "quote": TEXT | {"maxLength": 20000, "pattern": r"\S"},
                "target_kind": {"enum": ["project", "section", "operation", "review"]},
                "target_object_id": ID,
                "applicability_reason": TEXT | {"maxLength": 4000, "pattern": r"\S"},
            }
        ),
        "KnowledgeUnitReference": reference,
        "KnowledgeUnitReferenceHistory": obj(
            {
                "reference": ref("KnowledgeUnitReference"),
                "current_reasons": array(TEXT),
                "historical_bytes_preserved": {"const": True},
            }
        ),
        "KnowledgeIndexRequest": obj({"knowledge_version_id": ID}),
        "KnowledgeIndexAccepted": obj({"index_id": ID, "operation": operation}),
        "KnowledgeIndexPage": obj({"items": array(index, maxItems=100)}),
    }


def validate_actual(name, body):
    definitions = knowledge_schemas() | schemas()
    validator = Draft202012Validator(
        {"components": {"schemas": definitions}, "$ref": "#/components/schemas/" + name},
        format_checker=FormatChecker(),
    )
    if list(validator.iter_errors(body)):
        raise DomainError("INVALID_REQUEST", "知识检索请求字段不符合实际接口协议", 422)
    for field in ("query", "quote", "applicability_reason"):
        value = body.get(field) if isinstance(body, dict) else None
        if not isinstance(value, str):
            continue
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise DomainError("INVALID_REQUEST", "知识检索文本必须是有效UTF-8文本", 422) from None
        if any(ord(char) < 32 and char not in "\t\r\n" for char in value):
            raise DomainError("INVALID_REQUEST", "知识检索文本包含不支持的控制字符", 422)


def extend_openapi(actual):
    actual["components"]["schemas"].update(deepcopy(schemas()))
    routes = [
        (
            "/bids/{bid_id}/knowledge-retrievals",
            "post",
            "KnowledgeRetrievalRequest",
            "KnowledgeRetrievalResult",
            200,
        ),
        ("/knowledge-retrievals/{trace_id}", "get", None, "KnowledgeRetrievalHistory", 200),
        (
            "/knowledge-retrievals/{trace_id}/references",
            "post",
            "KnowledgeUnitReferenceRequest",
            "KnowledgeUnitReference",
            201,
        ),
        (
            "/knowledge-unit-references/{citation_id}",
            "get",
            None,
            "KnowledgeUnitReferenceHistory",
            200,
        ),
        (
            "/knowledge/{knowledge_id}/indexes",
            "post",
            "KnowledgeIndexRequest",
            "KnowledgeIndexAccepted",
            202,
        ),
        ("/knowledge/{knowledge_id}/indexes", "get", None, "KnowledgeIndexPage", 200),
    ]
    for path, method, request, response, status in routes:
        operation = actual["paths"].get("/api/v1" + path, {}).get(method)
        if operation is None:
            continue
        operation["operationId"] = method + response
        operation["security"] = [{"SessionCookie": []}]
        if request:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(request)}},
            }
        operation["responses"] = {
            str(status): {
                "description": "Actual knowledge RAG resource",
                "content": {"application/json": {"schema": ref(response)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
    return actual
