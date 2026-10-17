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
ACTION = {"enum": ["start", "end", "terminate", "archive", "reopen"]}
SCHEMAS = {
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
            "archive_blockers": {"type": "array", "items": {"enum": ["OPERATIONS_IN_FLIGHT"]}},
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
    return actual
