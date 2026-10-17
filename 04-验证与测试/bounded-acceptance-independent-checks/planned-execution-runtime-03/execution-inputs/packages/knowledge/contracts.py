"""Actual knowledge contract additions; frozen target schemas remain unchanged."""

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

from packages.domain.runtime import DomainError


def obj(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


def array(item, **constraints):
    return {"type": "array", "items": item, **constraints}


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


ID = {"type": "string", "format": "uuid"}
TIME = {"type": "string", "format": "date-time"}
NULLID = {"type": ["string", "null"], "format": "uuid"}
TEXT = {"type": "string", "minLength": 1}
POS = {"type": "integer", "minimum": 1}
HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
PURPOSE = {"enum": ["assist", "authoring", "compliance", "history"]}


def schemas():
    source = obj(
        {
            "source_id": ID,
            "source_revision": POS,
            "document_version_id": ID,
            "parse_id": ID,
            "physical_page": POS,
            "evidence_id": ID,
            "excerpt": {"type": "string", "minLength": 1, "maxLength": 20000},
        }
    )
    condition = obj(
        {
            "subject": TEXT,
            "scope": TEXT,
            "name": TEXT,
            "value": {"type": ["string", "number", "boolean"]},
            "unit": {"type": ["string", "null"]},
        }
    )
    app = obj(
        {
            "bidder_ids": array(ID, minItems=1, uniqueItems=True, maxItems=100),
            "bid_ids": array(ID, uniqueItems=True, maxItems=100),
            "regions": array(TEXT, minItems=1, uniqueItems=True, maxItems=100),
            "engineering_types": array(TEXT, minItems=1, uniqueItems=True, maxItems=100),
            "conditions": array(condition, maxItems=100),
            "purposes": array(PURPOSE, minItems=1, uniqueItems=True),
            "validity_kind": {"enum": ["bounded", "no_expiry_confirmed", "unknown"]},
            "valid_from": TIME,
            "valid_until": {"type": ["string", "null"], "format": "date-time"},
            "validity_basis": TEXT,
            "specificity_note": TEXT,
        }
    )
    version = obj(
        {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "content": {"type": "string", "minLength": 1, "maxLength": 100000},
            "categories": array(
                {
                    "enum": [
                        "company_fact",
                        "method",
                        "standard",
                        "retrospective",
                        "chapter_reference",
                    ]
                },
                minItems=1,
                uniqueItems=True,
            ),
            "scope": {"type": "string", "minLength": 1, "maxLength": 2000},
            "applicability": ref("KnowledgeApplicability"),
            "source_refs": array(ref("KnowledgeSourcePosition"), minItems=1, maxItems=100),
        }
    )
    knowledge = obj(
        {
            "id": ID,
            "version_id": ID,
            "revision": POS,
            "title": TEXT,
            "state": {"enum": ["candidate", "published", "withdrawn"]},
            "content": TEXT,
            "categories": array({"type": "string"}),
            "source_ids": array(ID),
            "source_impact": {"type": "boolean"},
            "workflow": {
                "enum": [
                    "candidate",
                    "pending_confirmation",
                    "published",
                    "withdrawn",
                    "expired",
                    "superseded",
                    "source_invalid",
                    "not_yet_effective",
                ]
            },
            "version_no": POS,
            "knowledge_source_id": ID,
            "source_revision": POS,
            "published_version_id": NULLID,
            "published_workflow": {"type": ["string", "null"]},
            "scope": TEXT,
            "applicability": {"anyOf": [ref("KnowledgeApplicability"), {"type": "null"}]},
            "source_refs": array(ref("KnowledgeSourcePosition")),
            "provenance": array(
                obj(
                    {
                        "source_ref": ref("KnowledgeSourcePosition"),
                        "bid_id": ID,
                        "original_name": TEXT,
                        "digest": HASH,
                        "byte_size": {"type": "integer", "minimum": 0},
                        "bidder_id": ID,
                        "original_project_title": TEXT,
                        "original_bidder": TEXT,
                    }
                )
            ),
            "declared_source_version_ids": array(ID),
            "created_by": ID,
            "created_at": TIME,
            "confirmed_by": NULLID,
            "confirmed_at": {"type": ["string", "null"], "format": "date-time"},
            "reference_notice": TEXT,
        }
    )
    knowledge["properties"]["published_workflow"] = {
        "enum": [None, *knowledge["properties"]["workflow"]["enum"]]
    }
    request = obj(
        {
            "bid_id": ID,
            "version_id": ID,
            "purpose": PURPOSE,
            "target_kind": {"enum": ["project", "section", "operation", "review"]},
            "target_object_id": ID,
            "excerpt": {"type": "string", "minLength": 1, "maxLength": 20000},
            "applicability_reason": {"type": "string", "minLength": 1, "maxLength": 4000},
        }
    )
    authority = obj(
        {
            "source_id": ID,
            "ancestor_id": ID,
            "kind": {"enum": ["document", "knowledge", "summary", "feature", "label", "model"]},
            "policy_class": {
                "enum": ["prebid_content", "official_label", "post_outcome", "derived_mixed"]
            },
            "known_at": TIME,
            "digest": HASH,
            "revision": POS,
            "status": {"const": "published"},
            "valid_from": TIME,
            "valid_until": {"type": ["string", "null"], "format": "date-time"},
        }
    )
    reference = obj(
        {
            "knowledge_id": ID,
            "version_id": ID,
            "bid_id": ID,
            "citation_ids": array(ID, minItems=1),
            "excerpt": TEXT,
            "purpose": PURPOSE,
            "target_kind": {"enum": ["project", "section", "operation", "review"]},
            "target_object_id": ID,
            "applicability_reason": TEXT,
            "source_manifest": array(ref("KnowledgeAuthority")),
            "source_refs": array(ref("KnowledgeSourcePosition")),
            "reference_only": {"const": True},
            "project_fact_created": {"const": False},
            "evaluated_at": TIME,
        }
    )
    impactentry = obj(
        {
            "citation_id": ID,
            "bid_id": ID,
            "target_object_id": ID,
            "purpose": {"type": "string"},
            "created_at": TIME,
            "phase": {"type": "string"},
            "archived_at": {"type": ["string", "null"], "format": "date-time"},
            "title": TEXT,
        }
    )
    return {
        "KnowledgeSourcePosition": source,
        "KnowledgeSourcePositions": obj(
            {
                "items": array(
                    obj(
                        {
                            "reference": ref("KnowledgeSourcePosition"),
                            "excerpt_truncated": {"type": "boolean"},
                        }
                    )
                ),
                "parse_id": NULLID,
                "physical_page": POS,
                "page_count": {"type": "integer", "minimum": 0},
                "next_ordinal": {"type": ["integer", "null"]},
                "has_more": {"type": "boolean"},
                "readiness": {"enum": ["not_ready", "readable_text_positions_only"]},
            }
        ),
        "KnowledgeApplicability": app,
        "KnowledgeVersionRequest": version,
        "KnowledgeView": knowledge,
        "KnowledgePage": obj(
            {
                "items": array(ref("KnowledgeView")),
                "next_cursor": NULLID,
                "has_more": {"type": "boolean"},
            }
        ),
        "KnowledgeReferenceRequest": request,
        "KnowledgeAuthority": authority,
        "KnowledgeReference": reference,
        "KnowledgeReferenceHistory": obj(
            {
                "reference": ref("KnowledgeReference"),
                "version_content": TEXT,
                "current_applicability_reasons": array(TEXT),
                "historical_bytes_preserved": {"const": True},
            }
        ),
        "KnowledgeVersionPage": obj(
            {
                "items": array(ref("KnowledgeView")),
                "next_cursor": {"type": ["integer", "null"]},
                "has_more": {"type": "boolean"},
            }
        ),
        "KnowledgeImpact": obj(
            {
                "knowledge_id": ID,
                "version_id": ID,
                "workflow": {"type": "string"},
                "groups": obj(
                    {
                        name: array(impactentry)
                        for name in ["working", "submitted", "archived", "closed"]
                    }
                ),
                "registered_research_model_derivations": array(
                    obj({"id": ID, "kind": {"type": "string"}, "digest": HASH})
                ),
                "requires_review": {"type": "boolean"},
                "unknown_ranges": array(TEXT),
                "historical_reports_rewritten": {"const": False},
            }
        ),
    }


def validate_actual(name, body):
    definitions = schemas()
    validator = Draft202012Validator(
        {"components": {"schemas": definitions}, "$ref": "#/components/schemas/" + name},
        format_checker=FormatChecker(),
    )
    if list(validator.iter_errors(body)):
        raise DomainError("INVALID_REQUEST", "知识请求字段或对象身份不符合实际接口协议", 422)


def extend_openapi(actual):
    actual["components"]["schemas"].update(deepcopy(schemas()))
    definitions = actual["components"]["schemas"]
    definitions["KnowledgePage"]["properties"]["items"] = {
        "type": "array",
        "items": ref("KnowledgeView"),
    }
    routes = [
        ("/knowledge", "post", "KnowledgeCandidate", "KnowledgeView", 201),
        ("/knowledge", "get", None, "KnowledgePage", 200),
        ("/knowledge/{knowledge_id}", "get", None, "KnowledgeView", 200),
        (
            "/knowledge/{knowledge_id}/versions",
            "post",
            "KnowledgeVersionRequest",
            "KnowledgeView",
            201,
        ),
        ("/knowledge/{knowledge_id}/versions", "get", None, "KnowledgeVersionPage", 200),
        ("/knowledge/{knowledge_id}/versions/{version_id}", "get", None, "KnowledgeView", 200),
        ("/knowledge/{knowledge_id}/submit", "post", "DecisionRequest", "KnowledgeView", 200),
        ("/knowledge/{knowledge_id}/publish", "post", "DecisionRequest", "KnowledgeView", 200),
        ("/knowledge/{knowledge_id}/withdraw", "post", "DecisionRequest", "KnowledgeView", 200),
        (
            "/knowledge/{knowledge_id}/references",
            "post",
            "KnowledgeReferenceRequest",
            "KnowledgeReference",
            201,
        ),
        ("/knowledge/{knowledge_id}/impacts", "get", None, "KnowledgeImpact", 200),
        ("/knowledge-references/{citation_id}", "get", None, "KnowledgeReferenceHistory", 200),
    ]
    routes.append(
        ("/knowledge/{knowledge_id}/source-positions", "get", None, "KnowledgeSourcePositions", 200)
    )
    for path, method, request, response, status in routes:
        operation = actual["paths"].get("/api/v1" + path, {}).get(method)
        if operation is None:
            continue
        operation["security"] = [{"SessionCookie": []}]
        if request:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(request)}},
            }
        operation["responses"] = {
            str(status): {
                "description": "实际企业知识资源",
                "content": {"application/json": {"schema": ref(response)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
    return actual
