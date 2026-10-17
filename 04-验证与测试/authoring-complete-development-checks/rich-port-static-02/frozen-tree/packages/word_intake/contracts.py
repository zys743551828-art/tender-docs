"""Actual external Word routes only; capability target schemas remain unchanged."""

from copy import deepcopy


def object_schema(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    uuid = {"type": "string", "format": "uuid"}
    nullable_uuid = {"type": ["string", "null"], "format": "uuid"}
    strings = {"type": "array", "items": {"type": "string"}}
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    schemas["WordInspectionView"] = object_schema(
        {
            "schema": {"const": "word-intake-inspection/1"},
            "container_validated": {"const": True},
            "entry_count": {"type": "integer", "minimum": 1},
            "expanded_bytes": {"type": "integer", "minimum": 1},
            "xml_part_count": {"type": "integer", "minimum": 1},
            "active_content_gaps": strings,
            "unsupported_layout_features": strings,
            "requested_fonts": strings,
            "conversion_eligible": {"type": "boolean"},
            "text_digest": digest,
        }
    )
    schemas["WordConversionView"] = object_schema(
        {
            "state": {
                "enum": ["pending", "running", "cancelling", "cancelled", "succeeded", "failed"]
            },
            "operation_id": nullable_uuid,
            "conversion_attempted": {"type": "boolean"},
            "coverage": {"enum": ["pending", "complete", "partial", "failed"]},
            "error_code": {"type": ["string", "null"]},
            "retryable": {"type": "boolean"},
            "gaps": strings,
            "pdf_document_version_id": nullable_uuid,
            "controlled_render_verified": {"type": "boolean"},
        }
    )
    action = object_schema(
        {
            "action": {"const": "upload_modified_pdf"},
            "label": {"const": "上传此修改稿的PDF"},
            "context": object_schema(
                {
                    "bid_id": uuid,
                    "role": {
                        "enum": [
                            "tender",
                            "supplement",
                            "drawing",
                            "boq",
                            "body",
                            "official_result",
                            "other",
                        ]
                    },
                    "declared_source_document_version_id": uuid,
                    "target": object_schema({"kind": {"const": "new_file"}}),
                }
            ),
        }
    )
    schemas["WordOriginalView"] = object_schema(
        {
            "id": uuid,
            "bid_id": uuid,
            "file_id": uuid,
            "original_name": {"type": "string"},
            "role": {"type": "string"},
            "original_saved": {"const": True},
            "source_sha256": digest,
            "byte_size": {"type": "integer", "minimum": 1},
            "inspection": {"$ref": "#/components/schemas/WordInspectionView"},
            "conversion": {"$ref": "#/components/schemas/WordConversionView"},
            "message": {"type": "string"},
            "delivery_check_message": {"const": "Word交付未获对应检查资格"},
            "review_qualification": {"const": "not_established_by_conversion"},
            "primary_action": action,
            "converted_pdf_action": {
                "anyOf": [
                    {"type": "null"},
                    object_schema({"action": {"const": "open_pdf"}, "document_version_id": uuid}),
                ]
            },
        }
    )
    schemas["WordOriginalPage"] = object_schema(
        {
            "items": {"type": "array", "items": {"$ref": "#/components/schemas/WordOriginalView"}},
            "has_more": {"type": "boolean"},
            "next_after": nullable_uuid,
        }
    )
    for path, name, identifier in [
        ("/api/v1/bids/{bid_id}/word-originals", "WordOriginalPage", "listWordOriginals"),
        (
            "/api/v1/bids/{bid_id}/word-originals/{version_id}",
            "WordOriginalView",
            "getWordOriginal",
        ),
    ]:
        operation = actual["paths"].get(path, {}).get("get")
        if operation:
            operation.update(operationId=identifier, security=[{"SessionCookie": []}])
            operation["responses"] = {
                "200": {
                    "description": "实际已保存原件与独立转换状态，不把转换当评审通过",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/" + name}}
                    },
                },
                "default": {"$ref": "#/components/responses/DomainError"},
            }
            if name == "WordOriginalView":
                operation["responses"]["200"]["headers"] = {"ETag": {"schema": {"type": "string"}}}
    retry = (
        actual["paths"]
        .get("/api/v1/bids/{bid_id}/word-originals/{version_id}/retry-conversion", {})
        .get("post")
    )
    if retry:
        retry.update(operationId="retryWordConversion", security=[{"SessionCookie": []}])
        retry["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": object_schema({})}},
        }
        retry["parameters"] += [
            deepcopy(actual["components"]["parameters"][name])
            for name in ("IdempotencyKey", "IfMatch")
        ]
        retry["responses"] = {
            "202": {
                "description": "受理明确原件的受控转换；失败/partial仍保留实际缺口",
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Accepted"}}
                },
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
