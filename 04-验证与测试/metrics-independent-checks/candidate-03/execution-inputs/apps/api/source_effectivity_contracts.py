"""补遗效力实际契约；目标快照保持不变。"""

import json
from copy import deepcopy

from jsonschema import Draft202012Validator

from packages.domain.runtime import FORMAT_CHECKER, ROOT, DomainError


def obj(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


UUID = {"type": "string", "format": "uuid"}
NULL_UUID = {"type": ["string", "null"], "format": "uuid"}
IDS = {"type": "array", "items": UUID, "uniqueItems": True}
TEXT = {"type": "string"}


def models(reference):
    quote = obj(
        {"evidence_id": UUID, "quote": {"type": "string", "minLength": 1, "maxLength": 20000}}
    )
    return {
        "SourceEffectivityRequest": obj(
            {
                "base_rule_set_id": UUID,
                "action": {"enum": ["whole_replace", "partial_replace", "defer"]},
                "old_document_version_id": UUID,
                "new_document_version_id": NULL_UUID,
                "rule_ids": IDS | {"maxItems": 500},
                "scope_basis": {"type": "array", "items": quote, "maxItems": 100},
                "additions": {"type": "array", "items": reference("RuleAddition"), "maxItems": 100},
                "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
            }
        )
        | {
            "allOf": [
                {
                    "if": {"properties": {"action": {"const": "defer"}}},
                    "then": {
                        "properties": {
                            "rule_ids": {"minItems": 1},
                            "scope_basis": {"maxItems": 0},
                            "additions": {"maxItems": 0},
                        }
                    },
                    "else": {
                        "properties": {
                            "new_document_version_id": UUID,
                            "scope_basis": {"minItems": 1},
                        }
                    },
                },
                {
                    "if": {"properties": {"action": {"const": "whole_replace"}}},
                    "then": {"properties": {"rule_ids": {"maxItems": 0}}},
                },
                {
                    "if": {"properties": {"action": {"const": "partial_replace"}}},
                    "then": {"properties": {"rule_ids": {"minItems": 1}}},
                },
            ]
        },
        "SourceEffectivitySource": obj(
            {
                "document_version_id": UUID,
                "original_name": TEXT,
                "roles": {"type": "array", "items": TEXT},
                "current": {"type": "boolean"},
                "parse_run_id": NULL_UUID,
                "coverage": {"enum": ["pending", "complete", "partial", "failed"]},
                "evidence_ids": IDS,
            }
        ),
        "SourceEffectivityView": obj(
            {
                "bid_id": UUID,
                "material_snapshot_id": NULL_UUID,
                "rule_set_id": NULL_UUID,
                "rules": {"type": "array", "items": reference("RuleView")},
                "sources": {"type": "array", "items": reference("SourceEffectivitySource")},
                "source_authority_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "last_decision_id": NULL_UUID,
                "requires_review": {"type": "boolean"},
            }
        ),
    }


def validate_request(body):
    definitions = json.loads(
        (ROOT / "packages/contracts/schemas/protocol.schema.json").read_text()
    )["$defs"]
    definitions.update(models(lambda name: {"$ref": "#/$defs/" + name}))
    errors = list(
        Draft202012Validator(
            {"$ref": "#/$defs/SourceEffectivityRequest", "$defs": definitions},
            format_checker=FORMAT_CHECKER,
        ).iter_errors(body)
    )
    if errors:
        raise DomainError(
            "INVALID_REQUEST",
            "请核对效力动作、影响条款、逐字依据和理由",
            422,
            {
                "fields": [
                    {"path": "/".join(map(str, error.path)), "constraint": error.validator}
                    for error in errors[:20]
                ]
            },
        )


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    schemas.update(deepcopy(models(lambda name: {"$ref": "#/components/schemas/" + name})))
    specifications = [
        (
            "/api/v1/bids/{bid_id}/source-effectivity",
            "get",
            "getSourceEffectivity",
            None,
            "SourceEffectivityView",
        ),
        (
            "/api/v1/bids/{bid_id}/source-effectivity-decisions",
            "post",
            "decideSourceEffectivity",
            "SourceEffectivityRequest",
            "RuleSetView",
        ),
    ]
    for path, method, operation_id, request, response in specifications:
        if path not in actual["paths"]:
            continue
        operation = actual["paths"][path][method]
        operation.update(operationId=operation_id, security=[{"SessionCookie": []}])
        if request:
            operation["parameters"] += [
                {"$ref": "#/components/parameters/" + name}
                for name in ("CSRF", "IdempotencyKey", "IfMatch")
            ]
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + request}}
                },
            }
        operation["responses"] = {
            "200": {
                "description": "有据更新要求后须明确重新评审；不自动判断正文满足",
                "headers": {"ETag": {"schema": {"type": "string"}}},
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + response}}
                },
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
