"""Closed assistant actual inputs and model output. No tool is inferred from prose."""

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from packages.domain.runtime import DomainError, sha

ID = {"type": "string", "format": "uuid"}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
TEXT = {"type": "string", "minLength": 1}
NODE_ID = TEXT | {"maxLength": 128}


def obj(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


def array(item, maximum=50, minimum=0):
    return {"type": "array", "items": item, "maxItems": maximum, "minItems": minimum}


SOURCE_REF = obj(
    {
        "kind": {"enum": ["project", "document", "section", "knowledge_unit"]},
        "id": ID,
        "version": HASH,
        "trace_id": {"type": ["string", "null"], "format": "uuid"},
        "physical_page": {"type": "integer", "minimum": 1},
    },
    ["kind", "id", "version", "trace_id"],
)
TOOL_SCHEMAS = [
    obj({"tool": {"const": "review.request"}, "target_bid_id": ID, "arguments": obj({})}),
    obj(
        {
            "tool": {"const": "authoring.generate"},
            "target_bid_id": ID,
            "arguments": obj({"section_id": ID, "instruction": TEXT | {"maxLength": 8000}}),
        }
    ),
    obj(
        {
            "tool": {"const": "knowledge.candidate"},
            "target_bid_id": ID,
            "arguments": obj(
                {
                    "title": TEXT | {"maxLength": 200},
                    "content": TEXT | {"maxLength": 20000},
                    "scope": TEXT | {"maxLength": 2000},
                    "source_version_ids": array(ID, 30, 1),
                }
            ),
        }
    ),
]
MODEL_SCHEMA = obj(
    {
        "answer": TEXT | {"maxLength": 20000},
        "citations": array(obj({"source_key": TEXT, "quote": TEXT | {"maxLength": 4000}}), 30),
        "issue_suggestions": array(
            obj(
                {
                    "statement": TEXT | {"maxLength": 2000},
                    "severity": {"enum": ["critical", "major", "minor", "info"]},
                    "source_keys": array(TEXT, 30),
                    "needs_basis": {"type": "boolean"},
                }
            ),
            20,
        ),
        "tool_proposals": array({"oneOf": TOOL_SCHEMAS}, 8),
        "gaps": array(TEXT | {"maxLength": 2000}, 30),
    }
)
SCHEMAS = {
    "AssistantSourceRef": SOURCE_REF,
    "AssistantTurnRequest": obj(
        {
            "client_message_id": ID,
            "text": TEXT | {"maxLength": 20000, "pattern": r"\S"},
            "source_selection": array(SOURCE_REF, 30),
            "section_id": {"type": ["string", "null"], "format": "uuid"},
            "max_tokens": {"type": "integer", "minimum": 1, "maximum": 4096},
        },
        ["client_message_id", "text", "source_selection"],
    ),
    "AssistantActionResolveRequest": obj({"decision": {"enum": ["confirm", "reject"]}}),
    "AssistantModelOutput": MODEL_SCHEMA,
    "AssistantGenerationRequest": obj(
        {
            "section_id": ID,
            "base_section_revision": {"type": "integer", "minimum": 1},
            "instruction": TEXT | {"maxLength": 8000},
            "source_version_ids": array(ID, 30),
            "node_id": NODE_ID,
            "selection_start": {"type": "integer", "minimum": 0},
            "selection_end": {"type": "integer", "minimum": 0},
        },
        ["section_id", "base_section_revision", "instruction", "source_version_ids"],
    ),
}


def validate(name, body):
    if not Draft202012Validator(SCHEMAS[name], format_checker=FormatChecker()).is_valid(body):
        raise DomainError("ASSISTANT_INVALID_INPUT", "助手输入或输出不符合受控协议", 422)

    def strings(value):
        if isinstance(value, str):
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError:
                raise DomainError(
                    "ASSISTANT_INVALID_INPUT", "助手文本必须是有效UTF-8", 422
                ) from None
            if any(ord(char) < 32 and char not in "\r\n\t" for char in value):
                raise DomainError("ASSISTANT_INVALID_INPUT", "助手文本含不可保存的控制字符", 422)
        elif isinstance(value, dict):
            for item in value.values():
                strings(item)
        elif isinstance(value, list):
            for item in value:
                strings(item)

    strings(body)
    return body


def normalized_turn(body):
    validate("AssistantTurnRequest", body)
    for source in body["source_selection"]:
        if (source["kind"] == "knowledge_unit") != bool(source["trace_id"]) or (
            "physical_page" in source and source["kind"] != "document"
        ):
            raise DomainError("ASSISTANT_INVALID_INPUT", "来源类型与解析或检索定位不一致", 422)
    return deepcopy(body) | {
        "source_selection": sorted(deepcopy(body["source_selection"]), key=sha),
        "section_id": body.get("section_id"),
        "max_tokens": body.get("max_tokens", 2048),
    }
