"""Actual lifecycle read/command schema; wiring does not prove every producer is guarded."""

from copy import deepcopy


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


ID = {"type": "string", "format": "uuid"}
NULL_ID = {"type": ["string", "null"], "format": "uuid"}
NULL_TIME = {"type": ["string", "null"], "format": "date-time"}
ACTION = {"enum": ["start", "end", "terminate", "archive", "reopen"]}
SCHEMAS = {
    "BatchClosureRequest": obj(
        {"reason": {"type": "string", "minLength": 1, "maxLength": 4000, "pattern": r"\S"}}
    ),
    "BatchClosureView": obj(
        {
            "batch": obj(
                {
                    "id": ID,
                    "bid_id": ID,
                    "state": {"enum": ["open", "verifying", "committed", "failed"]},
                    "revision": {"type": "integer", "minimum": 1},
                }
            ),
            "uploads": {
                "type": "array",
                "items": obj(
                    {
                        "id": ID,
                        "state": {
                            "enum": [
                                "authorized",
                                "uploaded",
                                "verifying",
                                "verified",
                                "quarantined",
                                "expired",
                            ]
                        },
                        "verified_version_id": NULL_ID,
                        "revision": {"type": ["integer", "null"], "minimum": 1},
                        "aborted_at": NULL_TIME,
                        "committed_binding_id": NULL_ID,
                    }
                ),
            },
            "closure": {
                "anyOf": [
                    {"type": "null"},
                    obj(
                        {
                            "id": ID,
                            "bid_id": ID,
                            "batch_id": ID,
                            "actor_id": ID,
                            "command_id": ID,
                            "reason": {"type": "string"},
                            "created_at": {"type": "string", "format": "date-time"},
                        }
                    ),
                ]
            },
            "available": {"type": "boolean"},
        }
    ),
    "ProjectLifecycleRequest": obj(
        {
            "action": ACTION,
            "reason": {"type": "string", "minLength": 1, "maxLength": 4000, "pattern": r"\S"},
        }
    ),
    "ProjectLifecycleView": obj(
        {
            "bid_id": ID,
            "phase": {"enum": ["preparation", "working", "submitted", "closed", "terminated"]},
            "archived_at": {"type": ["string", "null"], "format": "date-time"},
            "revision": {"type": "integer", "minimum": 1},
            "allowed_actions": {"type": "array", "items": ACTION, "uniqueItems": True},
            "archive_blockers": {
                "type": "array",
                "items": {
                    "enum": ["OPERATIONS_IN_FLIGHT", "UPLOADS_UNFINISHED", "BATCHES_UNFINISHED"]
                },
            },
            "batch_closure_available": {"type": "boolean"},
            "unfinished_uploads": {
                "type": "array",
                "items": obj(
                    {
                        "id": ID,
                        "batch_id": ID,
                        "state": {"enum": ["authorized", "uploaded", "verifying", "verified"]},
                        "verified_version_id": {"type": ["string", "null"], "format": "uuid"},
                        "aborted_at": {"type": ["string", "null"], "format": "date-time"},
                    }
                ),
            },
            "unfinished_batches": {
                "type": "array",
                "items": obj(
                    {
                        "id": ID,
                        "state": {"enum": ["open", "verifying"]},
                        "revision": {"type": "integer", "minimum": 1},
                    }
                ),
            },
            "active_operations": {
                "type": "array",
                "items": obj(
                    {
                        "id": ID,
                        "kind": {"type": "string"},
                        "state": {"enum": ["queued", "running", "cancel_requested"]},
                    }
                ),
            },
        }
    ),
}


def extend_openapi(actual):
    path = "/api/v1/bids/{bid_id}/lifecycle"
    if path not in actual["paths"]:
        return actual
    actual.setdefault("components", {}).setdefault("schemas", {}).update(deepcopy(SCHEMAS))
    for method, name in (("get", "getProjectLifecycle"), ("post", "changeProjectLifecycle")):
        if method not in actual["paths"][path]:
            continue
        op = actual["paths"][path][method]
        op["operationId"] = name
        if method == "post":
            op["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ProjectLifecycleRequest"}
                    }
                },
            }
        op["responses"]["200"] = {
            "description": "业务阶段与归档维度；不代替实际提交事实",
            "headers": {"ETag": {"schema": {"type": "string"}}},
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ProjectLifecycleView"}
                }
            },
        }
    for suffix, method, name in (
        ("closure-state", "get", "getBatchClosureState"),
        ("close", "post", "closeBatchKeepStaging"),
    ):
        batch_path = "/api/v1/bids/{bid_id}/upload-batches/{batch_id}/" + suffix
        if batch_path not in actual["paths"] or method not in actual["paths"][batch_path]:
            continue
        op = actual["paths"][batch_path][method]
        op["operationId"] = name
        op["responses"]["200"] = {
            "description": "明确结束未入库意图并保留暂存资料；不更改已入库记录",
            "headers": {"ETag": {"schema": {"type": "string"}}},
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/BatchClosureView"}}
            },
        }
        if method == "post":
            op["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/BatchClosureRequest"}
                    }
                },
            }
    return actual
