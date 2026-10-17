"""Actual operation closure; schema availability never means historical input qualification."""

from fastapi import FastAPI

from apps.api.historical_routes import router
from packages.model_governance.historical_contracts import extend_openapi


def test_only_eight_actual_operations_and_no_historical_input_or_scoring_operation():
    app = FastAPI()
    app.include_router(router)
    actual = extend_openapi(app.openapi())
    operations = [
        (path, method)
        for path, item in actual["paths"].items()
        for method in item
        if method in {"get", "post"}
    ]
    assert len(operations) == 8
    assert not any(
        "/inputs" in path or "/score" in path or "/backtest" in path for path, _ in operations
    )
    for path, method in operations:
        for status, response in actual["paths"][path][method]["responses"].items():
            if status in {"200", "201"}:
                assert response["content"]["application/json"]["schema"].get("$ref")
    for name in (
        "HistorySourceClaimRequest",
        "HistoricalSubjectRequest",
        "HistoricalConfirmationRequest",
        "HistorySourceClaimView",
        "HistoricalSubjectAuthorityView",
    ):
        assert actual["components"]["schemas"][name]["additionalProperties"] is False
    for name in ("HistorySourceClaimView", "HistoricalSubjectAuthorityView"):
        assert actual["components"]["schemas"][name]["properties"][
            "historical_input_available"
        ] == {"const": False}
