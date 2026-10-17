"""实际注册的确定性两轮比较契约，未提供的同方法重跑明确拒绝。"""

from copy import deepcopy


def obj(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    uuid = {"type": "string", "format": "uuid"}
    nullable = {"type": ["string", "null"], "format": "uuid"}
    text = {"type": "string"}
    texts = {"type": "array", "items": text}

    def ref(name):
        return {"$ref": "#/components/schemas/" + name}

    schemas["ReviewComparisonResult"] = obj(
        {
            "comparison_method_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "left_review_id": uuid,
            "right_review_id": uuid,
            "left_manifest": ref("ReviewManifest"),
            "right_manifest": ref("ReviewManifest"),
            "comparable_for_content_attribution": {"type": "boolean"},
            "break_reasons": texts,
            "score_delta": {"type": "null"},
            "score_delta_reason": text,
            "scope_note": text,
            "findings": {
                "type": "array",
                "items": obj(
                    {
                        "root_id": uuid,
                        "left_finding_id": nullable,
                        "right_finding_id": nullable,
                        "status": {
                            "enum": ["new", "persisting", "resolved_by_recheck", "not_rechecked"]
                        },
                        "basis": text,
                        "statement": text,
                    }
                ),
            },
            "body_changes": obj(
                {
                    "state": {"enum": ["complete", "partial"]},
                    "gaps": texts,
                    "total_change_groups": {"type": ["integer", "null"], "minimum": 0},
                    "items": {
                        "type": "array",
                        "maxItems": 2000,
                        "items": obj(
                            {
                                "kind": {"enum": ["replace", "delete", "insert"]},
                                "left_block_range": {
                                    "type": "array",
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "items": {"type": "integer", "minimum": 0},
                                },
                                "right_block_range": {
                                    "type": "array",
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "items": {"type": "integer", "minimum": 0},
                                },
                                "left_text": texts,
                                "right_text": texts,
                            }
                        ),
                    },
                }
            ),
        }
    )
    properties = {
        "comparison_method_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "id": uuid,
        "bid_id": uuid,
        "operation_id": uuid,
        "left_review_id": uuid,
        "right_review_id": uuid,
        "state": {
            "enum": ["queued", "running", "succeeded", "failed", "cancel_requested", "cancelled"]
        },
        "result_blob_id": nullable,
        "result_digest": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        "error_code": {"type": ["string", "null"]},
        "current_context_matches": {"type": "boolean"},
    }
    schemas["ReviewComparisonSummary"] = obj(deepcopy(properties))
    schemas["ReviewComparisonView"] = obj(
        deepcopy(properties)
        | {"result": {"anyOf": [ref("ReviewComparisonResult"), {"type": "null"}]}}
    )
    schemas["ReviewComparisonPage"] = obj(
        {
            "items": {"type": "array", "items": ref("ReviewComparisonSummary")},
            "has_more": {"type": "boolean"},
            "next_cursor": {"type": ["string", "null"]},
        }
    )
    for path, name, model in [
        ("/api/v1/comparisons/{comparison_id}", "getReviewComparison", "ReviewComparisonView"),
        ("/api/v1/bids/{bid_id}/comparisons", "listReviewComparisons", "ReviewComparisonPage"),
    ]:
        if path not in actual["paths"] or "get" not in actual["paths"][path]:
            continue
        operation = actual["paths"][path]["get"]
        operation.update(operationId=name, security=[{"SessionCookie": []}])
        operation["responses"] = {
            "200": {
                "description": "所选真实轮次的持久比较记录；当前资格变化单独标识",
                "headers": {"ETag": {"schema": text}},
                "content": {"application/json": {"schema": ref(model)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
