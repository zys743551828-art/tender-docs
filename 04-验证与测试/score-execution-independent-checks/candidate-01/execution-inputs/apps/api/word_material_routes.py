"""转换采用的真实只读状态；生成产物不等于当前正文已被采用或受检。"""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from apps.api.auth import auth
from packages.domain.runtime import DomainError, connection, etag, sha
from packages.domain.word_materials import configured, material_view

router = APIRouter(prefix="/api/v1", tags=["Word material adoption"])


@router.get("/bids/{bid_id}/word-materials/{version_id}", operation_id="getWordMaterialAdoption")
def get_word_material(bid_id: UUID, version_id: UUID, request: Request):
    auth(request)
    with connection() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ,READ ONLY")
        if not configured(c):
            raise DomainError("WORD_INTAKE_NOT_CONFIGURED", "Word接收协议尚未配置", 503)
        body = material_view(c, bid_id, version_id)
        return JSONResponse(body, headers={"ETag": etag("word-material", version_id, sha(body))})


def extend_openapi(actual):
    properties = {
        "bid_id": {"type": "string", "format": "uuid"},
        "source_document_version_id": {"type": "string", "format": "uuid"},
        "file_id": {"type": "string", "format": "uuid"},
        "source_is_file_head": {"type": "boolean"},
        "selected_as_current_material": {"type": "boolean"},
        "adoption_state": {"enum": ["not_selected", "pending", "adopted", "needs_selection"]},
        "adoption_reason": {"type": ["string", "null"]},
        "adopted_pdf_document_version_id": {"type": ["string", "null"], "format": "uuid"},
        "is_current_converted_pdf": {"type": "boolean"},
        "current_body_version_id": {"type": ["string", "null"], "format": "uuid"},
        "material_revision": {"type": "integer", "minimum": 0},
    }
    actual["components"]["schemas"]["WordMaterialAdoptionView"] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }
    operation = (
        actual["paths"].get("/api/v1/bids/{bid_id}/word-materials/{version_id}", {}).get("get")
    )
    if operation:
        operation.update(
            security=[{"SessionCookie": []}],
            responses={
                "200": {
                    "description": "当前材料与历史转换采用身份分别返回",
                    "headers": {"ETag": {"schema": {"type": "string"}}},
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/WordMaterialAdoptionView"}
                        }
                    },
                },
                "default": {"$ref": "#/components/responses/DomainError"},
            },
        )
