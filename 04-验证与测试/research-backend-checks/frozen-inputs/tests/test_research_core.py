"""合成研究工程反例；不表示真实预测精度、用户试用或模型业务资格。"""

import copy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from packages.domain.runtime import DomainError, sha
from packages.research.core import (
    FullSection,
    chapter_package,
    connected_groups,
    judge_three,
    validate_score,
    validate_split,
)
from packages.research.profile import descriptor, digest


def test_unknown_zero_and_comprehensive_distinct():
    validate_score("unknown", None, None)
    validate_score("technical_total", "0", "60")
    validate_score("comprehensive_total", "91", "100")
    for values in [
        ("unknown", "0", "100"),
        ("technical_total", "1", None),
        ("technical_total", "NaN", "100"),
        ("technical_total", "101", "100"),
        ("technical_total", "0", "0"),
    ]:
        with pytest.raises(DomainError):
            validate_score(*values)


def test_connected_closure_includes_template_and_intermediate_unselected_cases():
    rows = [
        {"id": "1", "project_id": "p1", "body_digest": "d1"},
        {"id": "2", "project_id": "p1", "template_group": "t1"},
        {"id": "3", "project_id": "p2", "template_group": "t1"},
        {"id": "4", "project_id": "p3", "body_digest": "d1"},
        {"id": "5", "project_id": "p4"},
    ]
    groups = connected_groups(rows)
    assert len({groups[key] for key in ("1", "2", "3", "4")}) == 1
    assert groups["1"] != groups["5"]
    assert connected_groups(list(reversed(rows))) == groups


def test_group_time_and_unknown_label_sources_blocked():
    cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [
        {
            "id": "1",
            "project_id": "p1",
            "known_at": cutoff - timedelta(days=2),
            "submitted_at": cutoff - timedelta(days=3),
        },
        {
            "id": "2",
            "project_id": "p2",
            "known_at": cutoff + timedelta(days=2),
            "submitted_at": cutoff + timedelta(days=1),
        },
    ]
    validate_split(rows, {"1": "train", "2": "test"}, cutoff)
    altered = copy.deepcopy(rows)
    altered[0]["known_at"] = cutoff
    with pytest.raises(DomainError, match="训练来源"):
        validate_split(altered, {"1": "train", "2": "test"}, cutoff)
    altered = copy.deepcopy(rows)
    altered[1]["project_id"] = "p1"
    with pytest.raises(DomainError, match="同项目"):
        validate_split(altered, {"1": "train", "2": "test"}, cutoff)
    altered = copy.deepcopy(rows)
    altered[0]["known_at"] = None
    with pytest.raises(DomainError, match="时间"):
        validate_split(altered, {"1": "train", "2": "test"}, cutoff)


def package():
    evidence, body, parse, rule = map(str, [uuid4(), uuid4(), uuid4(), uuid4()])
    text = "施工围挡与交通疏导按分期方案执行。"
    section = FullSection(
        "完整交通组织章节",
        body,
        parse,
        (3, 4),
        (
            {
                "evidence_id": evidence,
                "physical_page": 3,
                "text": text,
                "digest": sha(text.encode()),
            },
        ),
    )
    return chapter_package(
        rule={
            "id": rule,
            "statement": "交通组织",
            "conditions": {},
            "exceptions": {},
            "bands": [{"id": "good"}],
            "maximum": "5",
        },
        body_version_id=body,
        parse_id=parse,
        sections=[section],
        required_ids=[section.id],
        method_digest=digest(),
    )


class SyntheticPort:
    def __init__(self, mutate=None):
        self.requests = []
        self.mutate = mutate or (lambda value: value)

    def judge(self, *, package, replicate, logical_key):
        self.requests.append((package, replicate, logical_key))
        evidence = package["sections"][0]["blocks"][0]
        value = {
            "band_id": "good",
            "reason": "合成工程判断",
            "evidence": [{"evidence_id": evidence["evidence_id"], "quote": evidence["text"]}],
            "coverage": "complete",
            "status": "valid",
        }
        return self.mutate(value), "synthetic-call-" + str(replicate)


def test_same_full_input_three_unique_independent_judgments_without_final_score():
    port = SyntheticPort()
    value = package()
    result = judge_three(value, port)
    assert result["valid_replicates"] == 3
    assert result["prediction"]["mode"] == "unavailable"
    assert "value" not in result["prediction"]
    assert [request[1] for request in port.requests] == [1, 2, 3]
    assert len({request[2] for request in port.requests}) == 3
    assert all(request[0] == value for request in port.requests)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v | {"band_id": "invented"},
        lambda v: v | {"status": "unable_to_judge"},
        lambda v: v | {"coverage": "partial"},
        lambda v: v | {"evidence": [{"evidence_id": str(uuid4()), "quote": "伪造原文"}]},
    ],
)
def test_invalid_judgment_no_retry_no_zero_fill(mutate):
    port = SyntheticPort(mutate)
    with pytest.raises(DomainError):
        judge_three(package(), port)
    assert len(port.requests) == 1


def test_label_chat_and_tampered_packages_are_rejected_before_port():
    value = package()
    port = SyntheticPort()
    for key in ["outcomes", "official_score", "messages", "summary"]:
        malicious = value | {key: "考试答案"}
        malicious["digest"] = sha({k: v for k, v in malicious.items() if k != "digest"})
        with pytest.raises(DomainError):
            judge_three(malicious, port)
    altered = copy.deepcopy(value)
    altered["sections"][0]["blocks"][0]["text"] = "事后修改稿"
    with pytest.raises(DomainError):
        judge_three(altered, port)
    assert not port.requests
    nested = copy.deepcopy(value)
    nested["rule"]["conditions"] = {"official_score": "0"}
    nested["digest"] = sha({key: item for key, item in nested.items() if key != "digest"})
    with pytest.raises(DomainError):
        judge_three(nested, port)
    assert not port.requests


def test_incomplete_sections_and_wrong_version_cannot_be_complete():
    value = package()
    block = value["sections"][0]["blocks"][0]
    for section in [
        FullSection(
            "c", value["body_version_id"], value["parse_id"], (3,), (block,), ("image_not_read",)
        ),
        FullSection("c", str(uuid4()), value["parse_id"], (3,), (block,)),
    ]:
        with pytest.raises(DomainError):
            chapter_package(
                rule=value["rule"],
                body_version_id=value["body_version_id"],
                parse_id=value["parse_id"],
                sections=[section],
                required_ids=["c"],
                method_digest=digest(),
            )


def test_profile_is_research_scoped_and_never_qualifies_remote_alias():
    value = descriptor()
    assert value["purpose"] == "formal_score_research"
    assert value["formal_scoring_eligible"] is False
    assert value["immutable_weights_verified"] is False
    assert sha(value) == digest()


def test_actual_openapi_only_registers_real_routes_and_closes_references():
    import json
    from copy import deepcopy

    from fastapi import FastAPI
    from fastapi.openapi.utils import get_openapi

    from apps.api.research_routes import router
    from packages.domain.runtime import ROOT
    from packages.research.contracts import extend_openapi

    app = FastAPI()
    app.include_router(router)
    actual = get_openapi(title="合成研究实际接口检查", version="0.1.0", routes=app.routes)
    planned = json.loads((ROOT / "packages/contracts/openapi/openapi.json").read_text())
    actual["components"] = deepcopy(planned["components"])
    before = set(actual["paths"])
    extend_openapi(actual)
    assert set(actual["paths"]) == before
    operations = [
        operation
        for path in actual["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post"}
    ]
    assert (
        len(operations) == 23 and len({operation["operationId"] for operation in operations}) == 23
    )

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].startswith("#/")
                target = actual
                for part in value["$ref"][2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(actual)
