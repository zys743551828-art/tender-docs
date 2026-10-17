"""Actual scope and closed request boundaries, without claiming future APIs exist."""

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from apps.api.model_governance_routes import CohortRequest, ProtocolRequest, router
from packages.model_governance.contracts import extend_openapi
from tests.test_model_governance_statistics import bundle


def test_actual_nine_registered_operations_and_no_future_publish_claim():
    app = FastAPI()
    app.include_router(router)
    actual = extend_openapi(app.openapi())
    assert (
        sum(
            method in {"get", "post", "put", "delete", "patch"}
            for path in actual["paths"].values()
            for method in path
        )
        == 9
    )
    assert not any("publish" in path or "score-job" in path for path in actual["paths"])


def test_protocol_and_cohort_do_not_accept_client_labels_or_qualification():
    document = bundle()["protocol"]
    model = ProtocolRequest.model_validate({"document": document, "reason": "synthetic software"})
    assert model.document.model_dump(mode="json", by_alias=True) == document
    for key in ("origin", "accepted", "labels", "features", "metrics"):
        with pytest.raises(ValidationError):
            ProtocolRequest.model_validate(
                {"document": document, "reason": "synthetic", key: "injected"}
            )
    base = {
        "research_run_id": "36e6ea01-c868-4ab2-acd3-d980e2ca00bc",
        "protocol_id": "12aecb70-371a-4655-a9fa-c7a5d957457e",
        "repeat_index": 1,
        "calibration_group_ids": [],
        "reason": "synthetic",
    }
    with pytest.raises(ValidationError):
        CohortRequest.model_validate({**base, "allowed_source_ids": []})
