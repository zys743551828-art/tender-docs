"""Closed actual schemas for A2 tree/plan/two template text subset."""

from jsonschema import Draft202012Validator, FormatChecker

from packages.domain.runtime import DomainError, sha

ID = {"type": "string", "format": "uuid"}
NID = {"type": ["string", "null"], "format": "uuid"}
REV = {"type": "integer", "minimum": 1}
TITLE = {"type": "string", "minLength": 1, "maxLength": 200, "pattern": r"\S"}
TEXT = {"type": "string", "maxLength": 8000}
KIND = {"enum": ["chapter", "appendix"]}
KEY = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[a-zA-Z0-9_-]+$"}


def obj(properties, required=None):
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
    }


def array(items, maximum=500):
    return {"type": "array", "items": items, "maxItems": maximum}


PARENT = {"oneOf": [obj({"section_id": ID}), obj({"client_key": KEY}), {"type": "null"}]}
ORD = {"type": "integer", "minimum": 0, "maximum": 499}
REF = obj({"source_id": ID, "source_revision": REV, "evidence_id": ID, "document_version_id": ID})
CHANGES = [
    obj(
        {
            "op": {"const": "add"},
            "client_key": KEY,
            "title": TITLE,
            "node_type": KIND,
            "parent": PARENT,
            "ordinal": ORD,
        }
    ),
    obj({"op": {"const": "move"}, "section_id": ID, "parent": PARENT, "ordinal": ORD}),
    obj({"op": {"const": "rename"}, "section_id": ID, "title": TITLE}),
    obj({"op": {"const": "set_type"}, "section_id": ID, "node_type": KIND}),
    obj({"op": {"const": "delete"}, "section_id": ID}),
    obj(
        {
            "op": {"const": "restore"},
            "section_id": ID,
            "parent": PARENT,
            "ordinal": ORD,
            "include_descendants": {"type": "boolean"},
        },
        ["op", "section_id", "parent", "ordinal"],
    ),
    obj(
        {
            "op": {"const": "copy"},
            "section_id": ID,
            "parent": PARENT,
            "ordinal": ORD,
            "client_key": KEY,
        },
        ["op", "section_id", "parent", "ordinal"],
    ),
]
STYLE_FIELDS = {
    "font": {"enum": ["Noto Sans CJK SC", "Noto Serif CJK SC"]},
    "font_size_halfpoints": {"type": "integer", "minimum": 16, "maximum": 48},
    "margin_top_twips": {"type": "integer", "minimum": 360, "maximum": 4320},
    "margin_right_twips": {"type": "integer", "minimum": 360, "maximum": 4320},
    "margin_bottom_twips": {"type": "integer", "minimum": 360, "maximum": 4320},
    "margin_left_twips": {"type": "integer", "minimum": 360, "maximum": 4320},
    "paragraph_after_twips": {"type": "integer", "minimum": 0, "maximum": 720},
    "cover_enabled": {"type": "boolean"},
    "cover_title": {"type": "string", "maxLength": 200},
    "header_text": {"type": "string", "maxLength": 200},
    "footer_text": {"type": "string", "maxLength": 200},
    "page_number": {"type": "boolean"},
    "chapter_numbering": {"enum": ["decimal", "none"]},
    "toc_enabled": {"type": "boolean"},
}
STYLE = obj({"schema_version": {"const": 1}, "page": {"const": "A4"}} | STYLE_FIELDS)
OUTLINE_NODE = obj(
    {
        "key": KEY,
        "parent_key": {"oneOf": [KEY, {"type": "null"}]},
        "ordinal": ORD,
        "title": TITLE,
        "node_type": KIND,
        "goal": TEXT,
        "planned_pages": {"type": ["integer", "null"], "minimum": 1, "maximum": 10000},
    },
    ["key", "parent_key", "ordinal", "title", "node_type"],
)
OUTLINE = obj({"schema_version": {"const": 1}, "nodes": array(OUTLINE_NODE)})
FORMAT_BINDING = {
    "oneOf": [
        obj(
            {
                "rule_id": ID,
                "field": {"const": field},
                "value": spec,
                "source_refs": array(REF),
                "reason": TITLE,
            }
        )
        for field, spec in STYLE_FIELDS.items()
    ]
}
SCHEMAS = {
    "AuthoringTreeChangeRequest": obj(
        {"base_draft_revision": REV, "changes": array({"oneOf": CHANGES}, 100)}
    ),
    "AuthoringStructureResolve": obj({"decision": {"enum": ["confirm", "reject"]}}),
    "AuthoringPlanSave": obj(
        {
            "base_draft_revision": REV,
            "base_plan_revision": {"type": "integer", "minimum": 0},
            "state": {"enum": ["not_started", "drafting", "needs_basis", "ready"]},
            "goal": TEXT,
            "key_points": array({"type": "string", "minLength": 1, "maxLength": 2000}, 100),
            "rule_ids": array(ID),
            "source_refs": array(REF),
            "pending_facts": array({"type": "string", "minLength": 1, "maxLength": 2000}, 100),
            "planned_pages": {"type": ["integer", "null"], "minimum": 1, "maximum": 10000},
            "budget_basis": {"enum": ["manual", "requirement", "template"]},
            "budget_rule_id": NID,
            "budget_note": {"type": "string", "maxLength": 2000},
        }
    ),
    "AuthoringTemplateCreate": obj(
        {
            "name": TITLE,
            "kind": {"enum": ["outline", "layout"]},
            "config": {"oneOf": [OUTLINE, STYLE]},
        }
    ),
    "AuthoringTemplateVersion": obj(
        {"base_revision": REV, "name": TITLE, "config": {"oneOf": [OUTLINE, STYLE]}}
    ),
    "AuthoringDirectoryApplyPreview": obj(
        {
            "base_draft_revision": REV,
            "directory_template_version_id": ID,
            "matches": array(obj({"template_key": KEY, "section_id": ID})),
            "delete_section_ids": array(ID),
        }
    ),
    "AuthoringStyleApplyPreview": obj(
        {
            "base_draft_revision": REV,
            "style_template_version_id": ID,
            "format_bindings": array(FORMAT_BINDING, 50),
            "format_review_ack": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        }
    ),
    "AuthoringStructureRestorePreview": obj(
        {"base_draft_revision": REV, "structure_version_id": ID}
    ),
    "AuthoringStructuredBuildRequest": obj(
        {
            "draft_id": ID,
            "draft_revision": REV,
            "directory_template_version_id": ID,
            "style_template_version_id": ID,
            "formats": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
                "items": {"enum": ["docx", "pdf"]},
            },
            "follow_up": {"enum": ["none", "review"]},
        }
    ),
}
SCHEMA_DIGEST = sha({"schemas": SCHEMAS, "outline": OUTLINE, "style": STYLE, "version": 1})


def validate(name, value):
    schema = SCHEMAS.get(name, {"outline": OUTLINE, "layout": STYLE}.get(name))
    if schema is None or not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(
        value
    ):
        raise DomainError(
            "AUTHORING_STRUCTURE_REQUEST_INVALID", "字段不符合当前章节/计划/模板协议", 422
        )

    def strings(item):
        if isinstance(item, str):
            try:
                item.encode("utf-8", errors="strict")
            except UnicodeError:
                raise DomainError(
                    "AUTHORING_STRUCTURE_REQUEST_INVALID", "文本不能保存为UTF-8", 422
                ) from None
            if any(ord(c) < 32 and c not in "\n\t" for c in item):
                raise DomainError(
                    "AUTHORING_STRUCTURE_REQUEST_INVALID", "文本含不可保存控制字符", 422
                )
        elif isinstance(item, dict):
            for child in item.values():
                strings(child)
        elif isinstance(item, list):
            for child in item:
                strings(child)

    strings(value)
    return value
