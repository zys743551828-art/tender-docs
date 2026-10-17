"""只覆盖已实现的single/原生OSS分片传输与布局读取。"""

from copy import deepcopy


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    schemas["UploadBatchPage"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["items", "has_more", "next_cursor"],
        "properties": {
            "items": {"type": "array", "items": {"$ref": "#/components/schemas/UploadBatchView"}},
            "has_more": {"type": "boolean"},
            "next_cursor": {"type": ["string", "null"]},
        },
    }
    batches = actual["paths"].get("/api/v1/bids/{bid_id}/upload-batches", {}).get("get")
    if batches:
        batches.update(operationId="listUploadBatches", security=[{"SessionCookie": []}])
        batches["responses"] = {
            "200": {
                "description": "服务器批次发现，按UUID稳定倒序；筛选条件参与游标身份，不宣称时间排序",
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/UploadBatchPage"}}
                },
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
    schemas["UploadLayoutView"] = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "upload_id",
            "bid_id",
            "mode",
            "part_size",
            "part_count",
            "byte_size",
            "declared_sha256",
            "revision",
            "state",
            "session_expires_at",
        ],
        "properties": {
            "upload_id": {"type": "string", "format": "uuid"},
            "bid_id": {"type": "string", "format": "uuid"},
            "mode": {"enum": ["single", "multipart"]},
            "part_size": {"type": "integer", "minimum": 1},
            "part_count": {"type": "integer", "minimum": 1, "maximum": 10000},
            "byte_size": {"type": "integer", "minimum": 1},
            "declared_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "revision": {"type": "integer", "minimum": 1},
            "state": deepcopy(schemas["UploadView"]["properties"]["state"]),
            "session_expires_at": {"type": "string", "format": "date-time"},
        },
    }
    path = "/api/v1/uploads/{upload_id}/layout"
    if path in actual["paths"]:
        operation = actual["paths"][path]["get"]
        operation.update(operationId="getUploadLayout", security=[{"SessionCookie": []}])
        operation["responses"] = {
            "200": {
                "description": "数据库权威传输布局；此响应不包含上传授权",
                "headers": {"ETag": {"schema": {"type": "string"}}},
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/UploadLayoutView"}
                    }
                },
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
    path = "/api/v1/object-transfers/multipart/{upload_id}/parts/{part_number}"
    if path in actual["paths"]:
        operation = actual["paths"][path]["put"]
        operation["parameters"] += [
            {"in": "header", "name": name, "required": True, "schema": definition}
            for name, definition in [
                ("Origin", {"type": "string", "format": "uri"}),
                ("X-CSRF-Token", {"type": "string"}),
                ("Content-Length", {"type": "integer", "minimum": 1}),
            ]
        ]
    complete = actual["paths"].get("/api/v1/uploads/{upload_id}/complete", {}).get("post")
    if complete:
        condition = deepcopy(actual["components"]["parameters"]["IfMatch"])
        condition.update(
            required=False,
            description="multipart必须携带GET upload或layout的当前强ETag；single沿用既有完成语义。",
        )
        complete["parameters"].append(condition)
        complete["description"] = (
            "single沿用原件SHA核验；multipart必须以GET upload/layout的强ETag和完整真实分片收据提交，丢响应使用原Idempotency-Key、If-Match和请求重放。"
        )
