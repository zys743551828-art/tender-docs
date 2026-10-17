"""实际提交登记读写契约；冻结目标快照不改。"""

from copy import deepcopy

from packages.identity.contracts import SNAPSHOT

UUID = {"type": "string", "format": "uuid"}
NULL_UUID = {"type": ["string", "null"], "format": "uuid"}
TEXT = {"type": "string"}


def obj(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def page(name):
    return obj(
        {
            "items": {"type": "array", "items": {"$ref": "#/components/schemas/" + name}},
            "has_more": {"type": "boolean"},
            "next_cursor": {"type": ["string", "null"]},
        }
    )


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    ids = {"type": "array", "items": UUID}
    null_count = {"type": ["integer", "null"], "minimum": 0}
    schemas["SubmissionRiskSnapshot"] = obj(
        {
            "submission_id": UUID,
            "review_id": NULL_UUID,
            "manifest_digest": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
            "body_version_id": UUID,
            "scope_issue_ids": ids,
            "roots": {
                "type": "array",
                "items": obj(
                    {
                        "root_id": UUID,
                        "finding_id": UUID,
                        "state": TEXT,
                        "decision_id": NULL_UUID,
                        "evidence_ids": ids,
                    }
                ),
            },
            "identified": null_count,
            "open": null_count,
            "resolved": null_count,
            "dismissed": null_count,
            "assessment_as_of": {"type": "string", "format": "date-time"},
            "frozen_at": {"type": "string", "format": "date-time"},
            "basis": {"enum": ["reconstructed_from_audit", "unknown"]},
            "scope_definition": {"const": "disqualification_and_mandatory_root_findings"},
            "not_all_submission_blockers": {"const": True},
            "observation_authority_id": NULL_UUID,
            "scope_note": TEXT,
        }
    )
    gate = schemas["GateView"]
    additions = {
        "review_id": NULL_UUID,
        "material_revision": {"type": "integer", "minimum": 1},
        "current_identity_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "current_review_manifest_digest": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        "pending_core_rule_ids": {"type": "array", "items": UUID},
        "blocking_finding_ids": {"type": "array", "items": UUID},
        "warnings": {"type": "array", "items": TEXT},
    }
    gate["properties"]["body_version_id"] = deepcopy(NULL_UUID)
    gate["properties"].update(deepcopy(additions))
    gate["required"] += list(additions)
    submission = schemas["SubmissionView"]
    additions = {
        "current": {"type": "boolean"},
        "supersedes_id": NULL_UUID,
        "identity": {"anyOf": [deepcopy(SNAPSHOT), {"type": "null"}]},
        "original_name": TEXT,
        "artifact_kind": TEXT,
        "checks_label": TEXT,
        "open_risk_snapshot": {
            "anyOf": [{"$ref": "#/components/schemas/SubmissionRiskSnapshot"}, {"type": "null"}]
        },
        "platform_submission_verified": {"const": False},
        "explanation": {"type": ["string", "null"]},
        "correction_reason": {"type": ["string", "null"]},
    }
    submission["properties"]["evidence_ref"] = deepcopy(NULL_UUID)
    submission["properties"].update(deepcopy(additions))
    submission["required"] += list(additions)
    schemas["SubmissionPage"] = page("SubmissionView")
    schemas["SubmissionDocumentView"] = obj(
        {
            "document_version_id": UUID,
            "original_name": TEXT,
            "artifact_kind": TEXT,
            "digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "byte_size": {"type": "integer", "minimum": 1},
            "media_type": TEXT,
        }
    )
    schemas["SubmissionDocumentPage"] = page("SubmissionDocumentView")
    schemas["SubmissionCorrectionRequest"] = {
        "oneOf": [
            obj(
                {
                    "kind": {"const": kind},
                    "submission": {"$ref": "#/components/schemas/" + request},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                }
            )
            for kind, request in (
                ("standard", "SubmissionRequest"),
                ("offline_fact", "OfflineSubmissionRequest"),
            )
        ]
    }
    specifications = [
        ("/bids/{bid_id}/submission-gate", "get", "getSubmissionGate", None, "GateView"),
        (
            "/bids/{bid_id}/submissions",
            "post",
            "registerSubmission",
            "SubmissionRequest",
            "SubmissionView",
        ),
        (
            "/bids/{bid_id}/submission-facts",
            "post",
            "registerSubmissionFact",
            "OfflineSubmissionRequest",
            "SubmissionView",
        ),
        (
            "/submissions/{submission_id}/corrections",
            "post",
            "correctSubmission",
            "SubmissionCorrectionRequest",
            "SubmissionView",
        ),
        ("/submissions/{submission_id}", "get", "getSubmission", None, "SubmissionView"),
        ("/bids/{bid_id}/submissions", "get", "listSubmissions", None, "SubmissionPage"),
        (
            "/bids/{bid_id}/submission-documents",
            "get",
            "listSubmissionDocuments",
            None,
            "SubmissionDocumentPage",
        ),
    ]
    for path, method, name, request, response in specifications:
        operation = actual["paths"].get("/api/v1" + path, {}).get(method)
        if not operation:
            continue
        operation.update(operationId=name, security=[{"SessionCookie": []}])
        if request:
            # planned操作已经带头；新增操作才补，避免参数重复。
            existing = {p.get("$ref") for p in operation.get("parameters", [])}
            operation.setdefault("parameters", []).extend(
                {"$ref": "#/components/parameters/" + header}
                for header in ("CSRF", "IdempotencyKey", "IfMatch")
                if "#/components/parameters/" + header not in existing
            )
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + request}}
                },
            }
        operation["responses"] = {
            "201" if request else "200": {
                "description": "真实不可变实交事实；登记不代表交易平台提交成功",
                "headers": {"ETag": {"schema": TEXT}},
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/" + response}}
                },
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
