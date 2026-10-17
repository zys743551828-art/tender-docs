"""Actual execution schemas and routes, separate from frozen target contracts."""

from copy import deepcopy
from typing import Any

from packages.assistant.generation import GENERATION_SCHEMA
from packages.assistant.protocol import HASH, ID, MODEL_SCHEMA, array, obj
from packages.assistant.protocol import SCHEMAS as INPUTS

NID = {"type": ["string", "null"], "format": "uuid"}
TIME = {"type": "string", "format": "date-time"}
NTIME = {"type": ["string", "null"], "format": "date-time"}
STRING = {"type": "string"}
NSTRING = {"type": ["string", "null"]}
POS = {"type": "integer", "minimum": 1}
STATE = {"enum": ["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled"]}
TOOLS = {
    "enum": ["review.request", "authoring.generate", "authoring.apply_patch", "knowledge.candidate"]
}


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def nullable(schema):
    return {"oneOf": [schema, {"type": "null"}]}


def schemas():
    from packages.assistant.contracts import SCHEMAS as base

    result = deepcopy(INPUTS)
    position = obj(
        {
            "kind": {
                "enum": [
                    "structured_project_memory",
                    "original_document",
                    "draft_section",
                    "knowledge_retrieval_unit",
                ]
            },
            "bid_id": ID,
            "source_key": STRING,
            "version": HASH,
            "document_version_id": NID,
            "parse_id": NID,
            "physical_pages": array(POS, 500),
            "evidence_ids": array(ID, 500),
            "section_id": NID,
            "section_revision": {"type": ["integer", "null"], "minimum": 1},
            "knowledge_trace_id": NID,
            "knowledge_unit_id": NID,
            "verbatim_original": {"type": "boolean"},
            "knowledge_provenance": {
                "oneOf": [
                    ref("KnowledgeDerivedProvenance"),
                    ref("KnowledgeQuoteProvenance"),
                    {"type": "null"},
                ]
            },
        }
    )
    suggestion = obj(
        {
            "statement": STRING,
            "severity": {"enum": ["critical", "major", "minor", "info"]},
            "source_keys": array(STRING, 30),
            "needs_basis": {"type": "boolean"},
            "suggestion_id": ID,
            "record_kind": {"const": "assistant_suggestion"},
            "official_finding_id": {"type": "null"},
            "resolved": {"const": False},
        }
    )
    answer_part = obj(
        {
            "type": {"const": "assistant_reference_answer"},
            "job_id": ID,
            "citations": array(
                obj(
                    {
                        "source_key": STRING,
                        "quote": STRING,
                        "position": ref("AssistantCitationPosition"),
                    }
                ),
                30,
            ),
            "issue_suggestions": array(suggestion, 20),
            "action_ids": array(ID, 8),
            "rejected_proposals": array(
                obj({"tool": TOOLS, "target_bid_id": ID, "error_code": STRING}), 8
            ),
            "gaps": array(STRING, 30),
            "formal_scoring_input": {"const": False},
            "all_actions_require_explicit_button": {"const": True},
        }
    )
    work: dict[str, Any] = deepcopy(base["WorkMessageView"])
    work["properties"]["client_message_id"] = NID
    work["properties"]["assistant_operation_id"] = NID
    work["properties"].update(
        assistant_job_id=ID, assistant_operation_scope={"enum": ["project", "global"]}
    )
    work["properties"]["parts"]["items"]["oneOf"].append(ref("AssistantAnswerPart"))
    global_message = deepcopy(work)
    global_message["properties"]["bid_id"] = {"type": "null"}
    global_message["properties"]["scope"] = {"const": "global"}
    global_message["required"].append("scope")
    operation = obj(
        {
            "id": ID,
            "state": STATE,
            "cancel_requested_at": NTIME,
            "handler_version": STRING,
            "scope": {"enum": ["project", "global"]},
            "bid_id": NID,
            "error_code": NSTRING,
        },
        ["id", "state", "cancel_requested_at", "handler_version", "scope", "bid_id"],
    )
    publication = obj(
        {
            "state": {"enum": ["published", "stale", "failed"]},
            "project_message_id": NID,
            "global_message_id": NID,
            "action_id": NID,
            "error_code": NSTRING,
        }
    )
    candidate = deepcopy(GENERATION_SCHEMA)
    candidate["properties"]["replacement"] = ref("AuthoringNode")
    job = obj(
        {
            "id": ID,
            "kind": {"enum": ["answer", "authoring"]},
            "scope": {"enum": ["project", "global"]},
            "bid_id": NID,
            "conversation_id": NID,
            "user_message_id": NID,
            "operation": ref("AssistantExecution"),
            "cancel_requested": {"type": "boolean"},
            "cancellation_terminal": {"type": "boolean"},
            "model_call": nullable(
                obj(
                    {
                        "id": ID,
                        "state": {"enum": ["reserved", "succeeded", "failed", "unknown"]},
                        "billing_state": NSTRING,
                    }
                )
            ),
            "requires_billing_reconciliation": {"type": "boolean"},
            "publication": nullable(publication),
            "candidate": {"oneOf": [MODEL_SCHEMA, candidate, {"type": "null"}]},
            "formal_scoring_input": {"const": False},
            "automatic_retry_authorized": {"const": False},
        }
    )
    review = obj(
        {
            "kind": {"const": "request_review"},
            "body_version_id": ID,
            "material_revision": POS,
            "effect": STRING,
        }
    )
    generation = obj(
        {
            "kind": {"const": "generate_proposal"},
            "section_id": ID,
            "base_section_revision": POS,
            "instruction": STRING,
            "effect": STRING,
        }
    )
    knowledge = obj(
        {
            "kind": {"const": "knowledge_candidate"},
            "title": STRING,
            "content": STRING,
            "scope": STRING,
            "source_version_ids": array(ID, 30, 1),
            "effect": STRING,
        }
    )
    patch = obj(
        {
            "kind": {"const": "authoring_patch"},
            "section_id": ID,
            "base_section_revision": POS,
            "before": ref("AuthoringDocument"),
            "after": ref("AuthoringDocument"),
            "effect": STRING,
        }
    )
    accepted = obj(
        {
            "resource_id": ID,
            "resource_kind": {"const": "pending_intent"},
            "operation": ref("Operation"),
            "disposition": {"enum": ["created", "pending_latest"]},
            "operation_url": STRING,
        }
    )
    domain = nullable(
        {"oneOf": [ref("AssistantJob"), ref("KnowledgeView"), ref("DraftSaved"), accepted]}
    )
    resolution = obj(
        {
            "action_id": ID,
            "state": {"enum": ["executed", "rejected", "expired", "stale", "failed"]},
            "tool": TOOLS,
            "target_bid_id": ID,
            "domain_result": domain,
            "error_code": NSTRING,
            "http_status": {"type": "integer", "minimum": 200, "maximum": 599},
            "business_effect_executed": {"type": "boolean"},
        }
    )
    action = obj(
        {
            "action_id": ID,
            "job_id": ID,
            "target_bid_id": ID,
            "tool": TOOLS,
            "state": {"enum": ["pending", "executed", "rejected", "expired", "stale", "failed"]},
            "revision": POS,
            "request_digest": HASH,
            "base_revision": POS,
            "expires_at": TIME,
            "preview": {"oneOf": [review, generation, knowledge, patch]},
            "result": {
                "oneOf": [
                    obj({"assistant_resolution": resolution}),
                    ref("DraftSaved"),
                    {"type": "null"},
                ]
            },
            "resolved_by": NID,
            "error_code": NSTRING,
            "formal_scoring_input": {"const": False},
        }
    )
    global_view = obj(
        {
            "id": ID,
            "bid_id": {"type": "null"},
            "scope": {"const": "global"},
            "title": STRING,
            "is_main": {"const": True},
            "state": {"enum": ["active", "closed", "hidden"]},
            "revision": POS,
            "stream_id": ID,
            "last_event_seq": {"type": "integer", "minimum": 0},
            "created_at": TIME,
        }
    )
    source = obj(
        {
            "reference": nullable(ref("AssistantSourceRef")),
            "title": STRING,
            "available": {"type": "boolean"},
            "reasons": array(STRING, 20),
            "bid_id": NID,
            "formal_scoring_input": {"const": False},
        }
    )
    result.update(
        {
            "WorkMessageView": work,
            "WorkMessagePage": obj(
                {
                    "items": array(ref("WorkMessageView"), 100),
                    "has_more": {"type": "boolean"},
                    "before": NID,
                }
            ),
            "AssistantCitationPosition": position,
            "AssistantAnswerPart": answer_part,
            "AssistantExecution": operation,
            "AssistantJob": job,
            "AssistantJobPage": obj({"items": array(ref("AssistantJob"), 100)}),
            "AssistantAction": action,
            "AssistantGlobalConversation": global_view,
            "AssistantConversationPage": obj(
                {
                    "items": array(
                        {
                            "oneOf": [
                                ref("WorkConversationView"),
                                ref("AssistantGlobalConversation"),
                            ]
                        },
                        100,
                    )
                }
            ),
            "AssistantGlobalMessage": global_message,
            "AssistantGlobalMessagePage": obj(
                {
                    "items": array(ref("AssistantGlobalMessage"), 100),
                    "has_more": {"type": "boolean"},
                    "before": NID,
                }
            ),
            "AssistantGlobalEventPage": obj(
                {
                    "stream_id": ID,
                    "items": array(
                        obj(
                            {
                                "seq": POS,
                                "event_id": ID,
                                "event_type": STRING,
                                "payload": {"type": "object", "minProperties": 1},
                                "created_at": TIME,
                            }
                        ),
                        100,
                    ),
                    "next_cursor": {"type": "integer", "minimum": 0},
                    "head_cursor": {"type": "integer", "minimum": 0},
                }
            ),
            "AssistantSourcePage": obj(
                {"items": array(source, 30), "coverage": {"const": "partial"}, "notice": STRING}
            ),
        }
    )
    return result


ROUTES = [
    ("/assistant/global-conversations/main", "get", None, "AssistantGlobalConversation", 200),
    ("/assistant/global-conversations/main", "post", None, "AssistantGlobalConversation", 200),
    ("/assistant/conversations", "get", None, "AssistantConversationPage", 200),
    (
        "/assistant/global-conversations/{conversation_id}/state",
        "post",
        "WorkConversationStateRequest",
        "AssistantGlobalConversation",
        200,
    ),
    (
        "/bids/{bid_id}/conversations/{conversation_id}/turns",
        "post",
        "AssistantTurnRequest",
        "AssistantJob",
        202,
    ),
    (
        "/assistant/global-conversations/{conversation_id}/turns",
        "post",
        "AssistantTurnRequest",
        "AssistantJob",
        202,
    ),
    (
        "/assistant/global-conversations/{conversation_id}/messages",
        "get",
        None,
        "AssistantGlobalMessagePage",
        200,
    ),
    (
        "/assistant/global-conversations/{conversation_id}/events",
        "get",
        None,
        "AssistantGlobalEventPage",
        200,
    ),
    ("/assistant/sources", "get", None, "AssistantSourcePage", 200),
    ("/assistant/jobs/{job_id}", "get", None, "AssistantJob", 200),
    ("/assistant/jobs", "get", None, "AssistantJobPage", 200),
    ("/assistant/jobs/{job_id}/cancel", "post", None, "AssistantJob", 202),
    ("/assistant/actions/{action_id}", "get", None, "AssistantAction", 200),
    (
        "/assistant/actions/{action_id}/resolve",
        "post",
        "AssistantActionResolveRequest",
        "AssistantAction",
        200,
    ),
    (
        "/bids/{bid_id}/authoring/generations",
        "post",
        "AssistantGenerationRequest",
        "AssistantJob",
        202,
    ),
]


def extend_openapi(actual):
    actual["components"]["schemas"].update(schemas())
    for path, method, request, response, status in ROUTES:
        operation = actual["paths"].get("/api/v1" + path, {}).get(method)
        if operation is None:
            continue
        operation["operationId"] = (
            method + response + path.replace("/", "_").replace("{", "").replace("}", "")
        )
        operation["security"] = [{"SessionCookie": []}]
        if method == "post":
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(request) if request else obj({})}},
            }
        operation["responses"] = {
            str(status): {
                "description": "真实助手记录/操作状态，不等同正式评审或领域完成",
                "content": {"application/json": {"schema": ref(response)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
        if response in ("AssistantAction", "AssistantGlobalConversation"):
            operation["responses"][str(status)]["headers"] = {"ETag": {"schema": STRING}}
        if path.endswith("/resolve"):
            for code in (409, 422):
                operation["responses"][str(code)] = {
                    "description": "保留真实过期/失效/失败动作卡",
                    "content": {"application/json": {"schema": ref(response)}},
                }
    return actual
