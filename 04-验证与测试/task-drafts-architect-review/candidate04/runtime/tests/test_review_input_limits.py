"""超过真实输出协议范围先失败，不向模型发送不可能完整返回的要求集。"""

import pytest

from apps.worker import review_handlers
from packages.domain.runtime import DomainError


def test_core_rule_limit_fails_before_any_provider_request(monkeypatch):
    body_id = "synthetic-body"
    row = {"manifest": {"body_version_id": body_id}}
    evidence = {
        "rules": [{"kind": "mandatory", "provenance_status": "effective"} for _ in range(201)]
    }
    documents = [{"document_version_id": body_id, "blocks": [{"id": "synthetic-evidence"}]}]
    monkeypatch.setattr(review_handlers, "load_frozen", lambda _: (row, evidence, documents))

    def forbidden(*args, **kwargs):
        raise AssertionError("超出范围不能先收费后发现输出协议不可能完整")

    monkeypatch.setattr(review_handlers.model_calls, "generate", forbidden)
    with pytest.raises(DomainError) as error:
        review_handlers.compute_core({}, None)
    assert error.value.code == "CORE_RULE_RANGE_LIMIT" and error.value.status == 413
