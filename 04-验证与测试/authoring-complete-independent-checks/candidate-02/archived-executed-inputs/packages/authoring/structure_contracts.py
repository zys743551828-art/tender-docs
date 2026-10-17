"""Actual-only schemas for complete-A2 slice; no mutation of target contracts."""

from copy import deepcopy

from packages.authoring.structure_schema import ID, NID, REF, REV, SCHEMAS, obj

DIGEST = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
TEXT = {"type": "string"}
JSON = {"$ref": "#/components/schemas/AuthoringJsonValue"}


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


def arr(item):
    return {"type": "array", "items": item}


def extend_openapi(actual):
    schemas = actual.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(deepcopy(SCHEMAS))
    schemas["AuthoringJsonValue"] = {
        "oneOf": [
            {"type": "null"},
            {"type": "boolean"},
            {"type": "number"},
            TEXT,
            arr(JSON),
            {"type": "object", "additionalProperties": JSON},
        ]
    }
    node = obj(
        {
            "section_id": ID,
            "parent_id": NID,
            "ordinal": {"type": "integer", "minimum": 0},
            "node_type": {"enum": ["chapter", "appendix"]},
            "deleted": {"type": "boolean"},
            "section_revision": REV,
            "plan_version_id": NID,
            "template_family_id": NID,
            "template_key": {"type": ["string", "null"]},
        }
    )
    schemas["AuthoringTreeNode"] = node
    schemas["AuthoringPlanVersion"] = obj(
        {
            "id": ID,
            "bid_id": ID,
            "draft_id": ID,
            "section_id": ID,
            "revision": REV,
            "base_section_revision": REV,
            "state": {"enum": ["not_started", "drafting", "needs_basis", "ready"]},
            "planned_pages": {"type": ["integer", "null"], "minimum": 1},
            "payload": {"type": "object", "additionalProperties": JSON},
            "digest": DIGEST,
            "created_by": ID,
            "created_at": {"type": "string", "format": "date-time"},
        }
    )
    projected = deepcopy(node)
    projected["properties"].update(
        title=TEXT,
        rule_ids=arr(ID),
        source_refs=arr(REF),
        plan={"oneOf": [ref("AuthoringPlanVersion"), {"type": "null"}]},
        plan_content_basis_state={"enum": ["none", "current", "section_changed"]},
        actual_page_range={"type": "null"},
    )
    projected["required"] += [
        "title",
        "rule_ids",
        "source_refs",
        "plan",
        "plan_content_basis_state",
        "actual_page_range",
    ]
    schemas["AuthoringTreeView"] = obj(
        {
            "draft_id": ID,
            "draft_revision": REV,
            "snapshot_id": ID,
            "structure_version_id": NID,
            "nodes": arr(projected),
            "directory_template_version_id": ID,
            "style_template_version_id": ID,
            "metadata": {"type": "object", "additionalProperties": JSON},
            "page_budget": obj(
                {
                    "planned_total": {"type": "integer", "minimum": 0},
                    "unknown_section_ids": arr(ID),
                    "actual_total": {"type": "null"},
                    "actual_ranges_state": {"const": "unknown"},
                }
            ),
        }
    )
    from packages.authoring.structure_schema import OUTLINE, STYLE

    schemas["AuthoringTemplateVersionView"] = obj(
        {
            "id": ID,
            "family_id": ID,
            "revision": REV,
            "name": TEXT,
            "kind": {"enum": ["outline", "layout"]},
            "config": {"oneOf": [OUTLINE, STYLE]},
            "config_digest": DIGEST,
            "schema_digest": DIGEST,
            "previous_version_id": NID,
            "created_by": ID,
            "created_at": {"type": "string", "format": "date-time"},
            "immutable": {"const": True},
            "is_builtin": {"type": "boolean"},
        }
    )
    schemas["AuthoringTemplateCatalog"] = obj(
        {
            "items": arr(ref("AuthoringTemplateVersionView")),
            "next_cursor": NID,
            "coverage": {"const": "partial"},
            "notice": TEXT,
        }
    )
    schemas["AuthoringStructureExtension"] = obj(
        {
            "id": ID,
            "schema_digest": DIGEST,
            "nodes": arr(ref("AuthoringTreeNode")),
            "plans": {"type": "object", "additionalProperties": ref("AuthoringPlanVersion")},
            "directory_template": ref("AuthoringTemplateVersionView"),
            "style_template": ref("AuthoringTemplateVersionView"),
            "metadata": {"type": "object", "additionalProperties": JSON},
            "digest": DIGEST,
        }
    )
    if "AuthoringSnapshot" in schemas:
        schemas["AuthoringSnapshot"]["properties"]["authoring_structure"] = ref(
            "AuthoringStructureExtension"
        )
    preview = obj(
        {
            "action_id": ID,
            "base_draft_revision": REV,
            "created_ids": {"type": "object", "additionalProperties": ID},
            "plan_suggestions": {
                "type": "object",
                "additionalProperties": obj(
                    {
                        "template_version_id": ID,
                        "template_key": TEXT,
                        "goal": TEXT,
                        "planned_pages": {"type": ["integer", "null"], "minimum": 1},
                    }
                ),
            },
            "before": arr(ref("AuthoringTreeNode")),
            "after": arr(ref("AuthoringTreeNode")),
            "before_sections": {"type": "object", "additionalProperties": JSON},
            "after_sections": {"type": "object", "additionalProperties": JSON},
            "directory_template_version_id": ID,
            "style_template_version_id": ID,
            "deletion_impact": arr(
                obj(
                    {
                        "section_id": ID,
                        "title": TEXT,
                        "text_characters": {"type": "integer", "minimum": 0},
                        "rule_ids": arr(ID),
                        "source_count": {"type": "integer", "minimum": 0},
                        "plan_version_id": NID,
                    }
                )
            ),
            "format_report": JSON,
            "expires_in_seconds": REV,
        }
    )
    schemas["AuthoringStructurePreview"] = preview
    schemas["AuthoringStylePreview"] = {
        "oneOf": [
            preview,
            obj(
                {
                    "action_id": {"type": "null"},
                    "state": {"const": "requires_format_review"},
                    "format_report": JSON,
                }
            ),
        ]
    }
    saved = {"draft_id": ID, "draft_revision": REV, "snapshot_id": ID, "snapshot_digest": DIGEST}
    schemas["AuthoringStructureResult"] = {
        "oneOf": [
            obj(saved | {"action_id": ID, "state": {"const": "applied"}}),
            obj({"action_id": ID, "state": {"const": "rejected"}}),
        ]
    }
    schemas["AuthoringPlanSaved"] = obj(saved | {"plan": ref("AuthoringPlanVersion")})
    schemas["AuthoringStructureHistory"] = obj(
        {
            "items": arr(
                obj(
                    {
                        "id": ID,
                        "draft_revision": REV,
                        "snapshot_id": ID,
                        "digest": DIGEST,
                        "created_by": ID,
                        "created_at": {"type": "string", "format": "date-time"},
                    }
                )
            ),
            "next_cursor": {"type": ["integer", "null"], "minimum": 1},
        }
    )
    routes = [
        (
            "/bids/{bid_id}/drafts/{draft_id}/structure/history",
            "get",
            None,
            "AuthoringStructureHistory",
            200,
        ),
        ("/bids/{bid_id}/drafts/{draft_id}/structure", "get", None, "AuthoringTreeView", 200),
        (
            "/bids/{bid_id}/drafts/{draft_id}/structure/preview",
            "post",
            "AuthoringTreeChangeRequest",
            "AuthoringStructurePreview",
            201,
        ),
        (
            "/bids/{bid_id}/authoring-structure-actions/{action_id}/resolve",
            "post",
            "AuthoringStructureResolve",
            "AuthoringStructureResult",
            200,
        ),
        (
            "/bids/{bid_id}/drafts/{draft_id}/sections/{section_id}/plan",
            "put",
            "AuthoringPlanSave",
            "AuthoringPlanSaved",
            200,
        ),
        (
            "/bids/{bid_id}/drafts/{draft_id}/directory-template/preview",
            "post",
            "AuthoringDirectoryApplyPreview",
            "AuthoringStructurePreview",
            201,
        ),
        (
            "/bids/{bid_id}/drafts/{draft_id}/style-template/preview",
            "post",
            "AuthoringStyleApplyPreview",
            "AuthoringStylePreview",
            201,
        ),
        (
            "/bids/{bid_id}/drafts/{draft_id}/structure/restore-preview",
            "post",
            "AuthoringStructureRestorePreview",
            "AuthoringStructurePreview",
            201,
        ),
        ("/authoring/templates", "get", None, "AuthoringTemplateCatalog", 200),
        (
            "/authoring/template-versions/{version_id}",
            "get",
            None,
            "AuthoringTemplateVersionView",
            200,
        ),
        (
            "/authoring/template-families",
            "post",
            "AuthoringTemplateCreate",
            "AuthoringTemplateVersionView",
            201,
        ),
        (
            "/authoring/template-families/{family_id}/versions",
            "post",
            "AuthoringTemplateVersion",
            "AuthoringTemplateVersionView",
            201,
        ),
    ]
    for path, method, request, response, status in routes:
        operation = actual.get("paths", {}).get("/api/v1" + path, {}).get(method)
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
                "description": "Actual strict text A2 resource",
                "content": {"application/json": {"schema": ref(response)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
        if response == "AuthoringStylePreview":
            operation["responses"]["200"] = deepcopy(operation["responses"][str(status)])
    if "BuildRequest" in schemas:
        old = deepcopy(schemas["BuildRequest"])
        schemas["BuildRequest"] = {"oneOf": [old, ref("AuthoringStructuredBuildRequest")]}
    return actual
