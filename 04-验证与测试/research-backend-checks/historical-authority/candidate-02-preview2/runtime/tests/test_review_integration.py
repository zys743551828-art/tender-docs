"""真实PG/S3/ASGI评审纵向机制；模型显式MockTransport，不声称付费效果或Kafka验收。"""

import json
import os
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.auth import password_hash
from apps.api.main import app
from apps.worker import engine
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.reviews import tick
from packages.domain.runtime import FORMAT_CHECKER, clean, connection, required, sha, uid, validate

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REVIEW_INTEGRATION") != "1", reason="需要独立真实PG/S3机制环境"
)


def pdf_bytes(text):
    content = b"BT /F1 12 Tf 20 100 Td (" + text.encode("ascii") + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1600 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = []
    for number, value in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(number).encode() + b" 0 obj\n" + value + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode()
    return (
        data
        + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )


def pump(operation_id):
    seen = set()
    for _ in range(10):
        with connection(True) as c:
            op = c.execute("SELECT * FROM operations WHERE id=%s", (operation_id,)).fetchone()
            if op["state"] in ("succeeded", "failed", "cancelled"):
                return op
            events = c.execute(
                "SELECT payload FROM outbox WHERE operation_id=%s ORDER BY available_at,id",
                (operation_id,),
            ).fetchall()
        for event in events:
            message = event["payload"]
            if message["event_id"] not in seen and message["event_type"] == "step.requested":
                seen.add(message["event_id"])
                engine.handle(message)
    raise AssertionError("持久阶段没有有限完成")


@pytest.mark.parametrize("parser_runtime,detectors", [(False, False), (True, False), (True, True)])
def test_real_freeze_and_full_core_pipeline_with_offline_model(
    monkeypatch, parser_runtime, detectors
):
    fixture_output = os.environ.get("REVIEW_FIXTURE_ONLY_OUTPUT")
    if fixture_output and (not parser_runtime or detectors):
        pytest.skip("保留夹具仅显式完整Parser03/可选检测关闭变体")
    if parser_runtime:
        if os.environ.get("RUN_PARSING_WORKER_INTEGRATION") != "1":
            pytest.skip("真实解析端口另行显式启用")
        for line in (
            []
            if fixture_output and os.environ.get("PARSING_RUNTIME_TOKEN")
            else Path(".local/parsing.env").read_text().splitlines()
        ):
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                monkeypatch.setenv(key, shlex.split(value)[0])
    else:
        monkeypatch.delenv("PARSING_RUNTIME_TOKEN", raising=False)
    calls = []
    core_verdict = {"value": "violated"}
    detector_calls = []

    def handler(request):
        incoming = json.loads(request.content)
        content = json.loads(incoming["messages"][-1]["content"])
        if "check_kind" in content:
            detector_calls.append(content["check_kind"])
            output = {
                "read_evidence_ids": [str(b["id"]) for b in content["full_body_blocks"]]
                + [str(b["id"]) for doc in content["required_documents"] for b in doc["blocks"]],
                "issues": [],
                "gaps": [],
            }
        elif "documents" in content:
            blocks = [block for doc in content["documents"] for block in doc["evidence_blocks"]]
            statement = blocks[0]["text_content"]
            output = {
                "rules": [
                    {
                        "kind": "disqualification",
                        "statement": statement,
                        "conditions": [],
                        "exceptions": [],
                        "scope": {"bid_id": content["bid_id"], "description": "本项目"},
                        "maximum": None,
                        "bands": [],
                        "evidence_quotes": [
                            {"evidence_id": str(blocks[0]["id"]), "quote": statement}
                        ],
                    }
                ],
                "ambiguities": [],
                "read_evidence_ids": [str(b["id"]) for b in blocks],
            }
        else:
            blocks = content["full_body_blocks"]
            output = {
                "read_evidence_ids": [str(b["id"]) for b in blocks],
                "judgments": [
                    {
                        "rule_id": rule["id"],
                        "verdict": core_verdict["value"],
                        "statement": "已核对逐字承诺与本条要求",
                        "body_quotes": [
                            {
                                "evidence_id": str(blocks[0]["id"]),
                                "quote": blocks[0]["text_content"],
                            }
                        ],
                    }
                    for rule in content["rules"]
                ],
            }
        if "check_kind" not in content:
            calls.append(request)
        return httpx.Response(
            200,
            json={
                "id": "synthetic-" + uuid4().hex,
                "model": "deepseek-flash",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output, ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    model = DeepSeekClient(api_key="synthetic-no-external", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(DeepSeekClient, "from_env", classmethod(lambda cls: model))
    login, password = "review-probe-" + uuid4().hex, uuid4().hex
    with connection() as c:
        actor = uid()
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'离线评审纵向自测')", (actor,))
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (actor, login, password_hash(password)),
        )
    with TestClient(app) as client:
        origin = required("APP_ORIGIN")
        auth = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert auth.status_code == 200
        csrf = auth.json()["csrf_token"]

        def headers(tag=None):
            return {"Origin": origin, "X-CSRF-Token": csrf, "Idempotency-Key": uuid4().hex} | (
                {"If-Match": tag} if tag else {}
            )

        def put_upload(grant, data):
            url = urlsplit(grant["upload_url"])
            if url.path.startswith("/api/v1/object-transfers/"):
                return client.put(
                    url.path + "?" + url.query,
                    content=data,
                    headers={
                        "Origin": origin,
                        "X-CSRF-Token": csrf,
                        "Content-Type": "application/pdf",
                        "Content-Length": str(len(data)),
                    },
                )
            return httpx.put(grant["upload_url"], content=data, trust_env=False)

        company = client.post(
            "/api/v1/identity/bidders",
            json={
                "name": "合成明确投标主体" + uuid4().hex[:8],
                "reason": "合成测试明确本项目投标主体，不声明资质已核验",
            },
            headers=headers(),
        )
        assert company.status_code == 201
        bid = client.post(
            "/api/v1/bids",
            json={"title": "离线模型核心纵向", "bidder_id": company.json()["bidder"]["id"]},
            headers=headers(),
        ).json()["id"]
        settings = client.get(f"/api/v1/bids/{bid}/settings")
        changed = client.patch(
            f"/api/v1/bids/{bid}/settings",
            json={"consistency": detectors, "scope_residue": detectors},
            headers=headers(settings.headers["etag"]),
        )
        assert changed.status_code == 200
        batch = client.post(
            f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers()
        ).json()["id"]
        uploads = []
        marker = uuid4().hex[:8]
        for role, text in [
            (
                "tender",
                "Mandatory: provide 24 month warranty. Failure is disqualification. " + marker,
            ),
            ("body", "We provide a 12 month warranty. " + marker),
        ]:
            data = pdf_bytes(text)
            response = client.post(
                f"/api/v1/bids/{bid}/uploads",
                json={
                    "batch_id": batch,
                    "role": role,
                    "filename": role + ".pdf",
                    "byte_size": len(data),
                    "declared_sha256": sha(data),
                    "target": {"kind": "new_file"},
                },
                headers=headers(),
            )
            assert response.status_code == 200, response.text
            grant = response.json()
            validate("UploadGrant", grant)
            valid_url = FORMAT_CHECKER.conforms(grant["upload_url"], "uri")
            assert valid_url
            transfer = put_upload(grant, data)
            assert transfer.status_code == 200
            completed = client.post(
                f"/api/v1/uploads/{grant['upload_id']}/complete",
                json={"parts": [], "declared_sha256": sha(data)},
                headers=headers(),
            )
            assert completed.status_code == 202, completed.text
            assert pump(completed.json()["operation"]["id"])["state"] == "succeeded"
            uploads.append(client.get(f"/api/v1/uploads/{grant['upload_id']}").json())
        tag = client.get(f"/api/v1/bids/{bid}").headers["etag"]
        commit = client.post(
            f"/api/v1/bids/{bid}/upload-batches/{batch}/commit",
            json={
                "accepted_upload_ids": [u["id"] for u in uploads],
                "accepted_binding_ids": [u["binding_id"] for u in uploads],
                "explicit_partial_acceptance": False,
            },
            headers=headers(tag),
        )
        assert commit.status_code == 202, commit.text
        assert pump(commit.json()["operation"]["id"])["state"] == "succeeded"
        if parser_runtime and not fixture_output:
            # document/2完成即可从知识卡选择原页，不等待任何RAG向量任务。
            source_version = uploads[0]["document_version_id"]
            candidate = client.post(
                "/api/v1/knowledge",
                json={
                    "title": "真实解析原页立即可选",
                    "content": "仍为待完善知识候选",
                    "source_version_ids": [source_version],
                    "scope": "本项目",
                },
                headers=headers(),
            )
            assert candidate.status_code == 201
            positions = client.get(
                f"/api/v1/knowledge/{candidate.json()['id']}/source-positions",
                params={"document_version_id": source_version, "physical_page": 1},
            )
            assert (
                positions.status_code == 200
                and positions.json()["readiness"] == "readable_text_positions_only"
            )
            assert positions.json()["items"]
            with connection() as c:
                assert (
                    c.execute(
                        "SELECT count(*) AS n FROM retrieval.chunks WHERE parse_id=%s",
                        (positions.json()["parse_id"],),
                    ).fetchone()["n"]
                    == 0
                )
        before = client.get(f"/api/v1/bids/{bid}/review-state")
        assert before.status_code == 200 and before.json()["request"]["state"] == "preparing"
        assert before.json()["current_review"] is None
        tick(bid)
        prepared = client.get(f"/api/v1/bids/{bid}/review-state").json()["request"]
        assert pump(prepared["prepare_operation_id"])["state"] == "succeeded"
        tick(bid)
        state = client.get(f"/api/v1/bids/{bid}/review-state").json()
        assert state["request"]["review_id"], state
        review_id = state["request"]["review_id"]
        assert pump(state["current_review"]["operation_id"])["state"] == "succeeded"
        tick(bid)
        review = client.get(f"/api/v1/reviews/{review_id}")
        assert review.status_code == 200, review.text
        validate("ReviewView", review.json())
        assert review.json()["current"] and review.json()["prediction"]["mode"] == "unavailable"
        assert review.json()["core_coverage"] == ("complete" if parser_runtime else "partial")
        if not fixture_output:
            checks_view = client.get(f"/api/v1/reviews/{review_id}/content-checks")
            assert checks_view.status_code == 200
            statuses = checks_view.json()["checks"]
            assert all(
                item["state"] == ("complete" if detectors else "not_run") for item in statuses
            )
            assert all(item["new_finding_count"] == (0 if detectors else None) for item in statuses)
            assert set(detector_calls) == ({"consistency", "scope_residue"} if detectors else set())
        matrix = client.get(f"/api/v1/reviews/{review_id}/matrix")
        assert matrix.status_code == 200, matrix.text
        validate("MatrixView", matrix.json())
        assert matrix.json()["rows"][0]["verdict"] == "violated"
        risks = client.get(f"/api/v1/reviews/{review_id}/findings")
        assert risks.status_code == 200, risks.text
        assert risks.json()["items"][0]["severity"] == "critical"
        from packages.domain.submission_gate import evaluate

        with connection() as c:
            gate = evaluate(c, bid)
            codes = {item["code"] for item in gate["blockers"]}
            assert (
                not gate["allowed"]
                and {"CORE_RULE_CONFIRMATION_REQUIRED", "OPEN_BLOCKING_FINDING"} <= codes
            )
        manifest = client.get(f"/api/v1/reviews/{review_id}/manifest")
        assert manifest.status_code == 200, manifest.text
        validate("ReviewManifest", manifest.json())
        assert manifest.json()["body_version_id"] == next(
            u["document_version_id"] for u in uploads if u["filename"] == "body.pdf"
        )
        history = client.get(f"/api/v1/bids/{bid}/reviews")
        assert history.status_code == 200 and history.json()["items"][0]["id"] == review_id
        rule = matrix.json()["rows"][0]["rule"]
        single = client.get(f"/api/v1/rules/{rule['id']}")
        assert single.headers["etag"] and single.json()["confirmation"] == "pending"
        batch_rules = client.get(f"/api/v1/bids/{bid}/rules")
        selected = {
            "base_rule_set_id": batch_rules.json()["id"],
            "rule_ids": [rule["id"]],
            "reason": "明确勾选待确认条款",
        }
        invalid_batch = client.post(
            f"/api/v1/bids/{bid}/rules/decisions",
            json=selected | {"rule_ids": [rule["id"], str(uuid4())]},
            headers=headers(batch_rules.headers["etag"]),
        )
        assert invalid_batch.status_code == 404
        assert client.get(f"/api/v1/rules/{rule['id']}").json()["confirmation"] == "pending"
        selected_headers = headers(batch_rules.headers["etag"])
        batch_confirmed = client.post(
            f"/api/v1/bids/{bid}/rules/decisions", json=selected, headers=selected_headers
        )
        assert (
            batch_confirmed.status_code == 200
            and batch_confirmed.json()["rules"][0]["confirmation"] == "confirmed"
        )
        batch_replay = client.post(
            f"/api/v1/bids/{bid}/rules/decisions", json=selected, headers=selected_headers
        )
        assert (
            batch_replay.status_code == 200
            and batch_replay.headers["Idempotent-Replayed"] == "true"
        )
        single = client.get(f"/api/v1/rules/{rule['id']}")
        confirmation_headers = headers(single.headers["etag"])
        confirmation_body = {
            "decision": "confirmed",
            "reason": "已逐字核对条款性质和适用范围",
            "evidence_ids": [],
        }
        confirmed = client.post(
            f"/api/v1/rules/{rule['id']}/decisions",
            json=confirmation_body,
            headers=confirmation_headers,
        )
        assert confirmed.status_code == 200
        confirmed_replay = client.post(
            f"/api/v1/rules/{rule['id']}/decisions",
            json=confirmation_body,
            headers=confirmation_headers,
        )
        assert (
            confirmed_replay.status_code == 200
            and confirmed_replay.headers["Idempotent-Replayed"] == "true"
        )
        assert (
            client.get(f"/api/v1/reviews/{review_id}").json()["coverage_layers"][
                "human_confirmation_pending"
            ]
            == 0
        )
        current_rule = client.get(f"/api/v1/rules/{rule['id']}")
        deferred = client.post(
            f"/api/v1/rules/{rule['id']}/decisions",
            json={"decision": "deferred", "reason": "暂缓确认解释，不改变条款", "evidence_ids": []},
            headers=headers(current_rule.headers["etag"]),
        )
        assert deferred.status_code == 200
        still_current = client.get(f"/api/v1/reviews/{review_id}").json()
        assert (
            still_current["current"]
            and still_current["coverage_layers"]["human_confirmation_pending"] == 1
            and len(calls) == 2
        )
        confirmed = client.post(
            f"/api/v1/rules/{rule['id']}/decisions",
            json=confirmation_body,
            headers=headers(client.get(f"/api/v1/rules/{rule['id']}").headers["etag"]),
        )
        assert confirmed.status_code == 200
        finding_id = risks.json()["items"][0]["id"]
        observed = client.get(f"/api/v1/findings/{finding_id}")
        forbidden = client.post(
            f"/api/v1/findings/{finding_id}/decisions",
            json={
                "decision": "resolved_by_recheck",
                "reason": "不能人工假装已复核",
                "evidence_ids": [],
            },
            headers=headers(observed.headers["etag"]),
        )
        assert forbidden.status_code == 422
        dismissed = client.post(
            f"/api/v1/findings/{finding_id}/decisions",
            json={"decision": "dismiss", "reason": "合成用例明确人工驳回", "evidence_ids": []},
            headers=headers(observed.headers["etag"]),
        )
        assert dismissed.status_code == 200
        assert client.get(f"/api/v1/reviews/{review_id}").json()["risk_count"] == 0
        reopened = client.post(
            f"/api/v1/findings/{finding_id}/decisions",
            json={
                "decision": "reopen",
                "reason": "恢复合成风险用于后续跨轮复验",
                "evidence_ids": [],
            },
            headers=headers(client.get(f"/api/v1/findings/{finding_id}").headers["etag"]),
        )
        assert reopened.status_code == 200
        for evidence_id in risks.json()["items"][0]["evidence_ids"]:
            assert client.get(f"/api/v1/evidence/{evidence_id}").status_code == 200
        first_evidence = risks.json()["items"][0]["evidence_ids"][0]
        authoring = client.get(f"/api/v1/evidence/{first_evidence}/authoring-reference")
        assert authoring.status_code == 200 and authoring.json()["authoring_reference"] is not None
        assert authoring.json()["authoring_reference"]["evidence_id"] == first_evidence
        assert (
            authoring.json()["parse_run_id"]
            == client.get(f"/api/v1/evidence/{first_evidence}").json()["parse_run_id"]
        )
        assert len(calls) == 2
        with connection(True) as c:
            assert c.execute(
                "SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s",
                (bid,),
            ).fetchone()["n"] == (4 if detectors else 2)
            rows = c.execute(
                "SELECT i.manifest,i.manifest_digest FROM review_inputs i WHERE bid_id=%s", (bid,)
            ).fetchall()
            assert len(rows) == 1 and rows[0]["manifest_digest"] == review.json()["manifest_digest"]
        assert clean(review.json()) == review.json()
        request = {
            "body_version_id": manifest.json()["body_version_id"],
            "material_revision": manifest.json()["material_revision"],
            "trigger": "manual",
        }
        before_bid = client.get(f"/api/v1/bids/{bid}")
        assert client.get(f"/api/v1/bids/{bid}").headers["etag"] == before_bid.headers["etag"]
        missing = client.post(f"/api/v1/bids/{bid}/reviews", json=request, headers=headers())
        assert missing.status_code == 428
        request_headers = headers(before_bid.headers["etag"])
        accepted = client.post(f"/api/v1/bids/{bid}/reviews", json=request, headers=request_headers)
        assert accepted.status_code == 202, accepted.text
        assert client.get(f"/api/v1/bids/{bid}").headers["etag"] != before_bid.headers["etag"]
        stale = client.post(
            f"/api/v1/bids/{bid}/reviews", json=request, headers=headers(before_bid.headers["etag"])
        )
        assert stale.status_code == 412
        replay = client.post(f"/api/v1/bids/{bid}/reviews", json=request, headers=request_headers)
        assert replay.status_code == 202 and replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json() == accepted.json()
        tick(bid)
        tick(bid)
        reused = client.get(f"/api/v1/bids/{bid}/review-state").json()
        assert reused["request"]["review_id"] == review_id and len(calls) == 2
        new_request = request | {"rerun_nonce": str(uuid4())}
        submitted = client.post(
            f"/api/v1/bids/{bid}/reviews",
            json=new_request,
            headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
        )
        assert submitted.status_code == 202, submitted.text
        tick(bid)
        repeated = client.get(f"/api/v1/bids/{bid}/review-state").json()
        assert repeated["request"]["review_id"] != review_id
        assert repeated["current_review"]["input_digest"] == review.json()["input_digest"]
        assert pump(repeated["current_review"]["operation_id"])["state"] == "succeeded"
        tick(bid)
        assert len(calls) == 3  # 显式nonce只增加核心核对，规则/来源相同不重复提取。
        repeated_risks = client.get(
            f"/api/v1/reviews/{repeated['request']['review_id']}/findings"
        ).json()
        assert len(repeated_risks["items"]) == 1
        assert repeated_risks["items"][0]["origin_review_id"] == review_id
        assert repeated_risks["items"][0]["detection_run_state"] == "ran"
        assert (
            client.get(f"/api/v1/reviews/{repeated['request']['review_id']}").json()["risk_count"]
            == 1
        )
        settings = client.get(f"/api/v1/bids/{bid}/settings")
        hidden = client.patch(
            f"/api/v1/bids/{bid}/settings",
            json={"show_prediction": False},
            headers=headers(settings.headers["etag"]),
        )
        assert hidden.status_code == 200, hidden.text
        current = client.get(f"/api/v1/reviews/{repeated['request']['review_id']}").json()
        assert current["prediction"]["mode"] == "hidden" and current["current"] and len(calls) == 3

        # 实际新稿先冻结时保留旧关键风险；新稿有效逐条复核后才能解除。
        data = pdf_bytes("We provide the mandatory 24 month warranty. " + marker)
        batch2 = client.post(
            f"/api/v1/bids/{bid}/upload-batches", json={}, headers=headers()
        ).json()["id"]
        body_version = client.get(f"/api/v1/documents/{manifest.json()['body_version_id']}").json()
        grant_response = client.post(
            f"/api/v1/bids/{bid}/uploads",
            json={
                "batch_id": batch2,
                "role": "body",
                "filename": "body-v2.pdf",
                "byte_size": len(data),
                "declared_sha256": sha(data),
                "target": {
                    "kind": "new_version",
                    "file_id": body_version["file_id"],
                    "expected_file_revision": body_version["file_head_revision"],
                },
            },
            headers=headers(),
        )
        assert grant_response.status_code == 200, grant_response.text
        grant = grant_response.json()
        assert put_upload(grant, data).status_code == 200
        completed = client.post(
            f"/api/v1/uploads/{grant['upload_id']}/complete",
            json={"parts": [], "declared_sha256": sha(data)},
            headers=headers(),
        )
        assert pump(completed.json()["operation"]["id"])["state"] == "succeeded"
        upload = client.get(f"/api/v1/uploads/{grant['upload_id']}").json()
        committed = client.post(
            f"/api/v1/bids/{bid}/upload-batches/{batch2}/commit",
            json={
                "accepted_upload_ids": [upload["id"]],
                "accepted_binding_ids": [upload["binding_id"]],
                "explicit_partial_acceptance": False,
            },
            headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
        )
        assert committed.status_code == 202, committed.text
        assert pump(committed.json()["operation"]["id"])["state"] == "succeeded"
        tick(bid)
        tick(bid)
        updated = client.get(f"/api/v1/bids/{bid}/review-state").json()
        new_review_id = updated["request"]["review_id"]
        assert new_review_id and new_review_id != repeated["request"]["review_id"]
        assert all(
            item["confirmation"] == "confirmed"
            for item in client.get(f"/api/v1/bids/{bid}/rules").json()["rules"]
        )

        retained = client.get(f"/api/v1/reviews/{new_review_id}/findings").json()["items"]
        assert len(retained) == 1 and retained[0]["lifecycle"] == "open"
        assert retained[0]["detection_run_state"] == "not_run_this_round"
        core_verdict["value"] = "satisfied"
        assert pump(updated["current_review"]["operation_id"])["state"] == "succeeded"
        tick(bid)
        resolved = client.get(f"/api/v1/reviews/{new_review_id}/findings").json()["items"]
        assert len(resolved) == 1 and resolved[0]["lifecycle"] == (
            "resolved_by_recheck" if parser_runtime else "open"
        )
        assert client.get(f"/api/v1/reviews/{new_review_id}").json()["risk_count"] == (
            0 if parser_runtime else 1
        )
        assert len(calls) == 4
        with connection() as c:
            gate = evaluate(c, bid)
            assert bool(gate["blocking_finding_ids"]) is (not parser_runtime)
            if parser_runtime:
                assert gate["allowed"], [item["code"] for item in gate["blockers"]]
            else:
                assert not gate["allowed"] and {item["code"] for item in gate["blockers"]} == {
                    "CORE_COVERAGE_INCOMPLETE",
                    "OPEN_BLOCKING_FINDING",
                }
            assert (
                gate["current_review_manifest_digest"]
                == client.get(f"/api/v1/reviews/{new_review_id}").json()["manifest_digest"]
            )

        if fixture_output:
            retained_manifest = client.get(f"/api/v1/reviews/{new_review_id}/manifest").json()
            metadata = {
                "scope": "synthetic API/Worker/Parser03/MockTransport mechanism fixture; not actual model quality",
                "paid_calls": 0,
                "bid_id": bid,
                "review_id": new_review_id,
                "member_id": str(actor),
                "manifest": retained_manifest,
                "body_version_id": retained_manifest["body_version_id"],
                "gate_allowed": gate["allowed"],
                "origin": origin,
            }
            target = Path(fixture_output)
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
            return

        # 选择真实冻结轮次比较，跨刷新/跨浏览器可发现，不借当前轮冒充历史受检稿。
        comparison_url = f"/api/v1/bids/{bid}/comparisons"
        comparison_body = {
            "left_review_id": review_id,
            "right_review_id": new_review_id,
            "same_method_rerun": False,
        }
        assert (
            client.post(comparison_url, json=comparison_body, headers=headers()).status_code == 428
        )
        comparison_headers = headers(client.get(f"/api/v1/bids/{bid}").headers["etag"])
        comparison = client.post(comparison_url, json=comparison_body, headers=comparison_headers)
        assert comparison.status_code == 202
        assert (
            client.post(comparison_url, json=comparison_body, headers=comparison_headers).json()
            == comparison.json()
        )
        comparison_id = comparison.json()["resource_id"]
        queued = client.get(f"/api/v1/comparisons/{comparison_id}")
        assert queued.status_code == 200 and queued.json()["result"] is None
        assert pump(comparison.json()["operation"]["id"])["state"] == "succeeded"
        compared = client.get(f"/api/v1/comparisons/{comparison_id}")
        assert compared.status_code == 200 and compared.json()["current_context_matches"]
        result = compared.json()["result"]
        assert (
            result["left_manifest"]["body_version_id"]
            != result["right_manifest"]["body_version_id"]
        )
        assert result["score_delta"] is None and result["body_changes"]["items"]
        assert result["findings"][0]["status"] == (
            "resolved_by_recheck" if parser_runtime else "not_rechecked"
        )
        assert client.get(comparison_url).json()["items"][0]["id"] == comparison_id
        assert (
            client.post(
                comparison_url,
                json=comparison_body | {"same_method_rerun": True},
                headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
            ).status_code
            == 409
        )
        from jsonschema import Draft202012Validator

        comparison_contract = app.openapi()
        Draft202012Validator(
            {
                "$ref": "#/components/schemas/ReviewComparisonView",
                "components": comparison_contract["components"],
            }
        ).validate(compared.json())
        assert len(calls) == 4  # 比较没有新增任何模型调用。
        from apps.worker import comparison_handlers
        from packages.domain.runtime import DomainError

        another = client.post(
            comparison_url,
            json=comparison_body,
            headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
        )
        assert another.status_code == 202
        with connection(True) as c:
            old_method_message = c.execute(
                "SELECT payload FROM outbox WHERE operation_id=%s",
                (another.json()["operation"]["id"],),
            ).fetchone()["payload"]
        method_lease = engine.acquire(old_method_message)
        assert method_lease
        with monkeypatch.context() as patch:
            patch.setattr(comparison_handlers, "method_digest", lambda: "0" * 64)
            with pytest.raises(DomainError) as wrong_method:
                engine.run_compute(old_method_message, method_lease)
            assert wrong_method.value.code == "COMPARISON_METHOD_CHANGED"
        assert engine.finish(old_method_message, method_lease, None, wrong_method.value)
        assert (
            client.get(f"/api/v1/comparisons/{another.json()['resource_id']}").json()["error_code"]
            == "COMPARISON_METHOD_CHANGED"
        )

        # 实交只记录已经发生的事实；真实字节校验与闸门在服务端，评分缺失独立。
        gate_http = client.get(f"/api/v1/bids/{bid}/submission-gate")
        assert gate_http.status_code == 200
        from jsonschema import Draft202012Validator

        actual_contract = app.openapi()
        Draft202012Validator(
            {"$ref": "#/components/schemas/GateView", "components": actual_contract["components"]}
        ).validate(gate_http.json())
        # 三种真实展示产物均留独立artifact身份，PDF批注保留原正文且不伪造高亮。
        for artifact_kind, fmt in (
            ("review_report", "docx"),
            ("review_report", "pdf"),
            ("annotated_review_copy", "pdf"),
        ):
            report_context = client.get(f"/api/v1/reviews/{new_review_id}/report-context")
            assert report_context.status_code == 200
            context_body = report_context.json()
            export_body = {
                "artifact_kind": artifact_kind,
                "format": fmt,
                "presentation_revision": context_body["presentation_revision"],
                "source_impact_revision": context_body["source_impact_revision"],
            }
            report_headers = headers(report_context.headers["etag"])
            export_url = f"/api/v1/reviews/{new_review_id}/report-exports"
            exported = client.post(export_url, json=export_body, headers=report_headers)
            assert exported.status_code == 202
            assert pump(exported.json()["operation"]["id"])["state"] == "succeeded"
            exported_view = client.get(f"/api/v1/report-exports/{exported.json()['resource_id']}")
            assert exported_view.status_code == 200
            assert exported_view.json()["state"] == "succeeded"
            artifact = exported_view.json()["document_version_id"]
            assert artifact
            doc = client.get(f"/api/v1/documents/{artifact}")
            assert doc.status_code == 200 and doc.json()["artifact_kind"] == artifact_kind
            assert (
                client.get(f"/api/v1/bids/{bid}").json()["current_body_id"]
                == upload["document_version_id"]
            )
            assert (
                client.post(export_url, json=export_body, headers=report_headers).json()
                == exported.json()
            )
            reuse_context = client.get(f"/api/v1/reviews/{new_review_id}/report-context")
            reused_export = client.post(
                export_url, json=export_body, headers=headers(reuse_context.headers["etag"])
            )
            assert (
                reused_export.status_code == 202 and reused_export.json()["disposition"] == "reused"
            )
            assert reused_export.json()["resource_id"] == exported.json()["resource_id"]
            from packages.domain import storage

            with connection() as c:
                object_row = c.execute(
                    "SELECT b.* FROM blobs b JOIN document_versions v ON v.blob_id=b.id WHERE v.id=%s",
                    (artifact,),
                ).fetchone()
            output_bytes = storage.get(object_row["object_key"])
            assert sha(output_bytes) == object_row["digest"]
            if fmt == "docx":
                import io
                import zipfile

                with zipfile.ZipFile(io.BytesIO(output_bytes)) as archive:
                    xml = archive.read("word/document.xml").decode()
                assert "不是提交正文" in xml and new_review_id in xml
            else:
                import pypdfium2 as pdfium
                import pypdfium2.raw as raw

                with closing(pdfium.PdfDocument(output_bytes)) as rendered:
                    assert len(rendered) > 0
                    if artifact_kind == "annotated_review_copy":
                        with closing(rendered[0]) as annotated_page:
                            assert raw.FPDFPage_GetAnnotCount(annotated_page) >= 1
                            with closing(annotated_page.get_textpage()) as text:
                                assert "24 month warranty" in text.get_text_range()
        # 展示政策变更使旧tag失效，计算后迟到产物不得发布为当前副本。
        settings_before_export = client.get(f"/api/v1/bids/{bid}/settings")
        visibility = settings_before_export.json()["settings"]["show_prediction"]
        hidden = client.patch(
            f"/api/v1/bids/{bid}/settings",
            json={"show_prediction": not visibility},
            headers=headers(settings_before_export.headers["etag"]),
        )
        assert hidden.status_code == 200
        assert (
            client.post(
                export_url, json=export_body, headers=headers(report_context.headers["etag"])
            ).status_code
            == 412
        )
        hidden_context = client.get(f"/api/v1/reviews/{new_review_id}/report-context")
        hidden_request = {
            "artifact_kind": "review_report",
            "format": "docx",
            "presentation_revision": hidden_context.json()["presentation_revision"],
            "source_impact_revision": hidden_context.json()["source_impact_revision"],
        }
        delayed = client.post(
            export_url, json=hidden_request, headers=headers(hidden_context.headers["etag"])
        )
        assert delayed.status_code == 202
        with connection(True) as c:
            delayed_message = c.execute(
                "SELECT payload FROM outbox WHERE operation_id=%s ORDER BY available_at,id LIMIT 1",
                (delayed.json()["operation"]["id"],),
            ).fetchone()["payload"]
        lease = engine.acquire(delayed_message)
        assert lease
        delayed_output = engine.run_compute(delayed_message, lease)
        restored_policy = client.patch(
            f"/api/v1/bids/{bid}/settings",
            json={"show_prediction": visibility},
            headers=headers(hidden.headers["etag"]),
        )
        assert restored_policy.status_code == 200
        from packages.domain.runtime import DomainError

        with pytest.raises(DomainError) as stale_report:
            engine.finish(delayed_message, lease, delayed_output)
        assert stale_report.value.code == "REPORT_PRESENTATION_CHANGED"
        assert engine.finish(delayed_message, lease, None, stale_report.value)
        delayed_view = client.get(f"/api/v1/report-exports/{delayed.json()['resource_id']}").json()
        assert delayed_view["state"] == "failed" and delayed_view["document_version_id"] is None
        assert delayed_view["error_code"] == "REPORT_PRESENTATION_CHANGED"
        recovered_context = client.get(f"/api/v1/reviews/{new_review_id}/report-context")
        recovered_body = hidden_request | {
            "presentation_revision": recovered_context.json()["presentation_revision"],
            "source_impact_revision": recovered_context.json()["source_impact_revision"],
        }
        recovered = client.post(
            export_url, json=recovered_body, headers=headers(recovered_context.headers["etag"])
        )
        assert (
            recovered.status_code == 202
            and recovered.json()["resource_id"] != delayed.json()["resource_id"]
        )
        assert pump(recovered.json()["operation"]["id"])["state"] == "succeeded"
        report_page = client.get(export_url, params={"limit": 2})
        assert report_page.status_code == 200 and report_page.json()["has_more"]
        second_report_page = client.get(
            export_url, params={"limit": 100, "cursor": report_page.json()["next_cursor"]}
        )
        assert second_report_page.status_code == 200
        all_reports = report_page.json()["items"] + second_report_page.json()["items"]
        assert len({item["id"] for item in all_reports}) == len(all_reports) == 5
        Draft202012Validator(
            {
                "$ref": "#/components/schemas/ReportExportPage",
                "components": app.openapi()["components"],
            }
        ).validate(report_page.json())
        assert len(calls) == 4

        # 报告受理变更展示修订后，提交仍须读取真实当前项目tag。
        gate_http = client.get(f"/api/v1/bids/{bid}/submission-gate")
        with connection() as c:
            at = c.execute("SELECT clock_timestamp() AS at").fetchone()["at"].isoformat()
        submission_body = {
            "body_version_id": upload["document_version_id"],
            "review_id": new_review_id,
            "material_revision": updated["request"]["material_revision"],
            "submitted_at": at,
            "evidence_ref": upload["document_version_id"],
        }
        submissions_url = f"/api/v1/bids/{bid}/submissions"
        assert (
            client.post(submissions_url, json=submission_body, headers=headers()).status_code == 428
        )
        assert (
            client.post(
                submissions_url, json=submission_body, headers=headers('"stale"')
            ).status_code
            == 412
        )
        submission_headers = headers(gate_http.headers["etag"])
        wrong_purpose = client.post(
            submissions_url,
            json=submission_body | {"body_version_id": artifact},
            headers=headers(gate_http.headers["etag"]),
        )
        assert (
            wrong_purpose.status_code == 409
            and wrong_purpose.json()["code"] == "SUBMISSION_BODY_KIND_INVALID"
        )
        if parser_runtime:
            postdated = client.post(
                submissions_url,
                json=submission_body | {"submitted_at": "2000-01-01T00:00:00Z"},
                headers=headers(gate_http.headers["etag"]),
            )
            assert (
                postdated.status_code == 409
                and postdated.json()["code"] == "SUBMISSION_CHECKS_POSTDATE_FACT"
            )
        recorded = client.post(submissions_url, json=submission_body, headers=submission_headers)
        if parser_runtime:
            assert recorded.status_code == 201, recorded.json().get("code")
            record = recorded.json()
            assert record["gate_checked"] and record["kind"] == "standard" and record["current"]
            assert record["identity"]["binding_status"] == "confirmed"
            assert record["platform_submission_verified"] is False
            assert record["open_risk_snapshot"]["basis"] == "reconstructed_from_audit"
            assert record["open_risk_snapshot"]["open"] == 0
            assert (
                record["open_risk_snapshot"]["scope_definition"]
                == "disqualification_and_mandatory_root_findings"
            )
            Draft202012Validator(
                {
                    "$ref": "#/components/schemas/SubmissionView",
                    "components": actual_contract["components"],
                }
            ).validate(record)
            replay = client.post(submissions_url, json=submission_body, headers=submission_headers)
            assert replay.status_code == 201 and replay.json() == record
            assert replay.headers["idempotent-replayed"] == "true"
            listed = client.get(submissions_url)
            assert listed.status_code == 200 and listed.json()["items"][0]["id"] == record["id"]
            assert client.get(f"/api/v1/submissions/{record['id']}").json() == record
            duplicate = client.post(
                submissions_url,
                json=submission_body,
                headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
            )
            assert (
                duplicate.status_code == 409
                and duplicate.json()["code"] == "SUBMISSION_ALREADY_RECORDED"
            )
            # 更正追加事实，显式指定当前头；旧记录保留，不能第二次改同一旧头。
            corrected_fact = {
                "kind": "offline_fact",
                "submission": {
                    "actual_document_version_id": artifact,
                    "submitted_at": at,
                    "evidence_ref": upload["document_version_id"],
                    "explanation": "合成用户发现实际投递记录未经过完整平台核验，据实更正",
                },
                "reason": "合成实交事实更正，保留原检查历史",
            }
            correction_url = f"/api/v1/submissions/{record['id']}/corrections"
            correction_headers = headers(client.get(f"/api/v1/bids/{bid}").headers["etag"])
            correction = client.post(
                correction_url, json=corrected_fact, headers=correction_headers
            )
            assert correction.status_code == 201
            assert (
                not correction.json()["gate_checked"]
                and correction.json()["supersedes_id"] == record["id"]
            )
            assert correction.json()["current"]
            assert correction.json()["artifact_kind"] == "annotated_review_copy"
            assert correction.json()["open_risk_snapshot"]["basis"] == "unknown"
            assert correction.json()["open_risk_snapshot"]["open"] is None
            assert not client.get(f"/api/v1/submissions/{record['id']}").json()["current"]
            assert (
                client.post(correction_url, json=corrected_fact, headers=correction_headers).json()
                == correction.json()
            )
            old_head = client.post(
                correction_url,
                json=corrected_fact,
                headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
            )
            assert (
                old_head.status_code == 412
                and old_head.json()["code"] == "SUBMISSION_HEAD_CONFLICT"
            )
            page = client.get(submissions_url, params={"limit": 1}).json()
            assert page["has_more"]
            assert (
                client.get(
                    submissions_url, params={"cursor": page["next_cursor"], "limit": 1}
                ).json()["items"][0]["id"]
                == record["id"]
            )
        else:
            assert (
                recorded.status_code == 409 and recorded.json()["code"] == "SUBMISSION_GATE_BLOCKED"
            )

        # 正文换版生成派生规则包后，再次同输入按钮仍追溯原提取effect，不新增收费准备。
        with connection() as c:
            prepares_before = c.execute(
                "SELECT count(*) AS n FROM operations WHERE bid_id=%s AND handler_version='prepare/1'",
                (bid,),
            ).fetchone()["n"]
        replay_request = request | {
            "body_version_id": upload["document_version_id"],
            "material_revision": updated["request"]["material_revision"],
        }
        repeated_after_clone = client.post(
            f"/api/v1/bids/{bid}/reviews",
            json=replay_request,
            headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
        )
        assert repeated_after_clone.status_code == 202
        tick(bid)
        tick(bid)
        same_after_clone = client.get(f"/api/v1/bids/{bid}/review-state").json()
        assert same_after_clone["request"]["review_id"] == new_review_id and len(calls) == 4
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM operations WHERE bid_id=%s AND handler_version='prepare/1'",
                    (bid,),
                ).fetchone()["n"]
                == prepares_before
            )
            assert c.execute(
                "SELECT 1 FROM audit_entries WHERE bid_id=%s AND event_type='rules.source_equivalent_cloned'",
                (bid,),
            ).fetchone()

        # 控制面正在无锁核验原件时取消：迟到返回不能排入新的收费准备。
        import packages.domain.reviews as reviews_service

        waiting, released = threading.Event(), threading.Event()
        original_verify = reviews_service.verify_bytes

        def delayed_verify(documents):
            original_verify(documents)
            waiting.set()
            assert released.wait(5)

        cancel_request = request | {
            "body_version_id": upload["document_version_id"],
            "material_revision": updated["request"]["material_revision"],
            "rerun_nonce": str(uuid4()),
        }
        accepted = client.post(
            f"/api/v1/bids/{bid}/reviews",
            json=cancel_request,
            headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
        )
        assert accepted.status_code == 202, accepted.text
        monkeypatch.setattr(reviews_service, "verify_bytes", delayed_verify)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tick, bid)
            assert waiting.wait(5)
            pending = client.get(f"/api/v1/bids/{bid}/review-state")
            cancel_url = f"/api/v1/bids/{bid}/review-requests/{pending.json()['request']['request_id']}/cancel"
            reason = {"reason": "用户取消本次准备", "cancel_pending_same_input": True}
            missing = client.post(cancel_url, json=reason, headers=headers())
            assert missing.status_code == 428
            stale = client.post(cancel_url, json=reason, headers=headers('"stale"'))
            assert stale.status_code == 412
            cancel_headers = headers(pending.headers["etag"])
            cancelled = client.post(cancel_url, json=reason, headers=cancel_headers)
            assert (
                cancelled.status_code == 200 and cancelled.json()["request"]["state"] == "cancelled"
            )
            repeated_cancel = client.post(cancel_url, json=reason, headers=cancel_headers)
            assert (
                repeated_cancel.status_code == 200
                and repeated_cancel.headers["Idempotent-Replayed"] == "true"
            )
            released.set()
            future.result(timeout=5)
        monkeypatch.setattr(reviews_service, "verify_bytes", original_verify)
        tick(bid)
        final_state = client.get(f"/api/v1/bids/{bid}/review-state").json()
        assert final_state["request"]["state"] == "cancelled" and len(calls) == 4
        assert (
            client.get(
                f"/api/v1/operations/{pending.json()['request']['prepare_operation_id']}"
            ).json()["state"]
            == "succeeded"
        )
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='review.request_cancelled'",
                    (bid,),
                ).fetchone()["n"]
                == 1
            )

        # API角色机制模拟资料修订推进，关闭自动评审的旧未冻结意图须过期而非换稿收费。
        settings = client.get(f"/api/v1/bids/{bid}/settings")
        off = client.patch(
            f"/api/v1/bids/{bid}/settings",
            json={"auto_review": False},
            headers=headers(settings.headers["etag"]),
        )
        assert off.status_code == 200
        pending = client.post(
            f"/api/v1/bids/{bid}/reviews",
            json=cancel_request,
            headers=headers(client.get(f"/api/v1/bids/{bid}").headers["etag"]),
        )
        assert pending.status_code == 202
        with connection() as c:
            c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
            c.execute("UPDATE bids SET material_revision=material_revision+1 WHERE id=%s", (bid,))
        tick(bid)
        expired = client.get(f"/api/v1/bids/{bid}/review-state").json()["request"]
        assert expired["state"] == "superseded" and expired["review_id"] is None and len(calls) == 4
        # 真实人工补漏API原子新包、一次确认，不自动模型重跑。
        before_rules = client.get(f"/api/v1/bids/{bid}/rules")
        quote = client.get(f"/api/v1/evidence/{first_evidence}").json()["quote"]
        addition = {
            "kind": "mandatory",
            "statement": "人工补充质保适用范围",
            "conditions": [],
            "exceptions": [],
            "maximum": None,
            "bands": [],
            "scope": {"bid_id": bid, "description": "本项目"},
            "evidence_quotes": [{"evidence_id": first_evidence, "quote": quote}],
        }
        correction = {
            "base_rule_set_id": before_rules.json()["id"],
            "reason": "补充并确认有据的核心要求",
            "additions": [addition],
        }
        corrected = client.post(
            f"/api/v1/bids/{bid}/rule-set-corrections",
            json=correction,
            headers=headers(before_rules.headers["etag"]),
        )
        assert corrected.status_code == 201
        assert corrected.json()["id"] != before_rules.json()["id"]
        manual = next(
            item for item in corrected.json()["rules"] if item["origin_kind"] == "human_added"
        )
        assert (
            manual["confirmation"] == "confirmed"
            and manual["manual_origin_rule_id"] == manual["id"]
        )
        assert len(calls) == 4

        # 保存并确认事实一次原子提交，新集使旧轮过期；沿用原决定明确可追溯。
        empty_facts = client.get(f"/api/v1/bids/{bid}/facts")
        assert empty_facts.status_code == 200 and empty_facts.json()["facts"] == []
        fact_data = {
            "subject": "合成项目",
            "scope": "本项目招标要求",
            "name": "质保月数",
            "value": "24",
            "unit": "月",
            "evidence_ids": [first_evidence],
            "reason": "人工逐页核对质保月份",
        }
        fact_request = {"fact": fact_data, "replaces_fact_id": None}
        fact_headers = headers(empty_facts.headers["etag"])
        created_facts = client.post(
            f"/api/v1/bids/{bid}/facts", json=fact_request, headers=fact_headers
        )
        assert created_facts.status_code == 200
        assert created_facts.json()["facts"][0]["confirmation"] == "confirmed"
        original_fact = created_facts.json()["facts"][0]
        replay_facts = client.post(
            f"/api/v1/bids/{bid}/facts", json=fact_request, headers=fact_headers
        )
        assert replay_facts.status_code == 200 and replay_facts.json() == created_facts.json()
        second_facts = client.post(
            f"/api/v1/bids/{bid}/facts",
            json={"fact": fact_data | {"name": "另一条人工事实"}, "replaces_fact_id": None},
            headers=headers(created_facts.headers["etag"]),
        )
        assert second_facts.status_code == 200
        carried_fact = next(
            item for item in second_facts.json()["facts"] if item["name"] == fact_data["name"]
        )
        assert carried_fact["carryover"]["from_fact_id"] == original_fact["id"]
        assert (
            carried_fact["decision"]["object_id"] == original_fact["id"]
            and carried_fact["id"] != original_fact["id"]
        )
        second_fact = next(
            item for item in second_facts.json()["facts"] if item["name"] != fact_data["name"]
        )
        withdrawn = client.post(
            f"/api/v1/facts/{second_fact['id']}/withdrawals",
            json={"reason": "撤回合成重复项目"},
            headers=headers(second_facts.headers["etag"]),
        )
        assert withdrawn.status_code == 200 and len(withdrawn.json()["facts"]) == 1
        assert (
            not client.get(f"/api/v1/reviews/{new_review_id}").json()["current"] and len(calls) == 4
        )
        active_fact_id = withdrawn.json()["facts"][0]["id"]

        # 仅本合成项目撤回祖先，原页仍能查看，写作引用投影必须拒绝且不创建citation。
        with connection() as c:
            c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
            c.execute("SELECT pg_advisory_xact_lock(90127,1)")
            c.execute(
                "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id IN(SELECT parent_id FROM retrieval.lineage_edges WHERE child_id=%s)",
                (authoring.json()["authoring_reference"]["source_id"],),
            )
            before_citations = c.execute(
                "SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s", (bid,)
            ).fetchone()["n"]
        unavailable = client.get(f"/api/v1/evidence/{first_evidence}/authoring-reference")
        assert unavailable.status_code == 200
        assert unavailable.json()["authoring_reference"] is None and unavailable.json()["reasons"]
        assert unavailable.headers["etag"] != authoring.headers["etag"]
        with connection() as c:
            assert (
                c.execute(
                    "SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s", (bid,)
                ).fetchone()["n"]
                == before_citations
            )

        historical_comparison = client.get(f"/api/v1/comparisons/{comparison_id}")
        assert historical_comparison.status_code == 200
        assert historical_comparison.json()["result"] == result
        assert not historical_comparison.json()["current_context_matches"]

        changed_fact = client.get(f"/api/v1/facts/{active_fact_id}")
        assert (
            changed_fact.status_code == 200
            and changed_fact.json()["source_validity"] == "needs_revalidation"
        )
        assert (
            changed_fact.json()["confirmation"] == "pending"
            and changed_fact.json()["decision"]["decision"] == "confirmed"
        )
