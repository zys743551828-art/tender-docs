"""实际已注册API的引用闭包及身份；不以目标93操作冒充实现范围。"""

from fastapi.routing import APIRoute

from apps.api.main import app


def test_actual_openapi_only_registered_unique_routes_and_resolvable_references():
    actual = app.openapi()
    routes = [
        (route.path, method.lower())
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    ]
    assert len(routes) == len(set(routes))
    registered = set(routes)
    operations = []
    for path, methods in actual["paths"].items():
        for method, operation in methods.items():
            assert (path, method) in registered
            operations.append(operation["operationId"])
    assert len(operations) == len(set(operations))

    def check(value):
        if isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/")
                target = actual
                for segment in value["$ref"][2:].split("/"):
                    target = target[segment.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                check(child)

    check(actual)
    cancel = actual["paths"]["/api/v1/bids/{bid_id}/review-requests/{request_id}/cancel"]["post"]
    assert any(p.get("name") == "If-Match" and p["required"] for p in cancel["parameters"])
    assert "410" in actual["paths"]["/api/v1/streams/{stream_id}/events"]["get"]["responses"]
