"""独立非owner真实PG＋真实会话/CSRF；仅显式合成资料，无外部模型请求。"""

import os
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from apps.api.auth import csrf
from apps.api.research_routes import router
from packages.domain import storage
from packages.domain.runtime import DomainError, connection, required, secret_hash, sha, uid
from packages.research import batches, execution, ledger
from packages.research.core import judge_three
from packages.research.model_port import ControlledJudgmentPort
from packages.research.profile import digest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RESEARCH_INTEGRATION") != "1",
    reason="需要独立真实PG研究机制库；不使用owner跑业务",
)


@pytest.fixture
def client(monkeypatch):
    objects = {}
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", lambda key: objects[key])
    monkeypatch.setenv("RESEARCH_RPM", "1000")
    monkeypatch.setenv("RESEARCH_TPM", "100000000")
    monkeypatch.setenv(
        "RESEARCH_PRICE_CONFIG",
        '{"profile_digest":"'
        + digest()
        + '","currency":"USD","input_per_million":"0.30","output_per_million":"1.20","max_output_tokens":4096,"context_tokens":1000000}',
    )
    actor, session, token = uid(), uid(), uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO members(id,display_name) VALUES(%s,'合成研究机制测试成员')", (actor,)
        )
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
            (session, actor, secret_hash(token), sha(csrf(token).encode())),
        )
        who = c.execute(
            "SELECT r.rolsuper,r.oid FROM pg_roles r WHERE rolname=current_user"
        ).fetchone()
        assert not who["rolsuper"]
        assert not c.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace='design'::regnamespace AND relowner=%s) AS yes",
            (who["oid"],),
        ).fetchone()["yes"]
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse(
            {"code": error.code, "message": error.message, "details": error.details},
            status_code=error.status,
        )

    with TestClient(app) as web:
        web.cookies.set("session", token)
        yield (
            web,
            actor,
            lambda tag=None: {
                "Origin": required("APP_ORIGIN"),
                "X-CSRF-Token": csrf(token),
                "Idempotency-Key": uuid4().hex,
            }
            | ({"If-Match": tag} if tag else {}),
            objects,
        )


def seed(actor, *, year=2024):
    """只构造offline_fact合成提交，不声称标准Gate授权或真实解析效果。"""
    bid, project, round_id, bidder = uid(), uid(), uid(), uid()
    material, body, source, tender = uid(), uid(), uid(), uid()
    rule_set, rule, command, operation, review, input_id, submission = [uid() for _ in range(7)]
    obtained = datetime(year, 2, 1, tzinfo=timezone.utc)
    submitted = datetime(year, 2, 2, tzinfo=timezone.utc)
    known = datetime(year, 2, 5, tzinfo=timezone.utc)
    with connection() as c:
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,'{}',%s)",
            (bid, "合成历史研究样本" + str(bid), actor),
        )
        c.execute("INSERT INTO engineering_projects(id,title) VALUES(%s,'合成项目')", (project,))
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,lot,round_no) VALUES(%s,%s,'合成标段',1)",
            (round_id, project),
        )
        c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成公司')", (bidder,))
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'submitted')",
            (bid, round_id, bidder),
        )
        evidence = {}
        parses = {}
        for version, kind, text in [
            (body, "bid_body", "合成围挡及交通组织技术正文"),
            (source, "source_original", "合成正式技术成绩0分 满分60分"),
            (tender, "source_original", "合成评分规则：交通组织方案60分"),
        ]:
            blob, parse, block = uid(), uid(), uid()
            c.execute(
                "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
                (blob, sha(str(version)), "synthetic-research/" + str(blob)),
            )
            c.execute(
                "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成测试.pdf',%s,%s,%s)",
                (version, bid, blob, obtained, kind, actor),
            )
            c.execute(
                "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
                (parse, bid, version, sha("explicit-synthetic-parser"), sha("synthetic")),
            )
            c.execute(
                "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,100,100,0,'usable','[]')",
                (bid, parse),
            )
            c.execute(
                "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",
                (block, bid, parse, text, sha(text.encode())),
            )
            evidence[version] = block
            parses[version] = parse
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (material, bid, sha(str(material))),
        )
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0)",
            (material, bid, tender),
        )
        from packages.rag.indexing import ensure_parse_sources

        for version in (body, tender):
            ensure_parse_sources(c, bid, parses[version], publish_readable=True)
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (rule_set, bid, material, sha(str(rule_set))),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands) VALUES(%s,%s,%s,'scoring','交通组织方案','[]','[]',60,%s,%s)",
            (
                rule,
                bid,
                rule_set,
                [evidence[tender]],
                Jsonb(
                    [
                        {
                            "label": "明确",
                            "description": "合成档位",
                            "points": "60",
                            "evidence_ids": [str(evidence[tender])],
                        }
                    ]
                ),
            ),
        )
        c.execute(
            "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'synthetic-research-fixture',%s,%s,%s)",
            (command, actor, bid, uuid4().hex, sha("fixture")),
        )
        c.execute(
            "INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) VALUES(%s,%s,%s,'synthetic-review',%s,'succeeded','synthetic/1',now())",
            (operation, bid, command, sha(str(review))),
        )
        manifest = {"rule_set_id": str(rule_set)}
        c.execute(
            "INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at,evidence_manifest_blob_id,manifest_digest,as_of,evaluation_context_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,now(),%s,%s,%s,%s)",
            (
                input_id,
                bid,
                material,
                body,
                sha(str(input_id)),
                digest(),
                Jsonb(manifest),
                fetch_blob(c, body),
                sha(manifest),
                obtained,
                sha("context"),
            ),
        )
        c.execute(
            "INSERT INTO review_runs(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (review, bid, input_id, operation),
        )
        c.execute(
            "INSERT INTO submission_records(id,bid_id,body_version_id,review_id,kind,evidence,submitted_at,created_by) VALUES(%s,%s,%s,%s,'offline_fact',%s,%s,%s)",
            (submission, bid, body, review, Jsonb({"synthetic": True}), submitted, actor),
        )
        c.execute(
            "INSERT INTO submission_heads(bid_id,submission_id,revision) VALUES(%s,%s,1)",
            (bid, submission),
        )
    return {
        "bid": str(bid),
        "body": str(body),
        "source": str(source),
        "submission": str(submission),
        "known": known.isoformat(),
        "review": str(review),
        "material": str(material),
        "identity": {
            "project_id": str(project),
            "round_id": str(round_id),
            "bidder_id": str(bidder),
            "body_version_id": str(body),
        },
        "evidence": str(evidence[source]),
        "body_parse_id": str(parses[body]),
        "tender": str(tender),
    }


def fetch_blob(c, version):
    return c.execute("SELECT blob_id FROM document_versions WHERE id=%s", (version,)).fetchone()[
        "blob_id"
    ]


def register(web, headers, item, kind="technical_total", value="0"):
    body = {
        "submission_id": item["submission"],
        "score_kind": kind,
        "value": value,
        "maximum": "60" if value is not None else None,
        "source_version_id": item["source"],
        "known_at": item["known"],
    }
    result = web.post(f"/api/v1/bids/{item['bid']}/outcomes", json=body, headers=headers())
    assert result.status_code == 201, result.text
    return result, body


def verify(web, headers, item, response):
    body = {
        "state": "verified",
        "reason": "合成测试核对身份与成绩原页",
        "identity": item["identity"],
        "aggregation": "官方公布技术总分",
        "published_at": item["known"],
        "evidence_ids": [item["evidence"]],
        "material_snapshot_id": item["material"],
        "effective_material_confirmed": True,
    }
    result = web.post(
        "/api/v1/outcomes/" + response.json()["id"] + "/verification",
        json=body,
        headers=headers(response.headers["Verification-ETag"]),
    )
    assert result.status_code == 200, result.text
    return result


def admission(web, headers, item):
    result = web.post(
        f"/api/v1/bids/{item['bid']}/submissions/{item['submission']}/sample-admission",
        json={},
        headers=headers(),
    )
    assert result.status_code == 200, result.text
    sample = result.json()
    grouping = web.get("/api/v1/research/samples/" + sample["id"] + "/grouping")
    marked = web.post(
        "/api/v1/research/samples/" + sample["id"] + "/grouping",
        json={
            "template_key": None,
            "near_duplicate_key": None,
            "reviewed": True,
            "reason": "显式合成夹具，独立生成的正文并已检查模板/近重复",
        },
        headers=headers(grouping.headers["ETag"]),
    )
    assert marked.status_code == 200, marked.text
    return sample


def test_official_zero_verified_admitted_and_correction_never_falls_back(client):
    web, actor, headers, _ = client
    item = seed(actor)
    registered, body = register(web, headers, item)
    assert admission(web, headers, item)["admission"] == "pending"
    verify(web, headers, item, registered)
    first = admission(web, headers, item)
    assert first["admission"] == "admitted"
    assert first["id"] == admission(web, headers, item)["id"]
    change = web.post(
        "/api/v1/outcomes/" + registered.json()["id"] + "/corrections",
        json=body | {"score_kind": "unknown", "value": None, "maximum": None},
        headers=headers(registered.headers["ETag"]),
    )
    assert change.status_code == 201, change.text
    assert change.json()["supersedes_id"] == registered.json()["id"]
    assert admission(web, headers, item)["admission"] == "pending"
    old = web.get("/api/v1/outcomes/" + registered.json()["id"])
    assert old.json()["current"] is False
    caps = web.get("/api/v1/research/capabilities").json()
    assert caps["formal_prediction"]["mode"] == "unavailable"


def test_comprehensive_and_unknown_cannot_be_technical_labels_and_csrf_required(client):
    web, actor, headers, _ = client
    for kind, value in [("comprehensive_total", "50"), ("unknown", None)]:
        item = seed(actor)
        response, _ = register(web, headers, item, kind, value)
        verify(web, headers, item, response)
        sample = admission(web, headers, item)
        assert (
            sample["admission"] == "pending" and "no_official_technical_label" in sample["reasons"]
        )
    assert web.post("/api/v1/research/runs", json={}).status_code == 403


def test_real_pg_frozen_manifest_preview_run_budget_and_unknown_billing(client):
    web, actor, headers, objects = client
    samples, chosen = [], []
    for year in (2024, 2025):
        item = seed(actor, year=year)
        response, _ = register(web, headers, item)
        verify(web, headers, item, response)
        sample = admission(web, headers, item)
        chosen.append(item)
        samples.append(sample["id"])
        prepared = web.post(
            f"/api/v1/research/reviews/{item['review']}/chapter-packages",
            json={},
            headers=headers(),
        )
        assert prepared.status_code == 201, prepared.text
    frozen = web.post(
        "/api/v1/research/datasets",
        json={
            "sample_ids": samples,
            "split": {samples[0]: "train", samples[1]: "test"},
            "cutoff": "2025-01-01T00:00:00Z",
        },
        headers=headers(),
    )
    assert frozen.status_code == 201, frozen.text
    data = frozen.json()
    request = {
        "dataset_manifest_blob_id": data["dataset_manifest_blob_id"],
        "split_manifest_blob_id": data["split_manifest_blob_id"],
        "protocol_digest": sha("synthetic-protocol"),
        "method_digest": digest(),
        "profile_digest": digest(),
        "sample_ids": samples,
        "repeat_count": 1,
        "concurrency": 1,
        "fee_limit": "10",
        "currency": "USD",
    }
    preview = web.post("/api/v1/research/previews", json=request, headers=headers())
    assert preview.status_code == 201, preview.text
    assert preview.json()["estimated_calls"] == 6
    p = preview.json()
    start = web.post(
        "/api/v1/research/runs",
        json={
            "preview_id": p["id"],
            "manifest_digest": p["manifest_digest"],
            "price_config_digest": p["price_config_digest"],
        },
        headers=headers(),
    )
    assert start.status_code == 202, start.text
    run = start.json()
    assert run["total"] == 2 and run["pending"] == 2
    message = execution.schedule_one(run["id"])
    lease = execution.acquire(message)
    call = ledger.reserve(
        run_id=run["id"],
        message=message,
        lease=lease,
        logical_key="synthetic:judgment:1",
        request_digest=sha("clean-input"),
        profile_digest=digest(),
        reserved_fee="0.5",
    )
    again = ledger.reserve(
        run_id=run["id"],
        message=message,
        lease=lease,
        logical_key="synthetic:judgment:1",
        request_digest=sha("clean-input"),
        profile_digest=digest(),
        reserved_fee="0.5",
    )
    assert (
        call["new_reservation"]
        and not again["new_reservation"]
        and call["call_id"] == again["call_id"]
    )
    with pytest.raises(DomainError):
        ledger.reserve(
            run_id=run["id"],
            message=message,
            lease=lease,
            logical_key="over-budget",
            request_digest=sha("clean-input"),
            profile_digest=digest(),
            reserved_fee="20",
        )
    ledger.settle(call_id=call["call_id"], state="unknown")
    result = web.get("/api/v1/research/runs/" + run["id"])
    assert result.json()["state"] == "paused" and result.json()["reserved_fee"] == "0.5"
    resume = web.post(
        "/api/v1/research/runs/" + run["id"] + "/resume",
        json={"retry_failed": True, "reason": "合成恢复反例"},
        headers=headers(result.headers["ETag"]),
    )
    assert resume.status_code == 409 and resume.json()["code"] == "RESEARCH_BILLING_UNKNOWN"
    # 仅本合成请求确认未发送，不进行供应商请求或真实账单声明。
    ledger.settle(
        call_id=call["call_id"], state="failed", billed_fee="0", fee_basis="confirmed_not_sent"
    )
    execution.finish(message, lease, error_code="SYNTHETIC_NOT_SENT")
    with ledger.research_connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls WHERE research_run_id=%s", (run["id"],)
            ).fetchone()["n"]
            == 1
        )
    assert objects


def test_manifest_byte_tampering_is_not_trusted(client):
    _, _, _, objects = client
    from packages.research.artifacts import commit_stage, stage_json

    staged = stage_json({"schema": "synthetic"}, batches.DATASET_MEDIA)
    with connection() as c:
        identity = commit_stage(c, staged)
        row = batches.manifest_metadata(c, identity, batches.DATASET_MEDIA)
    objects[row["object_key"]] = b'{"official_score":60}'
    with pytest.raises(DomainError, match="摘要"):
        batches.read_manifest(row)


def create_run(client):
    web, actor, headers, _ = client
    samples = []
    for year in (2024, 2025):
        item = seed(actor, year=year)
        response, _ = register(web, headers, item)
        verify(web, headers, item, response)
        samples.append(admission(web, headers, item)["id"])
        prepared = web.post(
            f"/api/v1/research/reviews/{item['review']}/chapter-packages",
            json={},
            headers=headers(),
        )
        assert prepared.status_code == 201, prepared.text
    frozen = web.post(
        "/api/v1/research/datasets",
        json={
            "sample_ids": samples,
            "split": {samples[0]: "train", samples[1]: "test"},
            "cutoff": "2025-01-01T00:00:00Z",
        },
        headers=headers(),
    ).json()
    preview = web.post(
        "/api/v1/research/previews",
        json={
            "dataset_manifest_blob_id": frozen["dataset_manifest_blob_id"],
            "split_manifest_blob_id": frozen["split_manifest_blob_id"],
            "protocol_digest": sha("synthetic-protocol"),
            "method_digest": digest(),
            "profile_digest": digest(),
            "sample_ids": samples,
            "repeat_count": 1,
            "concurrency": 1,
            "fee_limit": "10",
            "currency": "USD",
        },
        headers=headers(),
    )
    assert preview.status_code == 201, preview.text
    p = preview.json()
    response = web.post(
        "/api/v1/research/runs",
        json={
            "preview_id": p["id"],
            "manifest_digest": p["manifest_digest"],
            "price_config_digest": p["price_config_digest"],
        },
        headers=headers(),
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_controlled_port_three_real_ledger_records_no_paid_transport(client):
    import json

    web, _, headers, objects = client
    run = create_run(client)
    message = execution.schedule_one(run["id"])
    lease = execution.acquire(message)
    with connection() as c:
        sample = c.execute(
            "SELECT sr.review_id FROM samples s JOIN submission_records sr ON sr.id=s.submission_id WHERE s.id=%s",
            (lease["sample_id"],),
        ).fetchone()
        original = c.execute(
            "SELECT b.* FROM chapter_packages cp JOIN blobs b ON b.id=cp.manifest_blob_id WHERE cp.review_id=%s",
            (sample["review_id"],),
        ).fetchone()
    package = json.loads(objects[original["object_key"]])

    requests = []
    transport = synthetic_native_transport(requests)
    port = ControlledJudgmentPort(
        run_id=run["id"], message=message, lease=lease, transport=transport
    )
    result = judge_three(package, port)
    assert len(requests) == 3 and result["valid_replicates"] == 3
    assert all(request["messages"] == requests[0]["messages"] for request in requests)
    replay = judge_three(package, port)
    assert replay == result and len(requests) == 3
    finish = execution.finish(message, lease, result=result)
    assert finish["state"] == "succeeded"
    with ledger.research_connection() as c:
        calls = c.execute(
            "SELECT * FROM model_calls WHERE research_run_id=%s", (run["id"],)
        ).fetchall()
        assert len(calls) == 3 and all(call["state"] == "succeeded" for call in calls)
        assert all(
            call["usage"]["settlement"]["fee_basis"] == "usage_price_estimate" for call in calls
        )
        remaining = c.execute("SELECT * FROM research_runs WHERE id=%s", (run["id"],)).fetchone()
        assert remaining["reserved_fee"] == 0 and remaining["spent_fee"] > 0
    # 第二项失败后恢复，只追加失败项尝试；第一项成功永不重新运行。
    two = execution.schedule_one(run["id"])
    second_lease = execution.acquire(two)
    execution.finish(two, second_lease, error_code="SYNTHETIC_TRANSIENT")
    failed = web.get("/api/v1/research/runs/" + run["id"])
    assert failed.json()["state"] == "failed"
    resume = web.post(
        "/api/v1/research/runs/" + run["id"] + "/resume",
        json={"retry_failed": True, "reason": "合成仅恢复失败样本"},
        headers=headers(failed.headers["ETag"]),
    )
    assert resume.status_code == 200, resume.text
    three = execution.schedule_one(run["id"])
    third_lease = execution.acquire(three)
    assert (
        third_lease["sample_id"] == second_lease["sample_id"]
        and third_lease["sample_id"] != lease["sample_id"]
    )
    execution.finish(three, third_lease, error_code="SYNTHETIC_STILL_UNAVAILABLE")


def synthetic_native_transport(requests, *, timeout=False):
    import json

    import httpx

    from packages.domain.providers.deepseek import DeepSeekClient

    def handler(request):
        arguments = json.loads(request.content)
        requests.append(arguments)
        if timeout:
            raise httpx.ReadTimeout("explicit-synthetic-timeout")
        frozen = json.loads(arguments["messages"][-1]["content"])
        evidence = frozen["sections"][0]["blocks"][0]
        value = {
            "band_id": frozen["rule"]["bands"][0]["id"],
            "reason": "显式MockTransport合成完整章节判断",
            "evidence": [{"evidence_id": evidence["evidence_id"], "quote": evidence["text"]}],
            "coverage": "complete",
            "status": "valid",
        }
        return httpx.Response(
            200,
            headers={"x-request-id": "synthetic-request-" + str(len(requests))},
            json={
                "id": "synthetic-response-" + str(len(requests)),
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": json.dumps(value)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )

    return DeepSeekClient(
        api_key="explicit-synthetic-never-external", transport=httpx.MockTransport(handler)
    )


def test_real_research_process_uses_clean_full_body_packages_and_three_mock_calls(client):
    from packages.research.worker import process

    web, _, _, _ = client
    run = create_run(client)
    message = execution.schedule_one(run["id"])
    requests = []
    result = process(message, synthetic_native_transport(requests))
    assert result["state"] == "succeeded" and len(requests) == 3
    assert all(
        '"label"' not in request["messages"][-1]["content"]
        and '"technical_total"' not in request["messages"][-1]["content"]
        and '"official_score"' not in request["messages"][-1]["content"]
        for request in requests
    )
    assert all(request["thinking"] == {"type": "disabled"} for request in requests)
    state = web.get("/api/v1/research/runs/" + run["id"]).json()
    assert state["succeeded"] == 1 and state["pending"] == 1


def test_mock_timeout_remains_unknown_and_never_resends(client):
    from packages.research.worker import process

    web, _, headers, _ = client
    run = create_run(client)
    message = execution.schedule_one(run["id"])
    requests = []
    transport = synthetic_native_transport(requests, timeout=True)
    failed = process(message, transport)
    assert failed["state"] == "failed" and len(requests) == 1
    assert process(message, transport) is None and len(requests) == 1
    state = web.get("/api/v1/research/runs/" + run["id"])
    assert state.json()["state"] == "paused" and state.json()["reserved_fee"] != "0"
    retry = web.post(
        "/api/v1/research/runs/" + run["id"] + "/resume",
        json={"retry_failed": True, "reason": "合成超时不补发"},
        headers=headers(state.headers["ETag"]),
    )
    assert retry.status_code == 409 and retry.json()["code"] == "RESEARCH_BILLING_UNKNOWN"


def test_research_role_cannot_read_labels_and_stale_execution_cannot_reserve(client):
    import psycopg

    run = create_run(client)
    message = execution.schedule_one(run["id"])
    lease = execution.acquire(message)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), ledger.research_connection() as c:
        c.execute("SELECT * FROM outcomes LIMIT 1")
    for changed, changed_lease in [
        (message | {"generation": 2}, lease),
        (message, lease | {"fence": 2}),
        (message, lease | {"owner": uid()}),
    ]:
        with pytest.raises(DomainError):
            ledger.reserve(
                run_id=run["id"],
                message=changed,
                lease=changed_lease,
                logical_key="stale",
                request_digest=sha("input"),
                profile_digest=digest(),
                reserved_fee="0.1",
            )
    with ledger.research_connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls WHERE research_run_id=%s", (run["id"],)
            ).fetchone()["n"]
            == 0
        )
    execution.finish(message, lease, error_code="SYNTHETIC_CLEANUP")


def test_historical_import_dry_run_exceptions_confirmation_and_same_bytes_reuse(client):
    web, actor, headers, _ = client
    item = seed(actor)
    record = {
        "external_record_id": "case-1",
        "bid_id": item["bid"],
        "actual_document_version_id": item["body"],
        "submitted_at": "2024-02-02T00:00:00Z",
        "evidence_ref": item["source"],
        "explanation": "合成线下实际提交资料，未经过系统完整检查",
        "score_kind": "technical_total",
        "value": "0",
        "maximum": "60",
        "source_version_id": item["source"],
        "known_at": item["known"],
    }
    preview = web.post(
        "/api/v1/research/import-previews",
        json={
            "records": [
                record,
                record
                | {"external_record_id": "case-bad", "actual_document_version_id": str(uid())},
                record | {"external_record_id": "case-duplicate"},
            ]
        },
        headers=headers(),
    )
    assert preview.status_code == 201, preview.text
    assert preview.json()["mode"] == "dry_run" and preview.json()["counts"] == {
        "total": 3,
        "pending": 1,
        "imported": 0,
        "errors": 2,
    }
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM outcomes WHERE bid_id=%s", (item["bid"],)
            ).fetchone()["n"]
            == 0
        )
    confirmed = web.post(
        "/api/v1/research/imports/" + preview.json()["id"] + "/confirm",
        json={},
        headers=headers(preview.headers["ETag"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["counts"]["imported"] == 1 and confirmed.json()["counts"]["errors"] == 2
    assert confirmed.json()["standard_submission_gate_checked"] is False
    # 新document_version身份、同原件字节，不再制造另一份实交/成绩/样本。
    new_body = uid()
    with connection() as c:
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) SELECT %s,bid_id,blob_id,'同字节再次上传.pdf',obtained_at,artifact_kind,created_by FROM document_versions WHERE id=%s",
            (new_body, item["body"]),
        )
    second = web.post(
        "/api/v1/research/import-previews",
        json={
            "records": [
                record
                | {
                    "external_record_id": "case-again",
                    "actual_document_version_id": str(new_body),
                    "value": "0.0",
                    "maximum": "60.00",
                }
            ]
        },
        headers=headers(),
    )
    assert (
        second.status_code == 201
        and "already_imported_will_reuse" in second.json()["records"][0]["errors"]
    )
    twice = web.post(
        "/api/v1/research/imports/" + second.json()["id"] + "/confirm",
        json={},
        headers=headers(second.headers["ETag"]),
    )
    assert twice.status_code == 200 and twice.json()["counts"]["imported"] == 1
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM submission_records WHERE bid_id=%s", (item["bid"],)
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM outcomes WHERE bid_id=%s", (item["bid"],)
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM samples WHERE bid_id=%s", (item["bid"],)
            ).fetchone()["n"]
            == 0
        )


def test_grouping_changes_pause_frozen_run_and_invalidate_split(client):
    web, _, headers, _ = client
    run = create_run(client)
    items = web.get("/api/v1/research/runs/" + run["id"] + "/items").json()["items"]
    for item in items:
        path = "/api/v1/research/samples/" + item["sample_id"] + "/grouping"
        old = web.get(path)
        result = web.post(
            path,
            json={
                "template_key": "synthetic-same-template-" + run["id"],
                "near_duplicate_key": None,
                "reviewed": True,
                "reason": "合成反例：发现同模板不得跨训练测试",
            },
            headers=headers(old.headers["ETag"]),
        )
        assert result.status_code == 200, result.text
    paused = web.get("/api/v1/research/runs/" + run["id"])
    assert paused.json()["state"] == "paused"
    resumed = web.post(
        "/api/v1/research/runs/" + run["id"] + "/resume",
        json={"retry_failed": True, "reason": "合成划分泄漏必须拒绝"},
        headers=headers(paused.headers["ETag"]),
    )
    assert resumed.status_code == 422 and resumed.json()["code"] == "RELATED_GROUP_LEAK"
    assert execution.schedule_one(run["id"]) is None


def test_actual_usage_estimate_over_budget_pauses_and_settlement_is_idempotent(client):
    web, _, _, _ = client
    run = create_run(client)
    message = execution.schedule_one(run["id"])
    lease = execution.acquire(message)
    call = ledger.reserve(
        run_id=run["id"],
        message=message,
        lease=lease,
        logical_key="synthetic-overage",
        request_digest=sha("input"),
        profile_digest=digest(),
        reserved_fee="0.1",
    )
    result = ledger.settle(
        call_id=call["call_id"],
        state="failed",
        billed_fee="20",
        fee_basis="usage_price_estimate",
        usage={"synthetic": True},
    )
    assert result["replayed"] is False
    duplicate = ledger.settle(
        call_id=call["call_id"],
        state="failed",
        billed_fee="20",
        fee_basis="usage_price_estimate",
        usage={"synthetic": True},
    )
    assert duplicate["replayed"] is True
    view = web.get("/api/v1/research/runs/" + run["id"]).json()
    assert view["state"] == "paused" and view["spent_fee"] == "20" and view["reserved_fee"] == "0.0"
    with pytest.raises(DomainError):
        ledger.reserve(
            run_id=run["id"],
            message=message,
            lease=lease,
            logical_key="after-overage",
            request_digest=sha("input"),
            profile_digest=digest(),
            reserved_fee="0.1",
        )
    execution.finish(message, lease, error_code="SYNTHETIC_OVERAGE")


def test_cancel_waits_for_execution_terminal_and_keeps_unknown_reserved_fee(client):
    from packages.research.worker import reconcile

    web, _, headers, _ = client
    run = create_run(client)
    message = execution.schedule_one(run["id"])
    lease = execution.acquire(message)
    call = ledger.reserve(
        run_id=run["id"],
        message=message,
        lease=lease,
        logical_key="synthetic-crash-after-reservation",
        request_digest=sha("input"),
        profile_digest=digest(),
        reserved_fee="0.1",
    )
    before = web.get("/api/v1/research/runs/" + run["id"])
    requested = web.post(
        "/api/v1/research/runs/" + run["id"] + "/cancel",
        json={"reason": "合成有在途请求的取消"},
        headers=headers(before.headers["ETag"]),
    )
    assert requested.status_code == 200 and requested.json()["cancel_requested"] is True
    assert requested.json()["state"] == "running" and requested.json()["pending"] == 1
    reconcile(once=True, run_id=run["id"])
    final = web.get("/api/v1/research/runs/" + run["id"]).json()
    assert (
        final["state"] == "cancelled" and final["pending"] == 0 and final["reserved_fee"] == "0.1"
    )
    with ledger.research_connection() as c:
        assert (
            c.execute("SELECT state FROM model_calls WHERE id=%s", (call["call_id"],)).fetchone()[
                "state"
            ]
            == "unknown"
        )
    with pytest.raises(DomainError, match="执行权已失效"):
        execution.finish(message, lease, result={"synthetic": "late"})


def test_outcome_context_prefills_identity_and_partial_parse_refuses_full_scope(client):
    web, actor, headers, _ = client
    item = seed(actor)
    context = web.get(
        f"/api/v1/bids/{item['bid']}/outcome-context", params={"submission_id": item["submission"]}
    )
    assert context.status_code == 200 and context.json()["identity"] == item["identity"]
    assert (
        context.json()["material_snapshot_id"] == item["material"]
        and context.json()["material_is_frozen"] is True
    )
    with connection() as c:
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'partial',%s)",
            (
                uid(),
                item["bid"],
                item["body"],
                sha("synthetic-parser-partial"),
                sha("synthetic-config"),
                Jsonb(["image_not_read"]),
            ),
        )
    rejected = web.post(
        f"/api/v1/research/reviews/{item['review']}/chapter-packages", json={}, headers=headers()
    )
    assert (
        rejected.status_code == 409
        and rejected.json()["code"] == "SCORING_FULL_CHAPTER_UNAVAILABLE"
    )
    assert rejected.json()["details"]["prediction_mode"] == "unavailable"


def operation_body(message):
    with connection() as c:
        return c.execute(
            "SELECT sr.bid_id,sr.body_version_id FROM research_run_attempts a JOIN samples s ON s.id=a.sample_id JOIN submission_records sr ON sr.id=s.submission_id WHERE a.operation_id=%s",
            (message["operation_id"],),
        ).fetchone()


def withdraw_original(bid, version):
    from packages.rag.indexing import original_source_id

    with connection() as c:
        c.execute("SET LOCAL lock_timeout='2s'")
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        changed = c.execute(
            "UPDATE retrieval.source_heads h SET status='withdrawn',revision=revision+1 FROM retrieval.source_nodes n JOIN document_versions d ON d.id=n.document_version_id WHERE h.source_id=n.id AND d.bid_id=%s AND n.id=%s",
            (bid, original_source_id(version)),
        ).rowcount
        assert changed == 1


def test_withdrawn_original_prevents_any_new_mock_model_request(client):
    from packages.research.worker import process

    web, _, _, _ = client
    run = create_run(client)
    message = execution.schedule_one(run["id"])
    body = operation_body(message)
    withdraw_original(body["bid_id"], body["body_version_id"])
    requests = []
    result = process(message, synthetic_native_transport(requests))
    assert result["state"] == "failed" and requests == []
    view = web.get("/api/v1/research/runs/" + run["id"]).json()
    assert view["spent_fee"] == "0" and view["reserved_fee"] == "0"
    with ledger.research_connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls WHERE research_run_id=%s", (run["id"],)
            ).fetchone()["n"]
            == 0
        )


def test_no_idle_transaction_during_model_or_result_put_and_withdrawal_blocks_publish(
    client, monkeypatch
):
    from packages.research.worker import process

    web, _, _, _ = client
    run = create_run(client)
    message = execution.schedule_one(run["id"])
    body = operation_body(message)
    original_put = storage.put
    observed = []

    def no_idle_transaction():
        with ledger.research_connection() as c:
            count = c.execute(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE usename=current_user AND datname=current_database() AND pid<>pg_backend_pid() AND state='idle in transaction'"
            ).fetchone()["n"]
            assert count == 0
        observed.append("no_idle_transaction")

    def put(key, data, media):
        no_idle_transaction()
        original_put(key, data, media)
        if media == "application/vnd.tender.research-result+json":
            withdraw_original(body["bid_id"], body["body_version_id"])
            observed.append("withdrawn_before_publish")

    monkeypatch.setattr(storage, "put", put)
    requests = []
    native = synthetic_native_transport(requests)
    original_generate = native.generate

    def generate(**arguments):
        no_idle_transaction()
        return original_generate(**arguments)

    monkeypatch.setattr(native, "generate", generate)
    result = process(message, native)
    assert result["state"] == "failed" and len(requests) == 3
    assert observed.count("no_idle_transaction") >= 7 and "withdrawn_before_publish" in observed
    with ledger.research_connection() as c:
        item = c.execute(
            "SELECT * FROM research_run_items WHERE operation_id=%s", (message["operation_id"],)
        ).fetchone()
        assert (
            item["state"] == "failed"
            and item["result_blob_id"] is None
            and item["error_code"] == "RESEARCH_SOURCE_CHANGED"
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM model_calls WHERE operation_id=%s AND state='succeeded'",
                (message["operation_id"],),
            ).fetchone()["n"]
            == 3
        )
    assert (
        web.get("/api/v1/research/capabilities").json()["formal_prediction"]["mode"]
        == "unavailable"
    )


def test_research_authority_mapping_is_not_worker_readable_or_writable_and_rr_fails(client):
    import psycopg
    from psycopg.rows import dict_row

    from packages.research.authority import qualify

    run = create_run(client)
    message = execution.schedule_one(run["id"])
    lease = execution.acquire(message)
    for table in ("research_chapter_authorities", "research_preview_chapters"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), ledger.research_connection() as c:
            c.execute("SELECT * FROM " + table)
    with ledger.research_connection() as c:
        verdict = qualify(c, message, lease)
        assert set(verdict) == {"qualified", "authority_digest"}
        wrong = c.execute(
            "SELECT design.qualify_research_chapter(%s,%s,%s,%s,%s,%s,%s) AS value",
            (
                message["operation_id"],
                message["step_key"],
                message["input_digest"],
                message["generation"],
                lease["fence"],
                lease["owner"],
                uid(),
            ),
        ).fetchone()["value"]
        assert wrong["qualified"] is False and "authority_digest" not in wrong
    with psycopg.connect(required("RESEARCH_WORKER_DATABASE_DSN"), row_factory=dict_row) as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        denied = c.execute(
            "SELECT design.qualify_research_chapter(%s,%s,%s,%s,%s,%s,NULL) AS value",
            (
                message["operation_id"],
                message["step_key"],
                message["input_digest"],
                message["generation"],
                lease["fence"],
                lease["owner"],
            ),
        ).fetchone()["value"]
        assert denied == {"qualified": False, "reason": "fresh_read_committed_required"}
    execution.finish(message, lease, error_code="SYNTHETIC_CLEANUP")


def test_old_parse_source_cannot_authorize_new_parse_evidence(client):
    web, actor, headers, _ = client
    item = seed(actor)
    with connection() as c:
        parse, evidence = uid(), uid()
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
            (parse, item["bid"], item["body"], sha("synthetic-reparse"), sha("synthetic-config")),
        )
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,100,100,0,'usable','[]')",
            (item["bid"], parse),
        )
        c.execute(
            "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text','合成新解析证据',%s)",
            (evidence, item["bid"], parse, sha("合成新解析证据".encode())),
        )
    response = web.post(
        f"/api/v1/research/reviews/{item['review']}/chapter-packages", json={}, headers=headers()
    )
    assert response.status_code == 409 and response.json()["code"] == "RESEARCH_SOURCE_UNREGISTERED"


def test_unconfigured_authority_is_explicit_503_and_capability_false(client):
    from packages.research.authority import configured

    with connection() as c:
        if configured(c):
            pytest.skip("此反例专供未热补的旧基线机制库执行")
    web, actor, headers, _ = client
    item = seed(actor)
    caps = web.get("/api/v1/research/capabilities")
    assert caps.status_code == 200 and caps.json()["source_authority_configured"] is False
    rejected = web.post(
        f"/api/v1/research/reviews/{item['review']}/chapter-packages", json={}, headers=headers()
    )
    assert (
        rejected.status_code == 503
        and rejected.json()["code"] == "RESEARCH_AUTHORITY_NOT_CONFIGURED"
    )


def test_manifest_io_and_model_io_run_outside_api_and_research_transactions(client, monkeypatch):
    from packages.research.worker import process

    originals = {"get": storage.get, "put": storage.put}
    observed = {"get": 0, "put": 0}

    def check_transactions():
        for connect in (connection, ledger.research_connection):
            with connect() as c:
                n = c.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE usename=current_user AND datname=current_database() AND pid<>pg_backend_pid() AND state='idle in transaction'"
                ).fetchone()["n"]
                assert n == 0

    def get(key):
        check_transactions()
        observed["get"] += 1
        return originals["get"](key)

    def put(key, raw, media):
        check_transactions()
        observed["put"] += 1
        return originals["put"](key, raw, media)

    monkeypatch.setattr(storage, "get", get)
    monkeypatch.setattr(storage, "put", put)
    run = create_run(client)
    message = execution.schedule_one(run["id"])
    requests = []
    result = process(message, synthetic_native_transport(requests))
    assert result["state"] == "succeeded" and len(requests) == 3
    assert observed["get"] >= 6 and observed["put"] >= 6


def test_architect_original_low_token_config_now_rejected(client,monkeypatch):
    import json
    low={"profile_digest":digest(),"currency":"USD","input_per_million":"0.30","output_per_million":"1.20","max_output_tokens":1,"context_tokens":2}
    monkeypatch.setenv("RESEARCH_PRICE_CONFIG",json.dumps(low))
    with pytest.raises(DomainError) as error:batches.price_config()
    assert error.value.code=="RESEARCH_PRICE_INVALID"
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('candidate02-budget-regression.json').write_text(json.dumps({'original_config_rejected':True,'code':error.value.code,'paid_calls':0},indent=2)+'\n')


def test_architect_original_withdrawn_body_zero_new_calls(client):
    import json
    from packages.research.worker import process
    from packages.rag.indexing import ensure_parse_sources,withdraw_source
    run=create_run(client)
    message=execution.schedule_one(run['id'])
    with connection() as c:
        original=c.execute('SELECT pr.id,pr.bid_id FROM research_run_attempts a JOIN samples s ON s.id=a.sample_id JOIN submission_records sr ON sr.id=s.submission_id JOIN parse_runs pr ON pr.document_version_id=sr.body_version_id WHERE a.operation_id=%s',(message['operation_id'],)).fetchone()
    with connection(True) as c:
        root,child=ensure_parse_sources(c,original['bid_id'],original['id'],publish_readable=True)
        revision=c.execute('SELECT revision FROM retrieval.source_heads WHERE source_id=%s',(root,)).fetchone()['revision']
        withdraw_source(c,original['bid_id'],child,revision)
    requests=[]
    value=process(message,synthetic_native_transport(requests))
    assert value['state']=='failed' and requests==[]
    with ledger.research_connection() as c:
        calls=c.execute('SELECT count(*) AS n FROM model_calls WHERE research_run_id=%s',(run['id'],)).fetchone()['n']
        item=c.execute('SELECT state,result_blob_id FROM research_run_items WHERE operation_id=%s',(message['operation_id'],)).fetchone()
    assert calls==0 and item['result_blob_id'] is None
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('candidate02-withdrawal-regression.json').write_text(json.dumps({'original_counterexample_rejected':True,'new_model_requests_after_withdrawal':0,'calls':calls,'item_state':item['state'],'result_blob_id':None,'paid_calls':0},indent=2)+'\n')


def test_architect_new_admission_group_change_does_not_pause(client):
    import json
    from packages.research.worker import process
    web,actor,headers,_=client
    run=create_run(client)
    message=execution.schedule_one(run['id'])
    with connection() as c:
        original=c.execute('SELECT s.id,s.source_group_id,br.engineering_project_id FROM research_run_attempts a JOIN samples s ON s.id=a.sample_id JOIN bid_profiles bp ON bp.bid_id=s.bid_id JOIN bid_rounds br ON br.id=bp.round_id WHERE a.operation_id=%s',(message['operation_id'],)).fetchone()
    added=seed(actor,year=2025)
    with connection() as c:c.execute('UPDATE bid_rounds SET engineering_project_id=%s,round_no=2 WHERE id=%s',(original['engineering_project_id'],added['identity']['round_id']))
    added['identity']['project_id']=str(original['engineering_project_id'])
    registered,_=register(web,headers,added)
    verify(web,headers,added,registered)
    admitted=web.post(f"/api/v1/bids/{added['bid']}/submissions/{added['submission']}/sample-admission",json={},headers=headers())
    assert admitted.status_code==200,admitted.text
    with connection() as c:
        current=c.execute('SELECT source_group_id FROM samples WHERE id=%s',(original['id'],)).fetchone()['source_group_id']
        state=c.execute('SELECT state FROM research_runs WHERE id=%s',(run['id'],)).fetchone()['state']
    assert current!=original['source_group_id'] and state=='running'
    requests=[]
    result=process(message,synthetic_native_transport(requests))
    assert result['state']=='succeeded' and len(requests)==3
    Path(os.environ['ARCHITECT_RESEARCH_OUT']).joinpath('candidate02-admission-group-counterexample.json').write_text(json.dumps({'reproduced':True,'frozen_group_changed':True,'run_state_after_new_admission':state,'new_model_requests':len(requests),'result_state':result['state'],'paid_calls':0},indent=2)+'\n')
