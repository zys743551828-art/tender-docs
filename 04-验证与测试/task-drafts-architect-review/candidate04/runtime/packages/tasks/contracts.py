"""Use the implemented task routers' exact Pydantic contracts, after target mappings."""

from copy import deepcopy

from fastapi.openapi.utils import get_openapi


def extend_openapi(actual):
    from apps.api.task_draft_routes import router as drafts
    from apps.api.task_routes import router as tasks

    generated = get_openapi(
        title="Implemented tasks", version="0.1.0", routes=[*tasks.routes, *drafts.routes]
    )
    actual.setdefault("components", {}).setdefault("schemas", {}).update(
        deepcopy(generated["components"]["schemas"])
    )
    for path, methods in generated["paths"].items():
        for method, operation in methods.items():
            if method not in actual.get("paths", {}).get(path, {}):
                continue
            item = deepcopy(operation)
            item["security"] = [{"SessionCookie": []}]
            item["responses"]["default"] = {"$ref": "#/components/responses/DomainError"}
            if method in {"post", "put", "delete", "patch"}:
                item.setdefault("parameters", []).extend(
                    [
                        {
                            "in": "header",
                            "name": name,
                            "required": True,
                            "schema": {"type": "string"},
                        }
                        for name in ("Origin", "X-CSRF-Token", "Idempotency-Key", "If-Match")
                    ]
                )
            actual["paths"][path][method] = item
