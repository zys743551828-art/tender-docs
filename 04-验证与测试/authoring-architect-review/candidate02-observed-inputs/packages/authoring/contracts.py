"""Actual closed editor schema and additional implementation routes for OpenAPI exporter."""

from copy import deepcopy


def obj(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


def array(item, **kwargs):
    return {"type": "array", "items": item, **kwargs}


def ref(name):
    return {"$ref": "#/components/schemas/" + name}


UUID = {"type": "string", "format": "uuid"}
INTEGER = {"type": "integer", "minimum": 1}
STRING = {"type": "string"}
DIGEST = {"type": "string", "pattern": "^[0-9a-f]{64}$"}


def extend_openapi(actual):
    schemas = actual["components"]["schemas"]
    source_ref = obj(
        {
            "source_id": UUID,
            "source_revision": INTEGER,
            "evidence_id": UUID,
            "document_version_id": UUID,
        }
    )
    schemas["AuthoringSourceReference"] = source_ref
    names = {
        "hardBreak": "AuthoringBreak",
        "text": "AuthoringText",
        "paragraph": "AuthoringParagraph",
        "heading": "AuthoringHeading",
        "bulletList": "AuthoringBulletList",
        "orderedList": "AuthoringOrderedList",
        "listItem": "AuthoringListItem",
    }
    schemas["AuthoringBreak"] = obj({"type": {"const": "hardBreak"}})
    schemas["AuthoringText"] = obj(
        {
            "type": {"const": "text"},
            "text": STRING,
            "marks": array(obj({"type": {"enum": ["bold", "italic"]}})),
        },
        ["type", "text"],
    )
    children = {
        "paragraph": ["text", "hardBreak"],
        "heading": ["text", "hardBreak"],
        "bulletList": ["listItem"],
        "orderedList": ["listItem"],
        "listItem": ["paragraph", "bulletList", "orderedList"],
    }
    for kind, allowed in children.items():
        attributes = {"id": {"type": "string", "minLength": 1, "maxLength": 128}}
        if kind == "heading":
            attributes["level"] = {"type": "integer", "minimum": 1, "maximum": 6}
        schemas[names[kind]] = obj(
            {
                "type": {"const": kind},
                "attrs": obj(attributes),
                "content": array({"oneOf": [ref(names[c]) for c in allowed]}),
            }
        )
    schemas["AuthoringNode"] = {"oneOf": [ref(name) for name in names.values()]}
    schemas["AuthoringBlock"] = {
        "oneOf": [
            ref(names[kind]) for kind in ["paragraph", "heading", "bulletList", "orderedList"]
        ]
    }
    schemas["AuthoringDocument"] = obj(
        {
            "type": {"const": "doc"},
            "attrs": obj(
                {
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "ordinal": {"type": "integer", "minimum": 0, "maximum": 499},
                    "rule_ids": array(UUID, maxItems=500),
                    "source_refs": array(ref("AuthoringSourceReference"), maxItems=500),
                }
            ),
            "content": array(ref("AuthoringBlock")),
        }
    )
    schemas["SectionChange"]["properties"]["content"] = ref("AuthoringDocument")
    schemas["SectionView"]["properties"]["content"] = ref("AuthoringDocument")
    schemas["AddAuthoringSection"] = obj(
        {"title": {"type": "string", "minLength": 1, "maxLength": 200}}
    )
    schemas["AuthoringSectionAdded"] = deepcopy(schemas["DraftSaved"])
    schemas["AuthoringSectionAdded"]["properties"]["section_id"] = UUID
    schemas["AuthoringSectionAdded"]["required"].append("section_id")
    schemas["AuthoringSnapshot"] = obj(
        {
            "snapshot_id": UUID,
            "snapshot_digest": DIGEST,
            "title": STRING,
            "sections": array(ref("AuthoringDocument")),
        }
    )
    schemas["AuthoringPreviewRequest"] = obj(
        {
            "base_section_revision": INTEGER,
            "node_id": STRING,
            "before_digest": DIGEST,
            "replacement": ref("AuthoringNode"),
            "source_refs": array(ref("AuthoringSourceReference")),
        }
    )
    schemas["AuthoringPreview"] = obj(
        {
            "action_id": UUID,
            "base_draft_revision": INTEGER,
            "before": ref("AuthoringDocument"),
            "after": ref("AuthoringDocument"),
            "expires_in_seconds": INTEGER,
        }
    )
    schemas["RestoreAuthoringSection"] = obj(
        {"revision": INTEGER, "base_draft_revision": INTEGER, "base_section_revision": INTEGER}
    )
    schemas["AuthoringDraftPage"] = obj(
        {
            "items": array(
                obj({"id": UUID, "revision": INTEGER, "title": STRING, "snapshot_id": UUID})
            ),
            "next_cursor": {"type": ["string", "null"], "format": "uuid"},
        }
    )
    schemas["AuthoringRevisionPage"] = obj(
        {
            "items": array(
                obj(
                    {
                        "revision": INTEGER,
                        "created_at": {"type": "string", "format": "date-time"},
                        "digest": DIGEST,
                        "created_by": UUID,
                    }
                )
            ),
            "next_cursor": {"type": ["integer", "null"]},
        }
    )
    schemas["AuthoringCapabilities"] = obj(
        {
            "schema_digest": DIGEST,
            "schema": obj(
                {
                    "version": INTEGER,
                    "nodes": array(STRING),
                    "marks": array(STRING),
                    "metadata": array(STRING),
                }
            ),
            "default_template_version_id": UUID,
            "formats": array({"enum": ["docx", "pdf"]}),
            "supports_external_docx_import": {"type": "boolean"},
            "supported_nodes": array(STRING),
            "unsupported": array(STRING),
        }
    )
    routes = [
        (
            "/authoring/capabilities",
            "get",
            "getAuthoringCapabilities",
            None,
            "AuthoringCapabilities",
            200,
        ),
        ("/bids/{bid_id}/drafts", "get", "listDrafts", None, "AuthoringDraftPage", 200),
        (
            "/bids/{bid_id}/drafts/{draft_id}/sections",
            "post",
            "addAuthoringSection",
            "AddAuthoringSection",
            "AuthoringSectionAdded",
            201,
        ),
        (
            "/bids/{bid_id}/draft-snapshots/{snapshot_id}",
            "get",
            "getAuthoringSnapshot",
            None,
            "AuthoringSnapshot",
            200,
        ),
        (
            "/bids/{bid_id}/sections/{section_id}/patch-previews",
            "post",
            "previewAuthoringPatch",
            "AuthoringPreviewRequest",
            "AuthoringPreview",
            201,
        ),
        (
            "/bids/{bid_id}/authoring-actions/{action_id}/apply",
            "post",
            "applyAuthoringPatch",
            "EmptyObject",
            "DraftSaved",
            200,
        ),
        (
            "/sections/{section_id}/revisions",
            "get",
            "listAuthoringSectionRevisions",
            None,
            "AuthoringRevisionPage",
            200,
        ),
        (
            "/bids/{bid_id}/sections/{section_id}/restore",
            "post",
            "restoreAuthoringSection",
            "RestoreAuthoringSection",
            "DraftSaved",
            200,
        ),
    ]
    schemas["EmptyObject"] = obj({})
    for path, method, operation_id, request, response, status in routes:
        operation = actual["paths"].get("/api/v1" + path, {}).get(method)
        if operation is None:
            continue
        operation["operationId"] = operation_id
        operation["security"] = [{"SessionCookie": []}]
        if request:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": ref(request)}},
            }
        operation["responses"] = {
            str(status): {
                "description": "Actual implemented authoring resource",
                "content": {"application/json": {"schema": ref(response)}},
            },
            "default": {"$ref": "#/components/responses/DomainError"},
        }
    return actual
