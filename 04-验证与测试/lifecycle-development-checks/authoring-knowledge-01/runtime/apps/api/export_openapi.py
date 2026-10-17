"""导出实际实现路由与其协议形状，目标快照仅提供被引用定义。"""

import json
from copy import deepcopy

from fastapi.openapi.utils import get_openapi

from apps.api.comparison_contracts import extend_openapi as extend_comparisons
from apps.api.human_contracts import extend_openapi as extend_human
from apps.api.report_contracts import extend_openapi as extend_reports
from apps.api.review_contracts import extend_openapi as extend_reviews
from apps.api.source_effectivity_contracts import extend_openapi as extend_effectivity
from apps.api.submission_contracts import extend_openapi as extend_submissions
from apps.api.upload_contracts import extend_openapi as extend_uploads
from packages.assistant.contracts import extend_openapi as extend_assistant
from packages.authoring.contracts import extend_openapi as extend_authoring
from packages.domain.runtime import ROOT
from packages.identity.contracts import extend_openapi as extend_identity
from packages.knowledge.contracts import extend_openapi as extend_knowledge
from packages.knowledge.rag_contracts import extend_openapi as extend_knowledge_rag
from packages.metrics.contracts import extend_openapi as extend_metrics
from packages.pricing.contracts import extend_openapi as extend_pricing
from packages.research.contracts import extend_openapi as extend_research


def implemented_openapi(app):
    target = json.loads((ROOT / "packages/contracts/openapi/openapi.json").read_text())
    actual = get_openapi(title=app.title, version="0.1.0", routes=app.routes)
    # 以实际注册路径为分母，不能把目标计划接口标作已实现。
    generated_schemas = actual.get("components", {}).get("schemas", {})
    actual["components"] = deepcopy(target["components"])
    for name, schema in generated_schemas.items():
        actual["components"]["schemas"].setdefault(name, schema)
    schemas = actual["components"]["schemas"]
    schemas["DocumentEvidenceView"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["document_version_id", "parse_run_id", "coverage", "gaps", "items"],
        "properties": {
            "document_version_id": {"type": "string", "format": "uuid"},
            "parse_run_id": {"type": ["string", "null"], "format": "uuid"},
            "coverage": {"enum": ["pending", "complete", "partial", "failed"]},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "items": {"type": "array", "items": {"$ref": "#/components/schemas/EvidenceView"}},
        },
    }
    for path, methods in actual["paths"].items():
        short = path.removeprefix("/api/v1")
        for method, operation in methods.items():
            planned = target["paths"].get(short, {}).get(method)
            if planned:
                actual_parameters = operation.get("parameters", [])
                methods[method] = deepcopy(planned)
                # 实际查询参数是本切片支持的参数；目标尚未实现的筛选不提前承诺。
                if method == "get":
                    methods[method]["parameters"] = actual_parameters + [
                        parameter
                        for parameter in planned.get("parameters", [])
                        if parameter.get("in") == "header" or "Header" in parameter.get("$ref", "")
                    ]
            elif path in [
                "/api/v1/auth/session",
                "/api/v1/documents/{document_version_id}/evidence",
                "/api/v1/documents/{document_version_id}",
            ]:
                name = (
                    "AuthView"
                    if path.endswith("/session")
                    else ("DocumentEvidenceView" if path.endswith("/evidence") else "DocumentView")
                )
                operation["operationId"] = (
                    "getSession"
                    if name == "AuthView"
                    else (
                        "getDocumentEvidence" if name == "DocumentEvidenceView" else "getDocument"
                    )
                )
                operation["security"] = [{"SessionCookie": []}]
                operation["responses"] = {
                    "200": {
                        "description": "实际实现的只读资源",
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/" + name}}
                        },
                    },
                    "default": {"$ref": "#/components/responses/DomainError"},
                }
    events = actual["paths"].get("/api/v1/streams/{stream_id}/events", {}).get("get")
    if events:
        events["responses"]["410"] = {
            "description": "游标已超出7天保留窗口，请重新读取服务器快照",
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
        }
    transfer = actual["paths"].get("/api/v1/object-transfers/uploads/{upload_id}", {}).get("put")
    if transfer:
        transfer["parameters"] += [
            {"in": "header", "name": name, "required": True, "schema": definition}
            for name, definition in [
                ("Origin", {"type": "string", "format": "uri"}),
                ("X-CSRF-Token", {"type": "string"}),
                ("Content-Length", {"type": "integer", "minimum": 1}),
            ]
        ]
    extend_reviews(actual)
    extend_assistant(actual)
    extend_comparisons(actual)
    extend_authoring(actual)
    extend_research(actual)
    extend_human(actual)
    extend_uploads(actual)
    extend_effectivity(actual)
    extend_knowledge(actual)
    extend_knowledge_rag(actual)
    extend_identity(actual)
    extend_pricing(actual)
    extend_submissions(actual)
    extend_reports(actual)
    extend_metrics(actual)
    return actual


def main():
    from apps.api.main import app

    print(json.dumps(implemented_openapi(app), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
