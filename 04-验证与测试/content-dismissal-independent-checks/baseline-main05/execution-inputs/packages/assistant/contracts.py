"""Implementation schemas for project conversation persistence; target contracts stay frozen."""

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from packages.domain.runtime import DomainError

SCHEMAS = {
    "WorkMessageRequest": {
        "type": "object",
        "additionalProperties": False,
        "required": ["client_message_id", "text", "source_version_ids"],
        "properties": {
            "client_message_id": {"type": "string", "format": "uuid"},
            "text": {"type": "string", "minLength": 1, "maxLength": 20000, "pattern": r"\S"},
            "source_version_ids": {
                "type": "array",
                "maxItems": 50,
                "uniqueItems": True,
                "items": {"type": "string", "format": "uuid"},
            },
        },
    },
    "WorkConversationStateRequest": {
        "type": "object",
        "additionalProperties": False,
        "required": ["state"],
        "properties": {"state": {"enum": ["active", "closed", "hidden"]}},
    },
}


def validate(name, body):
    if not Draft202012Validator(SCHEMAS[name], format_checker=FormatChecker()).is_valid(body):
        raise DomainError("INVALID_REQUEST", "请检查会话输入字段", 422)


def obj(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


ID = {"type": "string", "format": "uuid"}
TEXT_PART = obj({"type": {"const": "text"}, "text": {"type": "string"}})
REFERENCE_PART = obj(
    {"type": {"const": "source_reference"}, "document_version_id": ID, "source_bid_id": ID}
)
SCHEMAS.update(
    {
        "WorkConversationView": obj(
            {
                "id": ID,
                "bid_id": ID,
                "state": {"enum": ["active", "closed", "hidden"]},
                "revision": {"type": "integer", "minimum": 1},
                "created_at": {"type": "string", "format": "date-time"},
                "scope": {"const": "project"},
                "is_main": {"type": "boolean"},
            "title": {"type": "string"},
            "stream_id": {"type": ["string", "null"], "format": "uuid"},
            }
        ),
        "WorkMessageView": obj(
            {
                "id": ID,
                "bid_id": ID,
                "conversation_id": ID,
                "client_message_id": ID,
                "role": {"enum": ["user", "assistant", "tool"]},
                "parts": {"type": "array", "items": {"oneOf": [TEXT_PART, REFERENCE_PART]}},
                "created_by": ID,
                "created_at": {"type": "string", "format": "date-time"},
                "recorded": {"const": True},
                "assistant_operation_id": {"type": "null"},
                "formal_scoring_input": {"const": False},
            }
        ),
    }
)
SCHEMAS["WorkMessagePage"] = obj(
    {
        "items": {"type": "array", "items": SCHEMAS["WorkMessageView"]},
        "has_more": {"type": "boolean"},
        "before": {"type": ["string", "null"], "format": "uuid"},
    }
)


def extend_openapi(actual):
    base = "/api/v1/bids/{bid_id}/conversations"
    mappings = {
        (base + "/main", "get"): ("getMainWorkConversation", None, "WorkConversationView", 200),
        (base + "/main", "post"): ("ensureMainWorkConversation", None, "WorkConversationView", 200),
        (base + "/{conversation_id}", "get"): (
            "getWorkConversation",
            None,
            "WorkConversationView",
            200,
        ),
        (base + "/{conversation_id}/state", "post"): (
            "changeWorkConversationState",
            "WorkConversationStateRequest",
            "WorkConversationView",
            200,
        ),
        (base + "/{conversation_id}/messages", "get"): (
            "listWorkMessages",
            None,
            "WorkMessagePage",
            200,
        ),
        (base + "/{conversation_id}/messages", "post"): (
            "recordWorkMessage",
            "WorkMessageRequest",
            "WorkMessageView",
            201,
        ),
    }
    if not any(path in actual["paths"] for path, _ in mappings):
        return actual
    actual.setdefault("components", {}).setdefault("schemas", {}).update(deepcopy(SCHEMAS))
    for (path, method), (operation_id, request_name, response_name, status) in mappings.items():
        if method not in actual["paths"].get(path, {}):
            continue
        operation = actual["paths"][path][method]
        operation["operationId"] = operation_id
        if request_name or (path == base + "/main" and method == "post"):
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/" + request_name}
                        if request_name
                        else obj({})
                    }
                },
            }
        operation["responses"].pop("200", None)
        operation["responses"][str(status)] = {
            "description": "工作会话持久记录，不代表模型答复或正式操作已完成",
            "headers": {"ETag": {"schema": {"type": "string"}}}
            if response_name != "WorkMessagePage"
            else {},
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/" + response_name}}
            },
        }
    return actual
