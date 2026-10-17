"""Frozen A1.1 ASGI/PG/full-PDF mechanisms; explicit offline model and memory objects."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import apps.api.main as api_main
from apps.api.auth import password_hash
from apps.worker import engine
from packages.domain import model_calls, reviews, storage
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError, assert_role, connection, enqueue, required, sha


def pdf(pages):
    font = 3 + 2 * len(pages)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Kids ["
            + " ".join(str(3 + 2 * i) + " 0 R" for i in range(len(pages)))
            + "] /Count "
            + str(len(pages))
            + " >>"
        ).encode(),
    ]
    for i, text in enumerate(pages):
        content = (
            "BT /F1 12 Tf 45 760 Td ("
            + text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            + ") Tj ET"
        ).encode()
        objects.extend(
            [
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {4 + 2 * i} 0 R >>".encode(),
                f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
            ]
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(output)


def main():
    root = Path(os.environ["QA_A1_ROOT"])
    out = root / os.environ.get("QA_A1_EXECUTION_NAME", "execution")
    out.mkdir(exist_ok=False)
    overlay = root / "execution-inputs"
    assert Path(api_main.__file__).is_relative_to(overlay)
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
    result = dict(
        passed=False,
        checks=[],
        paid_generation_calls=0,
        kafka_consumers_started=0,
        scope="Frozen isolated runtime ASGI/actual restricted PG, actual TCP parser03 originals, direct single-bid controller/authoritative outbox handlers, offline MockTransport/in-memory storage; not Kafka or paid effect",
    )
    objects = {}
    calls = []
    desired = {"verdict": "violated"}
    marker = uuid4().hex[:8]
    stage = "setup"

    class PreparationComplete(Exception):
        pass

    def check(name, value, observation=None):
        result["checks"].append(dict(name=name, passed=bool(value), observation=observation))

    def model(request):
        content = json.loads(json.loads(request.content)["messages"][-1]["content"])
        calls.append(content)
        unknown_phase = os.environ.get("QA_A1_CONTROLLER_UNKNOWN")
        if (unknown_phase == "prepare" and "documents" in content) or (
            unknown_phase == "core" and "documents" not in content
        ):
            raise httpx.ReadTimeout("Controlled cross-command unknown", request=request)
        if os.environ.get("QA_A1_BOUNDARY_SCENARIO") == "unknown":
            raise httpx.ReadTimeout("Controlled unknown", request=request)
        if "documents" in content:
            blocks = [b for d in content["documents"] for b in d["evidence_blocks"]]
            additions = []
            for kind, fragment in [
                ("disqualification", "Construction period must be 90 days or bid is disqualified."),
                ("disqualification", "Unsigned bids are disqualified."),
                ("scoring", "Technical method maximum is 5 points."),
            ]:
                evidence = next(b for b in blocks if fragment in b["text_content"])
                additions.append(
                    dict(
                        kind=kind,
                        statement=fragment,
                        conditions=[],
                        exceptions=[],
                        scope=dict(
                            bid_id=content["bid_id"], description="Current synthetic project"
                        ),
                        maximum="5" if kind == "scoring" else None,
                        bands=[],
                        evidence_quotes=[dict(evidence_id=str(evidence["id"]), quote=fragment)],
                    )
                )
            generated = dict(
                rules=additions, ambiguities=[], read_evidence_ids=[str(b["id"]) for b in blocks]
            )
        else:
            blocks = content["full_body_blocks"]
            generated = dict(
                read_evidence_ids=[str(b["id"]) for b in blocks],
                judgments=[
                    dict(
                        rule_id=r["id"],
                        verdict=desired["verdict"],
                        statement="Offline controlled verdict for protocol validation",
                        body_quotes=[
                            dict(
                                evidence_id=str(blocks[-1]["id"]), quote=blocks[-1]["text_content"]
                            )
                        ],
                    )
                    for r in content["rules"]
                ],
            )
        if os.environ.get("QA_A1_BOUNDARY_SCENARIO") == "post-validation" and "rules" in generated:
            generated["rules"][0]["evidence_quotes"][0]["quote"] = "SYNTHETIC QUOTE NOT IN ORIGINAL"
        return httpx.Response(
            200,
            json=dict(
                id="offline-" + uuid4().hex,
                model="deepseek-flash",
                usage=dict(prompt_tokens=20, completion_tokens=30, total_tokens=50),
                choices=[
                    dict(
                        message=dict(role="assistant", content=json.dumps(generated)),
                        finish_reason="stop",
                    )
                ],
            ),
        )

    client_model = DeepSeekClient(
        api_key="synthetic-offline-only", transport=httpx.MockTransport(model)
    )
    parse_env = {}
    import shlex

    for line in Path("/workspace/tender-workbench/.local/parsing.env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            name, value = line.split("=", 1)
            parse_env[name] = shlex.split(value)[0]
    login = "a1-independent-" + uuid4().hex
    password = secrets.token_urlsafe(24)
    member = uuid4()
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'A1独立合成测试')", (member,))
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (member, login, password_hash(password)),
        )
    with (
        patch.object(DeepSeekClient, "from_env", return_value=client_model),
        patch.object(storage, "put", side_effect=lambda k, b, m: objects.__setitem__(k, b)),
        patch.object(storage, "get", side_effect=lambda k: objects[k]),
        TestClient(api_main.app) as client,
    ):
        try:
            origin = os.environ["APP_ORIGIN"]
            logged = client.post(
                "/api/v1/auth/login",
                json=dict(login_name=login, password=password),
                headers={"Origin": origin},
            )
            assert logged.status_code == 200
            token = logged.json()["csrf_token"]

            def headers(tag=None, key=None):
                return {
                    "Origin": origin,
                    "X-CSRF-Token": token,
                    "Idempotency-Key": key or uuid4().hex,
                } | ({"If-Match": tag} if tag else {})

            company = client.post('/api/v1/identity/bidders',json=dict(name='独立04合成主体-'+marker,reason='明确测试主体，不授资质'),headers=headers())
            assert company.status_code==201
            created = client.post(
                "/api/v1/bids", json=dict(title="独立A1冻结机制-" + marker,bidder_id=company.json()['bidder']['id']), headers=headers()
            )
            assert created.status_code == 201
            bid = created.json()["id"]
            result["bid_id"] = bid

            def seed_doc(role, lines):
                version, blob, parse = uuid4(), uuid4(), uuid4()
                data = pdf(lines)
                key = "independent-memory-originals/" + str(version)
                objects[key] = data
                (out / (str(version) + ".pdf")).write_bytes(data)
                with httpx.Client(trust_env=False, timeout=90) as parser:
                    parsed = parser.post(
                        "http://127.0.0.1:8812/parse",
                        content=data,
                        headers={
                            "Authorization": "Bearer " + parse_env["PARSING_RUNTIME_TOKEN"],
                            "Content-Type": "application/pdf",
                            "X-Source-Sha256": sha(data),
                        },
                    )
                result.setdefault("parser_setup_observations", []).append(
                    dict(role=role, document_version_id=str(version), status_code=parsed.status_code,
                         observed_parser_digest=parsed.json().get("parser_digest") if parsed.status_code == 200 else None)
                )
                assert parsed.status_code == 200
                p = parsed.json()
                if role=='body' and len(calls)==3:
                    # Controlled parser-output fixture: the real original still contains
                    # the final duration schedule, but it was not parsed/read into evidence.
                    p['coverage']='partial';p['gaps']=['NECESSARY_FINAL_DURATION_SCHEDULE_UNREAD_PHYSICAL_PAGE_3']
                    p['pages'][-1]['quality']='partial';p['pages'][-1]['gaps']=list(p['gaps']);p['pages'][-1]['blocks']=[]
                assert (
                    p["parser_digest"]
                    == "0578f501d377855240d5dae3e42cd295a43496cfc3bf28674e63a1e83ab49165"
                )
                with connection() as c:
                    c.execute(
                        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",
                        (blob, sha(data), key, len(data)),
                    )
                    c.execute(
                        "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now()-interval '1 hour',%s,%s)",
                        (
                            version,
                            bid,
                            blob,
                            role + ".pdf",
                            "bid_body" if role == "body" else "source_original",
                            member,
                        ),
                    )
                    c.execute(
                        "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            parse,
                            bid,
                            version,
                            p["parser_digest"],
                            p["config_digest"],
                            len(p["pages"]),
                            "complete" if p["coverage"] == "usable" else "partial",
                            Jsonb(p["gaps"]),
                        ),
                    )
                    for page in p["pages"]:
                        c.execute(
                            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                bid,
                                parse,
                                page["physical_page"],
                                page["width"],
                                page["height"],
                                page["rotation"],
                                page["quality"],
                                Jsonb(page["gaps"]),
                            ),
                        )
                        for i, b in enumerate(page["blocks"]):
                            c.execute(
                                "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,bbox,structure,digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                (
                                    uuid4(),
                                    bid,
                                    parse,
                                    page["physical_page"],
                                    i,
                                    b["kind"],
                                    b["text_content"],
                                    Jsonb(b["bbox"]),
                                    Jsonb(b.get("structure")),
                                    b["digest"],
                                ),
                            )
                return version, parse, p

            tender, _, _ = seed_doc(
                "tender",
                [
                    "Construction period must be 90 days or bid is disqualified.",
                    "Unsigned bids are disqualified.",
                    "Technical method maximum is 5 points. QA " + marker,
                    *(["A site visit is required."] if os.environ.get("QA_A1_HUMAN_API") else []),
                ],
            )
            body, body_parse, _ = seed_doc(
                "body",
                [
                    "Construction period will be 120 days.",
                    "",
                    "Unsigned bid. Final physical page. QA " + marker,
                ],
            )
            material_revision = 0

            def set_material(body_version, extra_versions=()):
                nonlocal material_revision
                material_revision += 1
                snapshot, command = uuid4(), uuid4()
                with connection() as c:
                    c.execute(
                        "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'qa-synthetic-material',%s,%s,%s)",
                        (command, member, bid, uuid4().hex, sha(str(snapshot))),
                    )
                    c.execute(
                        "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,%s,'{}',%s)",
                        (snapshot, bid, material_revision, sha(str(snapshot))),
                    )
                    for role, version in [
                        ("tender", tender),
                        ("body", body_version),
                        *extra_versions,
                    ]:
                        c.execute(
                            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,0)",
                            (snapshot, bid, version, role),
                        )
                    c.execute(
                        "UPDATE bid_profiles SET current_material_id=%s,current_body_id=%s WHERE bid_id=%s",
                        (snapshot, body_version, bid),
                    )
                    c.execute(
                        "UPDATE bids SET material_revision=%s,revision=revision+1 WHERE id=%s",
                        (material_revision, bid),
                    )
                    op = enqueue(c, UUID(bid), command, "parse-materials", {"id": str(snapshot)})
                    c.execute(
                        "UPDATE operations SET state='succeeded',finished_at=now() WHERE id=%s",
                        (op["id"],),
                    )
                return snapshot

            set_material(body)

            def state():
                response = client.get(f"/api/v1/bids/{bid}/review-state")
                assert response.status_code == 200
                return response

            def request(nonce=None):
                request_body = dict(
                    body_version_id=str(body), material_revision=material_revision, trigger="manual"
                )
                if nonce:
                    request_body["rerun_nonce"] = str(nonce)
                tag = client.get(f"/api/v1/bids/{bid}").headers["etag"]
                response = client.post(
                    f"/api/v1/bids/{bid}/reviews", json=request_body, headers=headers(tag)
                )
                assert response.status_code == 202
                return response

            def pump(operation):
                seen = set()
                for _ in range(8):
                    with connection(True) as c:
                        op = c.execute(
                            "SELECT state FROM operations WHERE id=%s", (operation,)
                        ).fetchone()
                        events = c.execute(
                            "SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.review.v1' ORDER BY available_at,id",
                            (operation,),
                        ).fetchall()
                    if op["state"] in ("succeeded", "failed", "cancelled"):
                        return op["state"]
                    for event in events:
                        message = event["payload"]
                        if message["event_id"] not in seen:
                            seen.add(message["event_id"])
                            engine.handle(message)
                return "not_terminal"

            def finish_request():
                for _ in range(6):
                    reviews.tick(UUID(bid))
                    s = state().json()
                    if s["request"].get("review_id"):
                        assert pump(s["current_review"]["operation_id"]) == "succeeded"
                        reviews.tick(UUID(bid))
                        return state().json()
                    assert pump(s["request"]["prepare_operation_id"]) == "succeeded"
                raise AssertionError("finite direct stages incomplete")

            stage = "durable_prepare_cancel"
            request()
            pending = state()
            check(
                "preparation_is_durable_and_not_a_review",
                pending.json()["request"]["state"] == "preparing"
                and pending.json()["current_review"] is None
                and state().json() == pending.json()
                and not calls,
            )
            waiting, released = threading.Event(), threading.Event()
            original = reviews.verify_bytes

            def pause_verify(documents):
                original(documents)
                waiting.set()
                assert released.wait(15)

            with (
                patch.object(reviews, "verify_bytes", side_effect=pause_verify),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(reviews.tick, UUID(bid))
                assert waiting.wait(10)
                latest = state()
                cancellation = client.post(
                    f"/api/v1/bids/{bid}/review-requests/{latest.json()['request']['request_id']}/cancel",
                    json=dict(reason="Independent cancellation", cancel_pending_same_input=True),
                    headers=headers(latest.headers["etag"]),
                )
                released.set()
                future.result(20)
            check(
                "cancel_during_original_validation_no_late_prepare",
                cancellation.status_code == 200
                and state().json()["request"]["state"] == "cancelled"
                and not calls,
            )
            if os.environ.get("QA_A1_BOUNDARY_SCENARIO"):
                from deploy import review_acceptance as tool

                scenario = os.environ["QA_A1_BOUNDARY_SCENARIO"]
                request()
                reviews.tick(UUID(bid))
                fixture_arguments = []
                other_command_message = None
                if os.environ.get("QA_GUARD_FIXTURE_SCOPE") == "1":
                    from packages.domain.review_data import frozen_documents, read_bundle
                    from packages.domain.runtime import clean

                    with connection(True) as c:
                        scoped_heads = c.execute(
                            "SELECT p.current_body_id,p.current_material_id,b.material_revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s", (bid,)
                        ).fetchone()
                        scoped_docs = frozen_documents(read_bundle(c, UUID(bid), scoped_heads["current_material_id"]))
                        preparation = c.execute("SELECT * FROM operations WHERE bid_id=%s AND handler_version='prepare/1' ORDER BY created_at DESC LIMIT 1", (bid,)).fetchone()
                        preparation_message = c.execute("SELECT payload FROM outbox WHERE operation_id=%s ORDER BY available_at,id LIMIT 1", (preparation["id"],)).fetchone()["payload"]
                    scoped_fixture = out / "approved-fixture.json"
                    scoped_fixture.write_text(json.dumps(clean(dict(bid_id=bid, heads=scoped_heads, documents=scoped_docs)), ensure_ascii=False, indent=2)+"\n")
                    fixture_arguments = [scoped_fixture, hashlib.sha256(scoped_fixture.read_bytes()).hexdigest(), preparation["command_id"]]
                    # A real same-bid other-command operation is offered by the fake
                    # consumer. It is never placed in a real Kafka topic or executed.
                    other_command, other_operation = uuid4(), uuid4()
                    with connection() as c:
                        c.execute("INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) SELECT %s,actor_id,'qa-other-command',target_id,%s,%s FROM commands WHERE id=%s", (other_command, str(other_command), sha(str(other_command).encode()), preparation["command_id"]))
                        c.execute("INSERT INTO operations(id,bid_id,command_id,kind,input_digest,state,handler_version,finished_at) VALUES(%s,%s,%s,'qa-other-command',%s,'succeeded','prepare/1',now())", (other_operation, bid, other_command, sha(str(other_operation).encode())))
                    other_command_message = dict(preparation_message, operation_id=str(other_operation), input_digest=sha(str(other_operation).encode()))
                foreign = uuid4()
                observations = {
                    "foreign_skipped": False,
                    "closed": False,
                    "consumer_initializations": 0,
                    "handled": [],
                    "commits": 0,
                }

                class FakeMessage:
                    def __init__(self, payload):
                        self.payload = payload

                    def error(self):
                        return None

                    def value(self):
                        return json.dumps(self.payload).encode()

                class OfflineConsumer:
                    def __init__(self, config):
                        observations["consumer_initializations"] += 1
                        observations["config"] = config
                        self.seen = set()
                        self.foreign = False
                        self.other_command = False

                    def subscribe(self, topics):
                        observations["topics"] = topics

                    def poll(self, seconds):
                        if not self.foreign:
                            self.foreign = True
                            return FakeMessage(
                                {"bid_id": str(foreign), "handler_version": "review/1"}
                            )
                        if other_command_message is not None and not self.other_command:
                            self.other_command = True
                            return FakeMessage(other_command_message)
                        with connection(True) as c:
                            events = c.execute(
                                "SELECT payload FROM outbox WHERE operation_id IN (SELECT id FROM operations WHERE bid_id=%s AND handler_version IN ('prepare/1','review/1')) ORDER BY available_at,id",
                                (bid,),
                            ).fetchall()
                        for event in events:
                            payload = event["payload"]
                            if payload["event_id"] not in self.seen:
                                self.seen.add(payload["event_id"])
                                if scenario == "cancel":
                                    with connection() as c:
                                        c.execute(
                                            "UPDATE operations SET state='cancel_requested',cancel_requested_at=now() WHERE id=%s",
                                            (payload["operation_id"],),
                                        )
                                return FakeMessage(payload)
                        return None

                    def close(self):
                        observations["closed"] = True

                    def commit(self, *args, **kwargs):
                        observations["commits"] += 1
                        raise AssertionError("Disposable tool must never commit offsets")

                def offline_handle(message):
                    assert message["bid_id"] == bid
                    if other_command_message is not None:
                        assert message["operation_id"] != other_command_message["operation_id"]
                    observations["handled"].append(message["handler_version"])
                    engine.handle(message)
                    if scenario == "success":
                        reviews.tick(UUID(bid))
                        reviews.tick(UUID(bid))

                guarded = tool._execute_locked(
                    UUID(bid),
                    Path(os.environ.get("QA_GUARD_CANDIDATE", str(root / "candidate-01.json"))),
                    *(
                        [os.environ["QA_GUARD_CANDIDATE_SHA256"]]
                        if os.environ.get("QA_GUARD_CANDIDATE_SHA256")
                        else []
                    ),
                    10,
                    OfflineConsumer,
                    offline_handle,
                    model_calls,
                    assert_role,
                    connection,
                    required,
                    *fixture_arguments,
                )
                with connection(True) as c:
                    ledger = c.execute(
                        "SELECT mc.state,mc.usage FROM model_calls mc JOIN operations o ON o.id=mc.operation_id WHERE o.bid_id=%s ORDER BY mc.created_at",
                        (bid,),
                    ).fetchall()
                    ops = c.execute(
                        "SELECT handler_version,state FROM operations WHERE bid_id=%s AND handler_version IN ('prepare/1','review/1') ORDER BY created_at",
                        (bid,),
                    ).fetchall()
                expected_status = (
                    "succeeded"
                    if scenario == "success"
                    else "stopped_model_failure_or_unknown"
                    if scenario == "unknown"
                    else "stopped_business_validation_or_cancel"
                )
                check(
                    "bounded_tool_stops_at_expected_state",
                    guarded["status"] == expected_status,
                    guarded,
                )
                check(
                    "foreign_messages_skipped_and_no_offset_commit",
                    bool(observations["handled"])
                    and observations["commits"] == 0
                    and observations["closed"]
                    and observations["config"]["group.id"].startswith("tender-acceptance-")
                    and observations["config"]["enable.auto.commit"] is False
                    and observations["config"]["enable.auto.offset.store"] is False,
                )
                if scenario == "post-validation":
                    check(
                        "schema_valid_response_but_business_quote_rejection_stops_immediately",
                        len(calls) == 1
                        and ledger[0]["state"] == "succeeded"
                        and ops[0]["state"] == "failed"
                        and len(observations["handled"]) == 1,
                        dict(ledger=ledger, operations=ops),
                    )
                elif scenario == "unknown":
                    check(
                        "unknown_preserved_single_invocation",
                        len(calls) == 1
                        and ledger[0]["state"] == "unknown"
                        and guarded["guarded_invocations"] == 1,
                    )
                elif scenario == "cancel":
                    check(
                        "cancellation_stops_without_transport",
                        len(calls) == 0 and not ledger and guarded["guarded_invocations"] == 0,
                    )
                elif scenario == "success":
                    check(
                        "ordinary_extract_core_finalize_chain_limited_to_two",
                        len(calls) == 2
                        and guarded["guarded_invocations"] == 2
                        and [r["state"] for r in ledger] == ["succeeded", "succeeded"],
                    )
                if ledger:
                    before = observations["consumer_initializations"]
                    try:
                        tool._execute_locked(
                            UUID(bid),
                            Path(
                                os.environ.get(
                                    "QA_GUARD_CANDIDATE", str(root / "candidate-01.json")
                                )
                            ),
                            *(
                                [os.environ["QA_GUARD_CANDIDATE_SHA256"]]
                                if os.environ.get("QA_GUARD_CANDIDATE_SHA256")
                                else []
                            ),
                            1,
                            OfflineConsumer,
                            offline_handle,
                            model_calls,
                            assert_role,
                            connection,
                            required,
                            *fixture_arguments,
                        )
                        rejected = False
                    except DomainError as error:
                        rejected = error.code == "ACCEPTANCE_PROJECT_NOT_FRESH"
                    check(
                        "any_prior_call_blocks_restart_before_consumer_init",
                        rejected and observations["consumer_initializations"] == before,
                    )
                result.update(
                    passed=all(c["passed"] for c in result["checks"]),
                    bounded_tool_result=guarded,
                    offline_consumer_observations=observations,
                    ledger=ledger,
                    operations=ops,
                    offline_transport_requests=len(calls),
                    tool_sha256=hashlib.sha256(Path(tool.__file__).read_bytes()).hexdigest(),
                )
                raise PreparationComplete
            if os.environ.get("QA_A1_ONLY_PREPARATION") == "1":
                stage = "prepare_concurrency_and_original_guard"
                settings = client.get(f"/api/v1/bids/{bid}/settings")
                changed = client.patch(
                    f"/api/v1/bids/{bid}/settings",
                    json={"auto_review": False},
                    headers=headers(settings.headers["etag"]),
                )
                assert changed.status_code == 200
                payload = dict(
                    body_version_id=str(body), material_revision=material_revision, trigger="manual"
                )
                no_condition = client.post(
                    f"/api/v1/bids/{bid}/reviews", json=payload, headers=headers()
                )
                check("missing_bid_cas_rejected", no_condition.status_code == 428)
                tag = client.get(f"/api/v1/bids/{bid}").headers["etag"]
                identity_headers = headers(tag)
                submitted = client.post(
                    f"/api/v1/bids/{bid}/reviews", json=payload, headers=identity_headers
                )
                assert submitted.status_code == 202
                stale = client.post(
                    f"/api/v1/bids/{bid}/reviews", json=payload, headers=headers(tag)
                )
                replay = client.post(
                    f"/api/v1/bids/{bid}/reviews", json=payload, headers=identity_headers
                )
                check(
                    "prepare_request_cas_and_exact_replay",
                    stale.status_code == 412
                    and replay.status_code == 202
                    and replay.headers.get("Idempotent-Replayed") == "true"
                    and replay.json() == submitted.json(),
                )
                set_material(body)
                reviews.tick(UUID(bid))
                check(
                    "auto_off_head_change_supersedes_without_model",
                    state().json()["request"]["state"] == "superseded" and not calls,
                )
                request()
                current = state()
                cancel_path = f"/api/v1/bids/{bid}/review-requests/{current.json()['request']['request_id']}/cancel"
                cancel_body = dict(
                    reason="Independent preparation cancellation", cancel_pending_same_input=True
                )
                missing = client.post(cancel_path, json=cancel_body, headers=headers())
                stale_cancel = client.post(
                    cancel_path, json=cancel_body, headers=headers('"stale"')
                )
                cancel_headers = headers(current.headers["etag"])
                cancelled = client.post(cancel_path, json=cancel_body, headers=cancel_headers)
                replay_cancel = client.post(cancel_path, json=cancel_body, headers=cancel_headers)
                changed_cancel = client.post(
                    cancel_path,
                    json=cancel_body | {"reason": "Changed input"},
                    headers=cancel_headers,
                )
                check(
                    "cancel_cas_and_identity_are_required",
                    missing.status_code == 428
                    and stale_cancel.status_code == 412
                    and cancelled.status_code == 200
                    and replay_cancel.status_code == 200
                    and replay_cancel.json() == cancelled.json()
                    and changed_cancel.status_code == 409,
                )
                request()
                with connection() as c:
                    original_key = c.execute(
                        "SELECT b.object_key FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",
                        (tender,),
                    ).fetchone()["object_key"]
                original_bytes = objects[original_key]
                objects[original_key] = b"corrupted same original locator"
                try:
                    reviews.tick(UUID(bid))
                    rejected = False
                except DomainError as error:
                    rejected = error.code == "ORIGINAL_BYTES_CORRUPTED"
                objects[original_key] = original_bytes
                with connection(True) as c:
                    model_count = c.execute(
                        "SELECT count(*) AS n FROM model_calls mc JOIN operations o ON o.id=mc.operation_id WHERE o.bid_id=%s",
                        (bid,),
                    ).fetchone()["n"]
                    prepare_count = c.execute(
                        "SELECT count(*) AS n FROM operations WHERE bid_id=%s AND kind IN ('prepare-review','review')",
                        (bid,),
                    ).fetchone()["n"]
                check(
                    "actual_original_sha_failure_blocks_before_any_model_reservation",
                    rejected and not calls and model_count == 0 and prepare_count == 0,
                )
                result.update(
                    passed=all(c["passed"] for c in result["checks"]),
                    offline_transport_requests=len(calls),
                    model_call_rows=model_count,
                )
                raise PreparationComplete
            if os.environ.get("QA_A1_CONTROLLER_UNKNOWN"):
                request()
                for _ in range(6):
                    reviews.tick(UUID(bid))
                    unknown_state = state().json()
                    operation = (
                        unknown_state["current_review"]["operation_id"]
                        if unknown_state["request"].get("review_id")
                        else unknown_state["request"]["prepare_operation_id"]
                    )
                    terminal = pump(operation)
                    if terminal == "failed":
                        break
                    assert terminal == "succeeded"
                assert terminal == "failed"
                with connection(True) as c:
                    before_operations = c.execute(
                        "SELECT count(*) AS n FROM operations WHERE bid_id=%s", (bid,)
                    ).fetchone()["n"]
                    ledger = c.execute(
                        "SELECT mc.state FROM model_calls mc JOIN operations o ON o.id=mc.operation_id WHERE o.bid_id=%s",
                        (bid,),
                    ).fetchall()
                transport_before = len(calls)
                check(
                    "unknown_call_record_exists_without_automatic_retry",
                    sum(row["state"] == "unknown" for row in ledger) == 1
                    and transport_before
                    == (1 if os.environ["QA_A1_CONTROLLER_UNKNOWN"] == "prepare" else 2),
                )
                denials = []
                for nonce in [None, uuid4()]:
                    request(nonce)
                    try:
                        reviews.tick(UUID(bid))
                        denials.append(False)
                    except DomainError as error:
                        denials.append(error.code == "MODEL_CALL_REQUIRES_RECONCILIATION")
                with connection(True) as c:
                    after_operations = c.execute(
                        "SELECT count(*) AS n FROM operations WHERE bid_id=%s", (bid,)
                    ).fetchone()["n"]
                check(
                    "new_command_or_nonce_is_not_paid_retry_authorization",
                    all(denials)
                    and len(calls) == transport_before
                    and before_operations == after_operations,
                    dict(
                        phase=os.environ["QA_A1_CONTROLLER_UNKNOWN"],
                        denials=denials,
                        operations_before=before_operations,
                        operations_after=after_operations,
                    ),
                )
                result.update(
                    passed=all(c["passed"] for c in result["checks"]),
                    offline_transport_requests=len(calls),
                    ledger=ledger,
                )
                raise PreparationComplete
            stage = "complete_freeze_and_review"
            request()
            first = finish_request()
            review_id = first["request"]["review_id"]
            view = client.get(f"/api/v1/reviews/{review_id}").json()
            matrix = client.get(f"/api/v1/reviews/{review_id}/matrix").json()
            findings = client.get(f"/api/v1/reviews/{review_id}/findings").json()["items"]
            manifest = client.get(f"/api/v1/reviews/{review_id}/manifest").json()
            check("only_extract_and_core_offline_calls", len(calls) == 2)
            core_payload = calls[1]
            with connection() as c:
                evidence_ids = {
                    str(r["id"])
                    for r in c.execute(
                        "SELECT id FROM evidence_blocks WHERE parse_id=%s", (body_parse,)
                    ).fetchall()
                }
                frozen = c.execute("SELECT * FROM review_inputs WHERE bid_id=%s", (bid,)).fetchone()
                blob = c.execute(
                    "SELECT * FROM blobs WHERE id=%s", (frozen["evidence_manifest_blob_id"],)
                ).fetchone()
            check(
                "full_body_all_physical_pages_and_evidence_not_rag",
                [p["physical_page"] for p in core_payload["full_body_pages"]] == [1, 2, 3]
                and {b["id"] for b in core_payload["full_body_blocks"]} == evidence_ids
                and any(
                    "Final physical page" in b["text_content"]
                    for b in core_payload["full_body_blocks"]
                ),
            )
            check(
                "frozen_manifest_and_actual_evidence_blob_sha",
                manifest["evidence_manifest_digest"]
                == sha(objects[blob["object_key"]])
                == blob["digest"]
                and manifest["body_version_id"] == str(body)
                and set(manifest["parse_run_ids"]),
            )
            layers = view["coverage_layers"]
            check(
                "four_layers_scoring_excluded_from_core_denominator",
                set(layers) >= set(["source_scope", "core_extraction", "judgment", "scoring"])
                and layers["judgment"]["planned"] == 2
                and layers["judgment"]["completed"] == 2
                and layers["scoring"]["planned"] == 1
                and layers["scoring"]["state"] == "not_run"
                and layers["human_confirmation_pending"] == 3,
                layers,
            )
            check(
                "rules_pending_and_formal_prediction_unavailable",
                all(row["rule"]["confirmation"] == "pending" for row in matrix["rows"])
                and view["prediction"]["mode"] == "unavailable",
            )
            check(
                "actual_risks_exist_and_not_model_confirmed",
                bool(findings)
                and any(f["severity"] == "critical" and f["decision"] is None for f in findings),
            )
            # This test ends before report/submission mutations and preserves 04 source.
            if os.environ.get("QA_A1_REUSE_AFTER_CLONE"):
                from packages.domain.review_data import read_bundle

                with connection() as c:
                    initial_source = reviews.source_fingerprint(
                        read_bundle(c, UUID(bid), UUID(manifest["material_snapshot_id"]))
                    )
                body, _, _ = seed_doc(
                    "body",
                    [
                        "Construction period will be 90 days.",
                        "",
                        "Signed final body page. QA " + uuid4().hex[:8],
                    ],
                )
                set_material(body)
                request()
                second = finish_request()
                prior_id = second["request"]["review_id"]
                with connection() as c:
                    before_head = c.execute(
                        "SELECT current_body_id,current_material_id,current_rule_set_id FROM bid_profiles WHERE bid_id=%s",
                        (bid,),
                    ).fetchone()
                    current_source = reviews.source_fingerprint(
                        read_bundle(c, UUID(bid), before_head["current_material_id"])
                    )
                    before_prepares = c.execute("SELECT count(*) AS n FROM operations WHERE bid_id=%s AND handler_version='prepare/1'", (bid,)).fetchone()["n"]
                before_calls = len(calls)
                request()
                reviews.tick(UUID(bid))
                repeated = state().json()
                with connection() as c:
                    after_head = c.execute(
                        "SELECT current_body_id,current_material_id,current_rule_set_id FROM bid_profiles WHERE bid_id=%s",
                        (bid,),
                    ).fetchone()
                    next_op = c.execute(
                        "SELECT id,handler_version,kind,state FROM operations WHERE id=%s",
                        (repeated["request"]["prepare_operation_id"],),
                    ).fetchone()
                    after_prepares = c.execute("SELECT count(*) AS n FROM operations WHERE bid_id=%s AND handler_version='prepare/1'", (bid,)).fetchone()["n"]
                check(
                    "unchanged_input_after_cloning_reuses_current_review_without_reextract",
                    repeated["request"].get("review_id") == prior_id
                    and (next_op is None or next_op["handler_version"] != "prepare/1")
                    and after_prepares == before_prepares
                    and len(calls) == before_calls,
                    dict(
                        initial_source_fingerprint=initial_source,
                        current_source_fingerprint=current_source,
                        heads_identical=before_head == after_head,
                        prior_review_id=prior_id,
                        request_state=repeated["request"],
                        new_operation=next_op,
                        offline_model_calls=before_calls,
                        prepare_count_before=before_prepares,
                        prepare_count_after=after_prepares,
                    ),
                )
                if os.environ.get("QA_A1_REUSE_PROVENANCE") == "1":
                    with connection() as c:
                        derived = c.execute("SELECT payload FROM audit_entries WHERE bid_id=%s AND event_type='rules.source_equivalent_cloned' ORDER BY created_at DESC LIMIT 1", (bid,)).fetchone()
                        parent_effect = c.execute("SELECT e.result,o.bid_id FROM effect_receipts e JOIN operations o ON o.id=e.operation_id WHERE e.operation_id=%s AND e.effect_key='extract-rules'", (derived["payload"]["extraction_operation_id"],)).fetchone() if derived else None
                    provenance = derived["payload"] if derived else {}
                    check("cloned_rules_keep_actual_extraction_effect_and_exact_source_method_provenance",
                          bool(parent_effect)
                          and str(parent_effect["bid_id"]) == bid
                          and provenance.get("from_rule_set_id") == str(parent_effect["result"].get("result_ref"))
                          and provenance.get("rule_set_id") == str(before_head["current_rule_set_id"])
                          and provenance.get("material_snapshot_id") == str(before_head["current_material_id"])
                          and provenance.get("source_fingerprint") == current_source
                          and provenance.get("method_digest") == sha(reviews.method_profile())
                          and provenance.get("extraction_effect_digest") == sha(parent_effect["result"]))
                    supplement, _, _ = seed_doc("supplement", ["Separate source update requires a fresh grounded extraction. QA " + uuid4().hex])
                    set_material(body, [("supplement", supplement)])
                    request()
                    reviews.tick(UUID(bid))
                    changed_source = state().json()["request"]
                    with connection() as c:
                        changed_operation = c.execute("SELECT handler_version,state FROM operations WHERE id=%s", (changed_source["prepare_operation_id"],)).fetchone()
                        changed_count = c.execute("SELECT count(*) AS n FROM operations WHERE bid_id=%s AND handler_version='prepare/1'", (bid,)).fetchone()["n"]
                    check("changed_nonbody_source_after_clone_queues_fresh_extraction_without_sending",
                          changed_operation["handler_version"] == "prepare/1"
                          and changed_count == after_prepares + 1
                          and len(calls) == before_calls)
                result.update(
                    passed=all(c["passed"] for c in result["checks"]),
                    scenario="Unchanged input after rule-set cloning must reuse current review without fresh extraction",
                    offline_transport_requests=len(calls),
                )
                raise PreparationComplete
            if os.environ.get("QA_A1_HUMAN_API"):
                from qa.a1_human_cases import run_cases

                human = run_cases(
                    client=client,
                    bid=bid,
                    headers=headers,
                    check=check,
                    calls=calls,
                    objects=objects,
                    out=out,
                    request=request,
                    finish_request=finish_request,
                    seed_doc=seed_doc,
                    set_material=set_material,
                    body_version=body,
                    pump=pump,
                    state=state,
                    review_id=review_id,
                    view=view,
                    matrix=matrix,
                    tender=tender,
                )
                result.update(human, passed=all(c["passed"] for c in result["checks"]))
                raise PreparationComplete
            stage = "same_content_and_nonce"
            request()
            same = finish_request()
            check(
                "same_content_without_nonce_reuses_review",
                same["request"]["review_id"] == review_id and len(calls) == 2,
            )
            request(uuid4())
            again = finish_request()
            second_id = again["request"]["review_id"]
            second = client.get(f"/api/v1/reviews/{second_id}").json()
            second_findings = client.get(f"/api/v1/reviews/{second_id}/findings").json()["items"]
            check(
                "nonce_new_execution_same_content_digest",
                second_id != review_id
                and second["input_digest"] == view["input_digest"]
                and len(calls) == 3,
            )
            check(
                "repeat_critical_deduplicates_and_retains_origin",
                len([f for f in second_findings if f['severity']=='critical']) == len([f for f in findings if f['severity']=='critical'])
                and all(f['origin_review_id']==review_id for f in second_findings if f['severity']=='critical'),
            )
            stage = "new_body_preserves_risks_until_actual_recheck"
            body, _, _ = seed_doc(
                "body",
                [
                    "Construction period will be 90 days.",
                    "",
                    "FINAL DURATION SCHEDULE: construction period is 120 days. QA " + marker,
                ],
            )
            set_material(body)
            request()
            for _ in range(4):
                reviews.tick(UUID(bid))
                s = state().json()
                if s["request"].get("review_id"):
                    break
            assert s["request"].get("review_id")
            new_id = s["request"]["review_id"]
            inherited = client.get(f"/api/v1/reviews/{new_id}/findings").json()["items"]
            check(
                "new_body_unreviewed_keeps_known_critical",
                any(
                    f["severity"] == "critical"
                    and f["lifecycle"] == "open"
                    and f["detection_run_state"] == "not_run_this_round"
                    for f in inherited
                ),
            )
            desired["verdict"] = "satisfied"
            assert pump(s["current_review"]["operation_id"]) == "succeeded"
            reviews.tick(UUID(bid))
            resolved = client.get(f"/api/v1/reviews/{new_id}/findings").json()["items"]
            new_view=client.get(f'/api/v1/reviews/{new_id}').json()
            new_matrix=client.get(f'/api/v1/reviews/{new_id}/matrix').json()
            counter_gate=client.get(f'/api/v1/bids/{bid}/submission-gate').json()
            check('necessary_final_duration_page_unread_must_not_auto_resolve_prior_critical',
                  any(f['severity']=='critical' and f['lifecycle'] in ('open','deferred') for f in resolved),
                  dict(findings=resolved,coverage=new_view['coverage_layers'],matrix=new_matrix,gate=counter_gate,
                       actual_unread_page_text='FINAL DURATION SCHEDULE: construction period is 120 days.',
                       controlled_parser_gap='NECESSARY_FINAL_DURATION_SCHEDULE_UNREAD_PHYSICAL_PAGE_3',
                       actual_model_pages=calls[-1]['full_body_pages'],actual_model_blocks=calls[-1]['full_body_blocks']))
            result.update(passed=all(c['passed'] for c in result['checks']),offline_transport_requests=len(calls),review_ids=[review_id,second_id,new_id],first_review=view,first_manifest=manifest)
            raise PreparationComplete
            history = client.get(f"/api/v1/bids/{bid}/reviews").json()
            old = client.get(f"/api/v1/reviews/{review_id}").json()
            check(
                "history_keeps_prior_round_and_frozen_input",
                len(history["items"]) == 3
                and not old["current"]
                and client.get(f"/api/v1/reviews/{review_id}/manifest").json() == manifest,
            )
            stage='main04_submission_report_cases'
            from qa.submission04_cases import run_cases
            extra=run_cases(client=client,bid=bid,headers=headers,check=check,calls=calls,objects=objects,out=out,body=body,review_id=new_id,member=member,manifest=manifest)
            result.update(extra)
            result.update(
                passed=all(c["passed"] for c in result["checks"]),
                offline_transport_requests=len(calls),
                review_ids=[review_id, second_id, new_id],
                first_review=view,
                first_matrix=matrix,
                first_manifest=manifest,
                first_findings=findings,
                history=history,
            )
        except PreparationComplete:
            pass
        except Exception as error:
            result["fatal"] = dict(
                stage=stage,
                type=type(error).__name__,
                message="Independent frozen A1 probe incomplete; no private configuration or request credentials logged",
            )
    entries=json.loads((root/'runtime-manifest.json').read_text())
    assert all(hashlib.sha256((root/'execution-inputs'/r['path']).read_bytes()).hexdigest()==r['sha256'] for r in entries)
    result['runtime_manifest_sha256']=hashlib.sha256((root/'runtime-manifest.json').read_bytes()).hexdigest()
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, default=str, indent=2) + "\n"
    )
    print(
        dict(
            passed=result["passed"],
            checks=len(result["checks"]),
            failed=[c["name"] for c in result["checks"] if not c["passed"]],
            fatal=result.get("fatal"),
            offline_calls=len(calls),
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
