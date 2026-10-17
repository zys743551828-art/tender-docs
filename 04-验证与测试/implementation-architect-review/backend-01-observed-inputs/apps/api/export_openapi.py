"""导出实际实现路由与其协议形状，目标快照仅提供被引用定义。"""

import json
from copy import deepcopy

from fastapi.openapi.utils import get_openapi

from packages.domain.runtime import ROOT


def implemented_openapi(app):
    target = json.loads((ROOT / "packages/contracts/openapi/openapi.json").read_text())
    actual = get_openapi(title=app.title, version="0.1.0", routes=app.routes)
    # 以实际注册路径为分母，不能把目标计划接口标作已实现。
    actual["components"] = deepcopy(target["components"])
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
                    methods[method]["parameters"] = actual_parameters
            elif path in [
                "/api/v1/auth/session",
                "/api/v1/documents/{document_version_id}/evidence",
            ]:
                name = "AuthView" if path.endswith("/session") else "DocumentEvidenceView"
                operation["operationId"] = (
                    "getSession" if name == "AuthView" else "getDocumentEvidence"
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
    return actual


def main():
    from apps.api.main import app

    print(json.dumps(implemented_openapi(app), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
