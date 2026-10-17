"""Real nonowner ASGI registration and calculation without fictitious coefficient evidence."""

import os

import pytest

from tests.test_pricing_integration import (
    TEXT,
    formula_request,
    post_scenario,
    register,
    scenario,
    seed,
)
from tests.test_pricing_method_evidence import RATIO_FIELDS

pytest_plugins = ["tests.test_pricing_integration"]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PRICING_INTEGRATION") != "1", reason="Explicit nonowner PG only"
)


def test_ratio_registers_and_calculates_from_actual_quote_without_coefficients(client):
    _, actor, _, _ = client
    text = TEXT.replace("上下方向偏离系数为0。", "")
    item = seed(actor, text)
    value = formula_request(item, text)
    value["evidence_quotes"][0]["supported_fields"] = sorted(RATIO_FIELDS)
    formula, _ = register(client, item, value)
    assert "偏离系数" not in formula.json()["formula"]["evidence_quotes"][0]["quote"]
    calculated = post_scenario(
        client, item, formula, scenario(formula.json()["formula_version_id"])
    )
    assert calculated.status_code == 201
    assert calculated.json()["price_score"] == "25.00"
