"""本批已注册的准备请求与冻结身份读取；不复制计划中的未实现路径。"""


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    uuid = {"type": "string", "format": "uuid"}
    nullable_uuid = {"type": ["string", "null"], "format": "uuid"}

    def ref(name):
        return {"$ref": "#/components/schemas/" + name}

    def obj(properties):
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties),
        }

    schemas["ReviewIntentView"] = obj(
        {
            "request_id": uuid,
            "trigger": {"enum": ["manual", "automatic", "build_follow_up"]},
            "body_version_id": uuid,
            "material_snapshot_id": uuid,
            "material_revision": {"type": "integer", "minimum": 1},
            "state": {
                "enum": [
                    "preparing",
                    "blocked",
                    "superseded",
                    "queued",
                    "running",
                    "cancel_requested",
                    "completed",
                    "cancelled",
                ]
            },
            "prepare_operation_id": uuid,
            "review_id": nullable_uuid,
            "error_code": {"type": ["string", "null"]},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "updated_at": {"type": "string", "format": "date-time"},
        }
    )
    schemas["ReviewStateView"] = obj(
        {
            "bid_id": uuid,
            "revision": {"type": "integer", "minimum": 1},
            "current_body_id": nullable_uuid,
            "material_revision": {"type": "integer", "minimum": 1},
            "current_material_snapshot_id": nullable_uuid,
            "current_rule_set_id": nullable_uuid,
            "request": {"anyOf": [ref("ReviewIntentView"), {"type": "null"}]},
            "current_review": {"anyOf": [ref("ReviewView"), {"type": "null"}]},
        }
    )
    schemas["EvidenceAuthoringReferenceView"] = obj(
        {
            "evidence_id": uuid,
            "bid_id": uuid,
            "parse_run_id": uuid,
            "document_version_id": uuid,
            "physical_page": {"type": "integer", "minimum": 1},
            "authoring_reference": {"anyOf": [ref("AuthoringSourceReference"), {"type": "null"}]},
            "reasons": {"type": "array", "items": {"type": "string"}},
        }
    )
    schemas["BuildPage"] = obj(
        {
            "items": {"type": "array", "items": ref("BuildView")},
            "next_cursor": {"type": ["string", "null"]},
            "has_more": {"type": "boolean"},
        }
    )
    schemas["ReviewPage"] = obj(
        {
            "items": {"type": "array", "items": ref("ReviewView")},
            "next_cursor": {"type": ["string", "null"]},
            "has_more": {"type": "boolean"},
        }
    )
    mappings = {
        "/api/v1/bids/{bid_id}/builds": ("listBuilds", "BuildPage"),
        "/api/v1/evidence/{evidence_id}/authoring-reference": (
            "getEvidenceAuthoringReference",
            "EvidenceAuthoringReferenceView",
        ),
        "/api/v1/bids/{bid_id}/review-state": ("getReviewState", "ReviewStateView"),
        "/api/v1/bids/{bid_id}/reviews": ("listReviews", "ReviewPage"),
        "/api/v1/reviews/{review_id}/manifest": ("getReviewManifest", "ReviewManifest"),
        "/api/v1/rules/{rule_id}": ("getRule", "RuleView"),
        "/api/v1/findings/{finding_id}": ("getFinding", "FindingView"),
    }
    for path, (operation_id, model) in mappings.items():
        if path not in actual["paths"] or "get" not in actual["paths"][path]:
            continue
        operation = actual["paths"][path]["get"]
        operation["operationId"] = operation_id
        operation["security"] = [{"SessionCookie": []}]
        operation["responses"] = {
            "200": {
                "description": "实际可读取的服务器投影",
                "headers": {"ETag": {"schema": {"type": "string"}, "description": "真实强ETag"}},
                "content": {"application/json": {"schema": ref(model)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }

    path = "/api/v1/bids/{bid_id}/review-requests/{request_id}/cancel"
    if path in actual["paths"]:
        operation = actual["paths"][path]["post"]
        operation.update(operationId="cancelReviewRequest", security=[{"SessionCookie": []}])
        operation["parameters"] = operation.get("parameters", []) + [
            {"$ref": "#/components/parameters/IdempotencyKey"},
            {"name": "If-Match", "in": "header", "required": True, "schema": {"type": "string"}},
        ]
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": ref("Cancellation")}},
        }
        operation["responses"] = {
            "200": {
                "description": "本请求已取消；保留资料与共享准备",
                "content": {"application/json": {"schema": ref("ReviewStateView")}},
                "headers": {"ETag": {"schema": {"type": "string"}}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
