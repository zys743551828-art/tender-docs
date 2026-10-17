"""Real restricted PG/S3 knowledge lifecycle, scope and historical provenance."""

import copy
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest

from packages.domain import storage
from packages.domain.runtime import Command, DomainError, assert_role, connection, now, sha
from packages.identity.repository import record_binding, register_bidder
from packages.knowledge import bridge, repository
from packages.rag.indexing import ensure_parse_sources

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KNOWLEDGE_INTEGRATION") != "1", reason="需要独立真实PG/S3受限API/Worker环境"
)


def new_bid(c, actor, bidder, title, region="浙江", kind="市政道路", confirmed=True):
    bid, project, round_id = uuid4(), uuid4(), uuid4()
    c.execute(
        "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,'{}',%s)", (bid, title, actor)
    )
    c.execute(
        "INSERT INTO engineering_projects(id,title,region,engineering_type) VALUES(%s,%s,%s,%s)",
        (project, title, region, kind),
    )
    c.execute(
        "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
        (round_id, project),
    )
    c.execute(
        "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
        (bid, round_id, bidder),
    )
    c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (uuid4(), bid))
    c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (bid,))
    if confirmed is not None:
        record_binding(
            c, bid, actor, None, reason="合成测试明确核验工程及投标主体", confirmed=confirmed
        )
    return bid


@pytest.fixture
def environment():
    assert_role()
    original = Path(
        "/workspace/tender-docs/04-验证与测试/authoring-development-checks/candidate03-evidence/synthetic-body.pdf"
    ).read_bytes()
    digest = sha(original)
    storage.put("blobs/" + digest, original, "application/pdf")
    import pypdfium2

    pdf = pypdfium2.PdfDocument(original)
    page = pdf[0]
    textpage = page.get_textpage()
    actual_text = textpage.get_text_bounded()
    textpage.close()
    page.close()
    pdf.close()
    with connection() as c:
        actor = uuid4()
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成知识维护成员')", (actor,))
        bidder, other = [
            UUID(register_bidder(c, actor, name, "合成测试明确登记，不授资质业绩资格", None)["id"])
            for name in ["合成本公司", "合成其他企业"]
        ]
        origin = new_bid(c, actor, bidder, "合成历史来源工程")
        target = new_bid(c, actor, bidder, "合成当前工程")
        foreign = new_bid(c, actor, other, "合成其他企业工程")
        blob = uuid4()
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf') ON CONFLICT(digest) DO NOTHING",
            (blob, digest, "blobs/" + digest, len(original)),
        )
        blob = c.execute("SELECT id FROM blobs WHERE digest=%s", (digest,)).fetchone()["id"]
        version, parse, evidence = uuid4(), uuid4(), uuid4()
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成工法原文.pdf',now(),'source_original',%s)",
            (version, origin, blob, actor),
        )
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
            (parse, origin, version, sha("knowledge-actual-PDFium"), sha({})),
        )
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,595,842,0,'usable','[]')",
            (origin, parse),
        )
        c.execute(
            "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",
            (evidence, origin, parse, actual_text, sha(actual_text)),
        )
        root, source = ensure_parse_sources(c, origin, parse, publish_readable=True)
        revision = c.execute(
            "SELECT revision FROM retrieval.source_heads WHERE source_id=%s", (source,)
        ).fetchone()["revision"]
        candidate = repository.create_candidate(
            c,
            actor,
            {
                "title": "合成道路工法参考",
                "content": "以原件技术措施作为参考，具体工期和数量需按当前项目另行核实。",
                "source_version_ids": [str(version)],
                "scope": "仅限浙江市政道路、本公司适用条件经核实后参考",
            },
        )
    rules = {
        "bidder_ids": [str(bidder)],
        "bid_ids": [],
        "regions": ["浙江"],
        "engineering_types": ["市政道路"],
        "conditions": [],
        "purposes": ["assist", "authoring", "history"],
        "validity_kind": "bounded",
        "valid_from": (now() - timedelta(days=1)).isoformat(),
        "valid_until": (now() + timedelta(days=30)).isoformat(),
        "validity_basis": "合成测试核实的有效窗口，实际业务需原件依据",
        "specificity_note": "工期120天为原项目信息，禁止自动成为本项目事实",
    }
    ref = {
        "source_id": str(source),
        "source_revision": revision,
        "document_version_id": str(version),
        "parse_id": str(parse),
        "physical_page": 1,
        "evidence_id": str(evidence),
        "excerpt": actual_text[:30],
    }
    body = {
        "title": "合成道路工法参考",
        "content": "以原件技术措施作为参考，具体工期和数量需按当前项目另行核实。",
        "categories": ["method", "chapter_reference"],
        "scope": "浙江市政道路条件下的参考",
        "applicability": rules,
        "source_refs": [ref],
    }
    return {
        "actor": actor,
        "bidder": bidder,
        "other": other,
        "origin": origin,
        "target": target,
        "foreign": foreign,
        "item": UUID(candidate["id"]),
        "candidate": candidate,
        "body": body,
        "root": root,
        "original": original,
        "blob_key": "blobs/" + digest,
    }


def action(c, env, name, revision):
    current = repository.view(c, env["item"])
    assert current["revision"] == revision
    return repository.transition(
        c,
        env["item"],
        env["actor"],
        {"decision": name, "reason": "合成成员明确核对后决定", "evidence_ids": []},
        repository.knowledge_etag(current),
        name,
    )


def publish(env, body=None):
    with connection() as c:
        version = repository.add_version(
            c,
            env["item"],
            env["actor"],
            copy.deepcopy(body or env["body"]),
            repository.knowledge_etag(env["candidate"]),
        )
    with connection() as c:
        pending = action(c, env, "submit", version["revision"])
    with connection() as c:
        return action(c, env, "publish", pending["revision"])


def test_candidate_unknown_metadata_is_never_published_and_draft_is_preserved(environment):
    env = environment
    with pytest.raises(DomainError) as exc, connection() as c:
        action(c, env, "publish", 1)
    assert exc.value.code == "KNOWLEDGE_NOT_READY"
    with connection() as c:
        assert repository.view(c, env["item"])["workflow"] == "candidate"
        assert not bridge.eligible_knowledge_sources(
            c, env["target"], "assist", now(), [UUID(env["candidate"]["version_id"])]
        )["eligible"]


def test_publish_subject_scope_reference_withdraw_and_old_originals_restore(environment):
    env = environment
    active = publish(env)
    with connection() as c:
        good = bridge.eligible_knowledge_sources(
            c, env["target"], "authoring", now(), [UUID(active["version_id"])]
        )
        wrong = bridge.eligible_knowledge_sources(
            c, env["foreign"], "authoring", now(), [UUID(active["version_id"])]
        )
        assert len(good["eligible"]) == 1 and not wrong["eligible"]
        assert "SUBJECT_MISMATCH" in wrong["excluded"][0]["reasons"]
        with pytest.raises(DomainError):
            bridge.eligible_knowledge_sources(c, env["target"], "formal_score", now())
    body = {
        "bid_id": str(env["target"]),
        "version_id": active["version_id"],
        "purpose": "authoring",
        "target_kind": "project",
        "target_object_id": str(env["target"]),
        "excerpt": "以原件技术措施作为参考",
        "applicability_reason": "主体、地区、工程类型和期限已核对，工期不作为本项目事实",
    }
    with connection() as c:
        command = Command(
            c, env["actor"], "knowledge-test-reference", env["item"], uuid4().hex, body
        )
        referenced = bridge.cite(
            c,
            env["target"],
            env["item"],
            UUID(active["version_id"]),
            env["actor"],
            body,
            command.id,
        )
        command.finish(referenced)
        assert not c.execute(
            "SELECT id FROM fact_items WHERE bid_id=%s", (env["target"],)
        ).fetchone()
    with connection() as c:
        withdrawn = action(c, env, "withdraw", active["revision"])
    assert withdrawn["workflow"] == "withdrawn"
    with connection() as c:
        with pytest.raises(DomainError):
            bridge.check_reference(
                c, env["target"], env["item"], UUID(active["version_id"]), "authoring"
            )
        history = bridge.reference_history(c, UUID(referenced["citation_ids"][0]))
        assert history["reference"]["excerpt"] == body["excerpt"]
        assert history["version_content"] == active["content"]
        assert history["current_applicability_reasons"]
        impact = bridge.impacts(c, env["item"], UUID(active["version_id"]))
        assert impact["groups"]["working"][0]["bid_id"] == str(env["target"])
    assert sha(storage.get(env["blob_key"])) == sha(env["original"])


def test_version_cas_keeps_published_old_until_explicit_new_publication(environment):
    env = environment
    active = publish(env)
    barrier = Barrier(2)

    def submit(label):
        barrier.wait()
        try:
            with connection() as c:
                return repository.add_version(
                    c,
                    env["item"],
                    env["actor"],
                    copy.deepcopy(env["body"]) | {"content": label},
                    repository.knowledge_etag(active),
                )
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["候选改写甲", "候选改写乙"]))
    assert sum(isinstance(v, dict) for v in results) == 1 and 412 in results
    newer = next(v for v in results if isinstance(v, dict))
    with connection() as c:
        old = repository.view(c, env["item"], UUID(active["version_id"]))
        assert old["workflow"] == "published" and newer["workflow"] == "candidate"
        assert bridge.check_reference(
            c, env["target"], env["item"], UUID(active["version_id"]), "assist"
        )
        pending = action(c, env, "submit", newer["revision"])
    with connection() as c:
        new = action(c, env, "publish", pending["revision"])
    with connection() as c:
        assert (
            repository.view(c, env["item"], UUID(active["version_id"]))["workflow"] == "superseded"
        )
        assert new["version_id"] != active["version_id"]
        with pytest.raises(DomainError):
            bridge.check_reference(
                c, env["target"], env["item"], UUID(active["version_id"]), "assist"
            )


def test_parent_withdrawal_invalidates_published_knowledge_and_old_quote_remains(environment):
    env = environment
    active = publish(env)
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (env["root"],),
        )
    with connection() as c:
        assert repository.view(c, env["item"])["workflow"] == "source_invalid"
        assert not bridge.eligible_knowledge_sources(
            c, env["target"], "assist", now(), [UUID(active["version_id"])]
        )["eligible"]
        assert (
            repository.view(c, env["item"], UUID(active["version_id"]))["source_refs"][0]["excerpt"]
            == env["body"]["source_refs"][0]["excerpt"]
        )


def test_expiry_unknown_and_missing_conditions_never_become_applicable(environment):
    env = environment
    unknown = copy.deepcopy(env["body"])
    unknown["applicability"]["validity_kind"] = "unknown"
    unknown["applicability"]["valid_until"] = None
    with connection() as c:
        version = repository.add_version(
            c, env["item"], env["actor"], unknown, repository.knowledge_etag(env["candidate"])
        )
    with pytest.raises(DomainError) as exc, connection() as c:
        action(c, env, "submit", version["revision"])
    assert exc.value.code == "KNOWLEDGE_VALIDITY_UNKNOWN"
    expired = copy.deepcopy(env["body"])
    expired["applicability"]["valid_until"] = (now() - timedelta(minutes=1)).isoformat()
    with connection() as c:
        old = repository.add_version(
            c,
            env["item"],
            env["actor"],
            expired,
            repository.knowledge_etag(version),
        )
    with pytest.raises(DomainError), connection() as c:
        action(c, env, "submit", old["revision"])
    needed = copy.deepcopy(env["body"])
    needed["applicability"]["conditions"] = [
        {"subject": "工程", "scope": "当前项目", "name": "开挖深度", "value": 3, "unit": "m"}
    ]
    with connection() as c:
        next_version = repository.add_version(
            c,
            env["item"],
            env["actor"],
            needed,
            repository.knowledge_etag(old),
        )
    with connection() as c:
        pending = action(c, env, "submit", next_version["revision"])
    with connection() as c:
        published = action(c, env, "publish", pending["revision"])
    with connection() as c:
        denied = bridge.eligible_knowledge_sources(
            c, env["target"], "assist", now(), [UUID(published["version_id"])]
        )
        assert (
            not denied["eligible"]
            and "PROJECT_FACT_UNKNOWN_OR_CONFLICT" in denied["excluded"][0]["reasons"]
        )


def test_foreign_company_fact_and_forged_positions_do_not_create_versions(environment):
    env = environment
    foreign = copy.deepcopy(env["body"])
    foreign["categories"] = ["company_fact"]
    foreign["applicability"]["bidder_ids"] = [str(env["other"])]
    with pytest.raises(DomainError) as exc, connection() as c:
        repository.add_version(
            c, env["item"], env["actor"], foreign, repository.knowledge_etag(env["candidate"])
        )
    assert exc.value.code == "KNOWLEDGE_SUBJECT_CONFLICT"
    for field, value in [
        ("physical_page", 2),
        ("excerpt", "不在原文中的捏造引文"),
        ("source_id", str(env["root"])),
    ]:
        forged = copy.deepcopy(env["body"])
        forged["source_refs"][0][field] = value
        with pytest.raises(DomainError) as exc, connection() as c:
            repository.add_version(
                c, env["item"], env["actor"], forged, repository.knowledge_etag(env["candidate"])
            )
        assert exc.value.code == "KNOWLEDGE_PROVENANCE_INVALID"
    with connection() as c:
        assert repository.view(c, env["item"])["revision"] == 1
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.knowledge_versions WHERE item_id=%s",
                (env["item"],),
            ).fetchone()["n"]
            == 1
        )


def test_published_knowledge_expires_at_the_boundary_without_rewriting_history(environment):
    env = environment
    active = publish(env)
    boundary = __import__("datetime").datetime.fromisoformat(
        env["body"]["applicability"]["valid_until"]
    )
    with connection() as c:
        assert bridge.eligible_knowledge_sources(
            c,
            env["target"],
            "assist",
            boundary - timedelta(microseconds=1),
            [UUID(active["version_id"])],
        )["eligible"]
        assert not bridge.eligible_knowledge_sources(
            c, env["target"], "assist", boundary, [UUID(active["version_id"])]
        )["eligible"]
        assert (
            repository.view(c, env["item"], UUID(active["version_id"]), boundary)["workflow"]
            == "expired"
        )
        assert (
            repository.view(c, env["item"], UUID(active["version_id"]), boundary)["content"]
            == active["content"]
        )


def test_actual_login_http_cas_replay_withdraw_and_reference_history(environment):
    from fastapi.testclient import TestClient

    from apps.api.auth import password_hash
    from apps.api.knowledge_routes import router
    from apps.api.main import app
    from packages.domain.runtime import required
    from packages.knowledge.contracts import validate_actual

    env = environment
    login, password = "knowledge-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (env["actor"], login, password_hash(password)),
        )
    if not any(getattr(route, "path", "") == "/api/v1/knowledge" for route in app.routes):
        app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/v1/knowledge").status_code == 401
        origin = required("APP_ORIGIN")
        signed = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert signed.status_code == 200
        base = {"Origin": origin, "X-CSRF-Token": signed.json()["csrf_token"]}

        def headers(tag=None, key=None):
            return (
                base | {"Idempotency-Key": key or uuid4().hex} | ({"If-Match": tag} if tag else {})
            )

        endpoint = "/api/v1/knowledge/" + str(env["item"])
        got = client.get(endpoint)
        assert got.status_code == 200
        validate_actual("KnowledgeView", got.json())
        assert (
            client.post(
                endpoint + "/versions", json=env["body"], headers={"Origin": origin}
            ).status_code
            == 403
        )
        assert (
            client.post(endpoint + "/versions", json=env["body"], headers=headers()).status_code
            == 428
        )
        key = uuid4().hex
        saved = client.post(
            endpoint + "/versions", json=env["body"], headers=headers(got.headers["etag"], key)
        )
        assert saved.status_code == 201, saved.json()
        validate_actual("KnowledgeView", saved.json())
        replay = client.post(
            endpoint + "/versions", json=env["body"], headers=headers(got.headers["etag"], key)
        )
        assert replay.status_code == 201 and replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json() == saved.json()
        tag = saved.headers["etag"]
        for action in ["submit", "publish"]:
            response = client.post(
                endpoint + "/" + action,
                json={"decision": action, "reason": "已核实出处主体及期限", "evidence_ids": []},
                headers=headers(tag),
            )
            assert response.status_code == 200, response.json()
            tag = response.headers["etag"]
        published = response.json()
        search = client.get(
            "/api/v1/knowledge", params={"bid_id": str(env["target"]), "q": "道路工法"}
        )
        assert search.status_code == 200
        validate_actual("KnowledgePage", search.json())
        assert [r["id"] for r in search.json()["items"]] == [str(env["item"])]
        foreign = client.get(
            "/api/v1/knowledge", params={"bid_id": str(env["foreign"]), "q": "道路工法"}
        )
        assert foreign.json()["items"] == []
        request = {
            "bid_id": str(env["target"]),
            "version_id": published["version_id"],
            "purpose": "authoring",
            "target_kind": "project",
            "target_object_id": str(env["target"]),
            "excerpt": "以原件技术措施作为参考",
            "applicability_reason": "仅用作参考，项目事实另核",
        }
        cite_key = uuid4().hex
        cited = client.post(endpoint + "/references", json=request, headers=headers(key=cite_key))
        assert cited.status_code == 201, cited.json()
        validate_actual("KnowledgeReference", cited.json())
        withdrawn = client.post(
            endpoint + "/withdraw",
            json={
                "decision": "withdraw",
                "reason": "发现适用冲突立即停用，旧稿保持原字节",
                "evidence_ids": [],
            },
            headers=headers(tag),
        )
        assert withdrawn.status_code == 200
        stale = client.post(endpoint + "/references", json=request, headers=headers(key=cite_key))
        assert stale.status_code == 409 and stale.json()["code"] == "KNOWLEDGE_NOT_APPLICABLE"
        history = client.get("/api/v1/knowledge-references/" + cited.json()["citation_ids"][0])
        assert history.status_code == 200
        validate_actual("KnowledgeReferenceHistory", history.json())
        assert history.json()["reference"]["excerpt"] == request["excerpt"]
        impacts = client.get(endpoint + "/impacts?version_id=" + published["version_id"])
        validate_actual("KnowledgeImpact", impacts.json())
        original = client.get(
            "/api/v1/artifacts/"
            + env["body"]["source_refs"][0]["document_version_id"]
            + "/download"
        )
        assert original.status_code == 200
        assert (
            original.json()["artifact_id"] == env["body"]["source_refs"][0]["document_version_id"]
        )


def test_source_republication_cannot_resurrect_knowledge_and_changes_strong_etag(environment):
    env = environment
    active = publish(env)
    tag = repository.knowledge_etag(active)
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (env["root"],),
        )
    with connection() as c:
        changed = repository.view(c, env["item"])
        assert changed["revision"] == active["revision"] and changed["workflow"] == "source_invalid"
        assert repository.knowledge_etag(changed) != tag
    with pytest.raises(DomainError) as exc, connection() as c:
        repository.transition(
            c,
            env["item"],
            env["actor"],
            {"decision": "withdraw", "reason": "保留原件并禁止新用", "evidence_ids": []},
            tag,
            "withdraw",
        )
    assert exc.value.status == 412
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='published',revision=revision+1 WHERE source_id=%s",
            (env["root"],),
        )
    with connection() as c:
        assert repository.view(c, env["item"])["workflow"] == "source_invalid"
        assert not bridge.eligible_knowledge_sources(
            c, env["target"], "assist", now(), [UUID(active["version_id"])]
        )["eligible"]


def test_worker_does_not_gain_project_metadata_capabilities_and_only_reads_frozen_source_authority(
    environment,
):
    from packages.knowledge.sources import authority_manifest

    env = environment
    active = publish(env)
    with connection() as c:
        prepared = bridge.eligible_knowledge_sources(
            c, env["target"], "authoring", now(), [UUID(active["version_id"])]
        )["eligible"][0]
    assert_role(True)
    with connection(True) as c:
        privileges = c.execute(
            "SELECT has_table_privilege(current_user,'design.bids','UPDATE') AS writes_bid, has_table_privilege(current_user,'design.bid_rounds','SELECT') AS reads_round, has_table_privilege(current_user,'design.engineering_projects','SELECT') AS reads_project"
        ).fetchone()
        assert not any(privileges.values())
        actual = authority_manifest(c, [UUID(prepared["knowledge_source_id"])], now())
        assert actual == prepared["source_manifest"]
    # Project applicability was prepared by the API, not invented with owner access.
    assert prepared["reference_only"] is True and prepared["original_evidence_allowlist"]


def test_impact_groups_follow_real_project_phase_and_never_rewrite_reference(environment):
    env = environment
    active = publish(env)
    body = {
        "purpose": "assist",
        "target_kind": "project",
        "target_object_id": str(env["target"]),
        "excerpt": "以原件技术措施作为参考",
        "applicability_reason": "明确作为历史参考",
    }
    with connection() as c:
        cmd = Command(c, env["actor"], "knowledge-phase-reference", env["item"], uuid4().hex, body)
        reference = bridge.cite(
            c, env["target"], env["item"], UUID(active["version_id"]), env["actor"], body, cmd.id
        )
        cmd.finish(reference)
    with connection() as c:
        c.execute("UPDATE bid_profiles SET phase='submitted' WHERE bid_id=%s", (env["target"],))
        assert bridge.impacts(c, env["item"])["groups"]["submitted"][0]["bid_id"] == str(
            env["target"]
        )
    with pytest.raises(DomainError) as exc, connection() as c:
        cmd = Command(c, env["actor"], "knowledge-phase-reference", env["item"], uuid4().hex, body)
        bridge.cite(
            c, env["target"], env["item"], UUID(active["version_id"]), env["actor"], body, cmd.id
        )
    assert exc.value.code == "PROJECT_NOT_WRITABLE"
    with connection() as c:
        c.execute("UPDATE bid_profiles SET archived_at=now() WHERE bid_id=%s", (env["target"],))
        impact = bridge.impacts(c, env["item"])
        assert impact["groups"]["archived"][0]["bid_id"] == str(env["target"])
        restored = bridge.reference_history(c, UUID(reference["citation_ids"][0]))
        assert (
            restored["reference"] == reference and impact["historical_reports_rewritten"] is False
        )


def test_source_positions_are_authoritative_not_client_inferred(environment):
    from packages.knowledge.contracts import validate_actual

    env = environment
    with connection() as c:
        choices = repository.source_positions(
            c, env["item"], UUID(env["body"]["source_refs"][0]["document_version_id"]), 1, -1, 10
        )
        validate_actual("KnowledgeSourcePositions", choices)
        assert len(choices["items"]) == 1
        actual = choices["items"][0]["reference"]
        assert actual["source_id"] == env["body"]["source_refs"][0]["source_id"]
        assert actual["source_revision"] == env["body"]["source_refs"][0]["source_revision"]
        assert actual["parse_id"] == env["body"]["source_refs"][0]["parse_id"]
    with pytest.raises(DomainError) as exc, connection() as c:
        repository.source_positions(c, env["item"], uuid4(), 1, -1, 10)
    assert exc.value.code == "KNOWLEDGE_ORIGINAL_NOT_SELECTED"


def test_later_publication_is_not_backfilled_into_earlier_reference_scope(environment):
    from datetime import datetime

    env = environment
    active = publish(env)
    just_before = datetime.fromisoformat(active["confirmed_at"]) - timedelta(microseconds=1)
    with connection() as c:
        assert not bridge.eligible_knowledge_sources(
            c, env["target"], "history", just_before, [UUID(active["version_id"])]
        )["eligible"]
        assert bridge.eligible_knowledge_sources(
            c, env["target"], "history", now(), [UUID(active["version_id"])]
        )["eligible"]


CONDITION = {"subject": "工程", "scope": "当前项目", "name": "开挖深度", "value": "3", "unit": "m"}


@pytest.fixture
def conditional(environment, request):
    """Real original bytes/parse sources in target project, distinct from knowledge origin."""
    env = environment
    body = copy.deepcopy(env["body"])
    condition = CONDITION | {"value": getattr(request, "param", CONDITION["value"])}
    body["applicability"]["conditions"] = [condition]
    env["condition"] = condition
    env["active"] = publish(env, body)
    with connection() as c:
        version, parse, evidence, snapshot = [uuid4() for _ in range(4)]
        original = UUID(env["body"]["source_refs"][0]["document_version_id"])
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) SELECT %s,%s,blob_id,'合成当前事实依据.pdf',now(),'source_original',%s FROM document_versions WHERE id=%s",
            (version, env["target"], env["actor"], original),
        )
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
            (snapshot, env["target"], sha(str(snapshot))),
        )
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0)",
            (snapshot, env["target"], version),
        )
        c.execute(
            "UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s",
            (snapshot, env["target"]),
        )
        origin_parse = UUID(env["body"]["source_refs"][0]["parse_id"])
        c.execute(
            "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) SELECT %s,%s,%s,parser_digest,config_digest,page_count,coverage,gaps FROM parse_runs WHERE id=%s",
            (parse, env["target"], version, origin_parse),
        )
        c.execute(
            "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) SELECT %s,%s,physical_page,width,height,rotation,quality,gaps FROM pages WHERE parse_id=%s",
            (env["target"], parse, origin_parse),
        )
        c.execute(
            "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) SELECT %s,%s,%s,physical_page,ordinal,kind,text_content,digest FROM evidence_blocks WHERE id=%s",
            (evidence, env["target"], parse, UUID(env["body"]["source_refs"][0]["evidence_id"])),
        )
        root, source = ensure_parse_sources(c, env["target"], parse, publish_readable=True)
    env.update(
        fact_evidence=evidence,
        fact_parse=parse,
        fact_version=version,
        fact_root=root,
        fact_source=source,
    )
    return env


def fact_input(env, **overrides):
    return (
        CONDITION
        | {
            "evidence_ids": [str(env["fact_evidence"])],
            "reason": "显式合成机制确认，非真实公司事实质量证明",
        }
        | overrides
    )


def add_confirmed_fact(env, **overrides):
    from packages.domain.fact_sets import replace_set

    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (env["target"],))
        return replace_set(
            c,
            env["target"],
            env["actor"],
            "合成核验后确认",
            kind="add",
            new_fact=fact_input(env, **overrides),
        )[1]


def assert_condition_denied(env):
    with connection() as c:
        scope = bridge.eligible_knowledge_sources(
            c, env["target"], "authoring", now(), [UUID(env["active"]["version_id"])]
        )
        assert not scope["eligible"]
        assert "PROJECT_FACT_UNKNOWN_OR_CONFLICT" in scope["excluded"][0]["reasons"]
        assert repository.search(c, None, None, 100, env["target"], "authoring")["items"] == []
        before = c.execute(
            "SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s", (env["target"],)
        ).fetchone()["n"]
        with pytest.raises(DomainError) as exc:
            bridge.cite(
                c,
                env["target"],
                env["item"],
                UUID(env["active"]["version_id"]),
                env["actor"],
                reference_body(env),
                None,
            )
        assert exc.value.code == "KNOWLEDGE_NOT_APPLICABLE"
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.citations WHERE bid_id=%s", (env["target"],)
            ).fetchone()["n"]
            == before
        )


def reference_body(env):
    return {
        "bid_id": str(env["target"]),
        "version_id": env["active"]["version_id"],
        "purpose": "authoring",
        "target_kind": "project",
        "target_object_id": str(env["target"]),
        "excerpt": "以原件技术措施作为参考",
        "applicability_reason": "依据当前人工确认事实逐项核对",
    }


@pytest.mark.parametrize("conditional", [3, "3"], indirect=True)
def test_unconfirmed_matching_fact_without_evidence_is_not_knowledge_permission(conditional):
    from psycopg.types.json import Jsonb

    env = conditional
    with connection() as c:
        fact_set, fact = uuid4(), uuid4()
        c.execute(
            "INSERT INTO fact_sets(id,bid_id,digest) VALUES(%s,%s,%s)",
            (fact_set, env["target"], sha(str(fact_set))),
        )
        c.execute(
            "INSERT INTO fact_items(id,bid_id,fact_set_id,subject,scope,name,value,unit,evidence_ids) VALUES(%s,%s,%s,'工程','当前项目','开挖深度',%s,'m','{}')",
            (fact, env["target"], fact_set, Jsonb(env["condition"]["value"])),
        )
        c.execute(
            "INSERT INTO fact_heads(bid_id,fact_set_id,revision) VALUES(%s,%s,1)",
            (env["target"], fact_set),
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM decision_heads WHERE bid_id=%s AND object_kind='fact'",
                (env["target"],),
            ).fetchone()["n"]
            == 0
        )
    assert_condition_denied(env)


def test_current_confirmed_fact_and_multiple_exact_carryovers_are_usable(conditional):
    from packages.domain.fact_sets import current_facts
    from packages.domain.rule_sets import current_decision

    env = conditional
    original = add_confirmed_fact(env)
    for name in ["附加条件一", "附加条件二"]:
        add_confirmed_fact(env, name=name)
        with connection() as c:
            _, facts = current_facts(c, env["target"])
            inherited = next(f for f in facts if f["name"] == CONDITION["name"])
            assert inherited["id"] != original
            assert (
                current_decision(c, env["target"], "fact", inherited["id"])["object_id"] == original
            )
            assert bridge.eligible_knowledge_sources(
                c, env["target"], "authoring", now(), [UUID(env["active"]["version_id"])]
            )["eligible"]
    with connection() as c:
        cited = bridge.cite(
            c,
            env["target"],
            env["item"],
            UUID(env["active"]["version_id"]),
            env["actor"],
            reference_body(env),
            None,
        )
        assert cited["reference_only"] and not cited["project_fact_created"]


@pytest.mark.parametrize("state", ["rejected", "deferred", "confirmed_without_basis"])
def test_non_effective_decision_cannot_satisfy_condition(conditional, state):
    from packages.domain.human_decisions import append_decision

    env = conditional
    fact = add_confirmed_fact(env)
    with connection() as c:
        decision = append_decision(
            c,
            env["target"],
            env["actor"],
            "fact",
            fact,
            {
                "decision": "rejected"
                if state == "deferred"
                else "confirmed"
                if state == "confirmed_without_basis"
                else state,
                "reason": "合成无效决定反例",
                "evidence_ids": [str(env["fact_evidence"])],
            },
        )
        if state == "deferred":
            # No API supports fact deferral; explicit persisted-state safety negative only.
            c.execute(
                "INSERT INTO decisions(id,bid_id,object_kind,object_id,object_revision,decision,reason,evidence_ids,actor_id) VALUES(%s,%s,'fact',%s,3,'deferred','合成存储安全反例','{}',%s)",
                (new_id := uuid4(), env["target"], fact, env["actor"]),
            )
            c.execute(
                "UPDATE decision_heads SET decision_id=%s,revision=revision+1 WHERE bid_id=%s AND object_kind='fact' AND object_id=%s",
                (new_id, env["target"], fact),
            )
        assert decision["id"]
    assert_condition_denied(env)


@pytest.mark.parametrize(
    "change",
    [{"subject": "其他主体"}, {"scope": "历史项目"}, {"value": "4"}, {"unit": "cm"}],
)
def test_same_numeric_value_cannot_hide_subject_scope_unit_or_type_mismatch(conditional, change):
    env = conditional
    add_confirmed_fact(env, **change)
    assert_condition_denied(env)


@pytest.mark.parametrize("mode", ["conflicting_confirmed", "pending_equal"])
def test_conflicting_or_pending_matching_fact_is_not_filtered_out(conditional, mode):
    from packages.domain.fact_sets import current_facts

    env = conditional
    original = add_confirmed_fact(env)
    if mode == "conflicting_confirmed":
        add_confirmed_fact(env, value="4")
        assert_condition_denied(env)
        return
    # Equal values still cannot hide an explicitly unconfirmed duplicate.
    # Synthetic raw fact-set member models extractor output, not a user mutation endpoint.
    with connection() as c:
        head, _ = current_facts(c, env["target"])
        c.execute(
            "INSERT INTO fact_items(id,bid_id,fact_set_id,subject,scope,name,value,unit,evidence_ids) SELECT %s,bid_id,%s,subject,scope,name,value,unit,evidence_ids FROM fact_items WHERE id=%s",
            (uuid4(), head["fact_set_id"], original),
        )
    assert_condition_denied(env)


@pytest.mark.parametrize("change", ["ancestor_head", "withdrawn", "new_parse", "missing_carryover"])
def test_changed_confirmation_basis_or_unknown_carryover_stops_new_use(conditional, change):
    from packages.domain.fact_sets import current_facts
    from packages.domain.rule_sets import current_decision

    env = conditional
    original = add_confirmed_fact(env)
    with connection() as c:
        assert bridge.eligible_knowledge_sources(
            c, env["target"], "authoring", now(), [UUID(env["active"]["version_id"])]
        )["eligible"]
        if change == "ancestor_head":
            c.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1 WHERE source_id=%s",
                (env["fact_root"],),
            )
        elif change == "withdrawn":
            c.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",
                (env["fact_root"],),
            )
        elif change == "new_parse":
            c.execute(
                "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) SELECT %s,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps FROM parse_runs WHERE id=%s",
                (uuid4(), env["fact_parse"]),
            )
        else:
            head, _ = current_facts(c, env["target"])
            clone = uuid4()
            c.execute(
                "INSERT INTO fact_items(id,bid_id,fact_set_id,subject,scope,name,value,unit,evidence_ids) SELECT %s,bid_id,fact_set_id,subject,scope,name,value,unit,evidence_ids FROM fact_items WHERE id=%s",
                (clone, original),
            )
            decision = current_decision(c, env["target"], "fact", original)
            c.execute(
                "INSERT INTO decision_heads(bid_id,object_kind,object_id,revision,decision_id) VALUES(%s,'fact',%s,1,%s)",
                (env["target"], clone, decision["id"]),
            )
            assert head["fact_set_id"]
    assert_condition_denied(env)


def test_fact_source_change_rechecks_authenticated_reference_replay_and_preserves_history(
    conditional,
):
    from fastapi.testclient import TestClient

    from apps.api.auth import password_hash
    from apps.api.main import app
    from packages.domain.runtime import required

    env = conditional
    add_confirmed_fact(env)
    login, password = "knowledge-fact-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (env["actor"], login, password_hash(password)),
        )
    with TestClient(app) as client:
        origin = required("APP_ORIGIN")
        signed = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert signed.status_code == 200
        headers = {
            "Origin": origin,
            "X-CSRF-Token": signed.json()["csrf_token"],
            "Idempotency-Key": uuid4().hex,
        }
        endpoint = "/api/v1/knowledge/" + str(env["item"]) + "/references"
        body = reference_body(env)
        first = client.post(endpoint, json=body, headers=headers)
        assert first.status_code == 201, first.json()
        with connection() as c:
            c.execute(
                "UPDATE retrieval.source_heads SET revision=revision+1 WHERE source_id=%s",
                (env["fact_root"],),
            )
        second = client.post(endpoint, json=body, headers=headers)
        assert second.status_code == 409 and second.json()["code"] == "KNOWLEDGE_NOT_APPLICABLE"
        assert (
            client.get(
                "/api/v1/knowledge", params={"bid_id": str(env["target"]), "q": "道路工法"}
            ).json()["items"]
            == []
        )
        historical = client.get("/api/v1/knowledge-references/" + first.json()["citation_ids"][0])
        assert historical.status_code == 200
        assert historical.json()["reference"]["excerpt"] == body["excerpt"]
        assert (
            "PROJECT_FACT_UNKNOWN_OR_CONFLICT" in historical.json()["current_applicability_reasons"]
        )


@pytest.mark.parametrize("isolation", ["autocommit", "REPEATABLE READ", "SERIALIZABLE"])
def test_trusted_bridge_rejects_transaction_modes_that_cannot_protect_current_authority(
    environment, isolation
):
    import psycopg
    from psycopg.rows import dict_row

    from packages.domain.runtime import required
    from packages.knowledge.sources import lock_graph

    with psycopg.connect(
        required("API_DATABASE_DSN"), row_factory=dict_row, autocommit=isolation == "autocommit"
    ) as c:
        c.execute("SET search_path=design,public")
        if isolation != "autocommit":
            c.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
        with pytest.raises(ValueError, match="requires"):
            lock_graph(c)
        with pytest.raises(ValueError, match="requires"):
            bridge.eligible_knowledge_sources(c, environment["target"], "authoring", now())


@pytest.mark.parametrize("conditional", [3, True], indirect=True)
def test_confirmed_string_value_is_not_coerced_to_numeric_or_boolean_condition(conditional):
    env = conditional
    add_confirmed_fact(env)
    assert_condition_denied(env)


def test_fact_change_under_project_lock_is_seen_after_bridge_wait(conditional):
    from threading import Event

    from packages.domain.fact_sets import replace_set

    env = conditional
    original = add_confirmed_fact(env)
    started = Event()
    release_probe = Event()

    def read_after_lock():
        with connection() as other:
            other.execute("SET LOCAL lock_timeout='5s'")
            started.set()
            return bridge.eligible_knowledge_sources(
                other, env["target"], "authoring", now(), [UUID(env["active"]["version_id"])]
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with connection() as c:
            c.execute("SELECT id FROM bids WHERE id=%s FOR NO KEY UPDATE", (env["target"],))
            future = pool.submit(read_after_lock)
            assert started.wait(3)
            # This wait only lets the reader reach the already-held bid lock.
            assert not release_probe.wait(0.1) and not future.done()
            replace_set(
                c,
                env["target"],
                env["actor"],
                "合成并发事实更正",
                kind="replace",
                removed_id=original,
                new_fact=fact_input(env, value="4"),
            )
        result = future.result(timeout=5)
    assert not result["eligible"]
    assert "PROJECT_FACT_UNKNOWN_OR_CONFLICT" in result["excluded"][0]["reasons"]


@pytest.mark.parametrize("state", ["missing", "pending", "profile_changed_same_bidder"])
def test_matching_bidder_uuid_without_confirmed_binding_cannot_authorize_new_knowledge(
    environment, state
):
    env = environment
    env["active"] = publish(env)
    with connection() as c:
        if state == "missing":
            env["target"] = new_bid(
                c, env["actor"], env["bidder"], "合成无绑定依据项目", confirmed=None
            )
        elif state == "pending":
            record_binding(
                c, env["target"], env["actor"], None, reason="合成主体尚待核实", confirmed=False
            )
        else:
            # UUID, region and engineering_type all still match; only binding proof is stale.
            c.execute(
                "UPDATE bid_profiles SET round_id=(SELECT round_id FROM bid_profiles WHERE bid_id=%s) WHERE bid_id=%s",
                (env["origin"], env["target"]),
            )
        scope = bridge.eligible_knowledge_sources(
            c, env["target"], "authoring", now(), [UUID(env["active"]["version_id"])]
        )
        assert (
            not scope["eligible"]
            and scope["project_context"]["identity"]["binding_status"] == "pending"
        )
        assert "PROJECT_IDENTITY_UNCONFIRMED" in scope["excluded"][0]["reasons"]
        assert repository.search(c, None, None, 100, env["target"], "authoring")["items"] == []
        with pytest.raises(DomainError) as exc:
            bridge.cite(
                c,
                env["target"],
                env["item"],
                UUID(env["active"]["version_id"]),
                env["actor"],
                reference_body(env),
                None,
            )
        assert exc.value.code == "KNOWLEDGE_NOT_APPLICABLE"
        assert repository.view(c, env["item"])["content"] == env["active"]["content"]


def test_identity_context_freeze_and_http_replay_reject_pending_and_reconfirmed_same_uuid(
    environment,
):
    from fastapi.testclient import TestClient

    from apps.api.auth import password_hash
    from apps.api.main import app
    from packages.domain.runtime import required
    from packages.knowledge.contracts import validate_actual

    env = environment
    env["active"] = publish(env)
    login, password = "knowledge-identity-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (env["actor"], login, password_hash(password)),
        )
        initial = bridge.check_reference(
            c, env["target"], env["item"], UUID(env["active"]["version_id"]), "authoring"
        )
        frozen = initial["project_context"]
        round_id = frozen["identity"]["round_id"]
        assert (
            frozen["identity"]["binding_status"] == "confirmed"
            and frozen["identity"]["authority_id"]
        )
    with TestClient(app) as client:
        origin = required("APP_ORIGIN")
        signed = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert signed.status_code == 200
        headers = {
            "Origin": origin,
            "X-CSRF-Token": signed.json()["csrf_token"],
            "Idempotency-Key": uuid4().hex,
        }
        endpoint = f"/api/v1/knowledge/{env['item']}/references"
        body = reference_body(env)
        first = client.post(endpoint, json=body, headers=headers)
        assert first.status_code == 201, first.json()
        validate_actual("KnowledgeReference", first.json())
        assert first.json()["project_context"] == frozen
        historical_path = "/api/v1/knowledge-references/" + first.json()["citation_ids"][0]
        with connection() as c:
            c.execute(
                "UPDATE bid_profiles SET round_id=(SELECT round_id FROM bid_profiles WHERE bid_id=%s) WHERE bid_id=%s",
                (env["origin"], env["target"]),
            )
        assert (
            client.get("/api/v1/knowledge", params={"bid_id": str(env["target"])}).json()["items"]
            == []
        )
        rejected = client.post(endpoint, json=body, headers=headers)
        assert rejected.status_code == 409 and rejected.json()["code"] == "KNOWLEDGE_NOT_APPLICABLE"
        history = client.get(historical_path)
        assert history.status_code == 200 and history.json()["reference"] == first.json()
        assert "PROJECT_IDENTITY_UNCONFIRMED" in history.json()["current_applicability_reasons"]
        identity_path = f"/api/v1/bids/{env['target']}/identity"
        identity = client.get(identity_path)
        assert identity.status_code == 200
        corrected = client.put(
            identity_path,
            json={
                "bidder_id": str(env["bidder"]),
                "round_id": round_id,
                "reason": "合成核对原工程与同一真实主体后明确重新确认",
            },
            headers=headers
            | {"Idempotency-Key": uuid4().hex, "If-Match": identity.headers["etag"]},
        )
        assert corrected.status_code == 200, corrected.json()
        assert corrected.json()["identity"]["binding_status"] == "confirmed"
        assert corrected.json()["identity"]["authority_id"] != frozen["identity"]["authority_id"]
        replay = client.post(endpoint, json=body, headers=headers)
        assert (
            replay.status_code == 409
            and replay.json()["code"] == "KNOWLEDGE_PROJECT_CONTEXT_CHANGED"
        )
        fresh = client.post(endpoint, json=body, headers=headers | {"Idempotency-Key": uuid4().hex})
        assert fresh.status_code == 201, fresh.json()
        assert (
            fresh.json()["project_context"]["identity"]["bidder_id"]
            == frozen["identity"]["bidder_id"]
        )
        assert fresh.json()["project_context"]["identity"]["digest"] != frozen["identity"]["digest"]
        assert client.get(historical_path).json()["reference"] == first.json()
    with pytest.raises(DomainError) as exc, connection() as c:
        bridge.check_reference(
            c,
            env["target"],
            env["item"],
            UUID(env["active"]["version_id"]),
            "authoring",
            expected_context=frozen,
        )
    assert exc.value.code == "KNOWLEDGE_PROJECT_CONTEXT_CHANGED"
    assert storage.get(env["blob_key"]) == env["original"]


def test_older_reference_without_identity_context_remains_readable_but_cannot_replay_new_use(
    environment,
):
    from fastapi.testclient import TestClient

    from apps.api.auth import password_hash
    from apps.api.main import app
    from packages.domain.runtime import audit, required
    from packages.knowledge.contracts import validate_actual

    env = environment
    env["active"] = publish(env)
    body = reference_body(env)
    login, password, key = "knowledge-old-" + uuid4().hex, uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (env["actor"], login, password_hash(password)),
        )
        selected = bridge.cite(
            c,
            env["target"],
            env["item"],
            UUID(env["active"]["version_id"]),
            env["actor"],
            body,
            None,
        )
        historical = copy.deepcopy(selected)
        historical.pop("project_context")
        old_id = uuid4()
        historical["citation_ids"] = [str(old_id)]
        ref = env["body"]["source_refs"][0]
        c.execute(
            "INSERT INTO retrieval.citations(id,bid_id,source_id,source_revision,purpose,evidence_id,target_object_id) VALUES(%s,%s,%s,%s,'authoring',%s,%s)",
            (
                old_id,
                env["target"],
                UUID(selected["source_manifest"][0]["source_id"]),
                env["active"]["source_revision"],
                UUID(ref["evidence_id"]),
                env["target"],
            ),
        )
        audit(c, env["actor"], env["target"], "knowledge.cited", historical, None)
        restored = bridge.reference_history(c, old_id)
        validate_actual("KnowledgeReferenceHistory", restored)
        assert (
            restored["reference"] == historical and "project_context" not in restored["reference"]
        )
        assert restored["version_content"] == env["active"]["content"]
        # Explicit legacy ACK fixture; no historical identity is invented.
        command = Command(c, env["actor"], "knowledge-reference", env["item"], key, body)
        command.finish(historical, 201)
    with TestClient(app) as client:
        origin = required("APP_ORIGIN")
        signed = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert signed.status_code == 200
        replay = client.post(
            f"/api/v1/knowledge/{env['item']}/references",
            json=body,
            headers={
                "Origin": origin,
                "X-CSRF-Token": signed.json()["csrf_token"],
                "Idempotency-Key": key,
            },
        )
        assert (
            replay.status_code == 409
            and replay.json()["code"] == "KNOWLEDGE_PROJECT_CONTEXT_MISSING"
        )
        history = client.get(f"/api/v1/knowledge-references/{old_id}")
        assert history.status_code == 200 and history.json()["reference"] == historical


def test_architect_pending_fact_satisfies_knowledge_condition(environment):
    import json
    from psycopg.types.json import Jsonb
    env=environment
    body=copy.deepcopy(env['body'])
    body['applicability']['conditions']=[{'subject':'工程','scope':'当前项目','name':'开挖深度','value':3,'unit':'m'}]
    active=publish(env,body)
    with connection() as c:
        fact_set,fact=uuid4(),uuid4()
        c.execute('INSERT INTO fact_sets(id,bid_id,digest) VALUES(%s,%s,%s)',(fact_set,env['target'],sha(str(fact_set))))
        c.execute('INSERT INTO fact_items(id,bid_id,fact_set_id,subject,scope,name,value,unit,evidence_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(fact,env['target'],fact_set,'工程','当前项目','开挖深度',Jsonb(3),'m',[]))
        c.execute('INSERT INTO fact_heads(bid_id,fact_set_id,revision) VALUES(%s,%s,1) ON CONFLICT(bid_id) DO UPDATE SET fact_set_id=excluded.fact_set_id,revision=fact_heads.revision+1',(env['target'],fact_set))
        assert c.execute("SELECT count(*) AS n FROM decision_heads WHERE bid_id=%s AND object_kind='fact'",(env['target'],)).fetchone()['n']==0
        selected=bridge.eligible_knowledge_sources(c,env['target'],'authoring',now(),[UUID(active['version_id'])])
        assert not selected['eligible'] and 'PROJECT_FACT_UNKNOWN_OR_CONFLICT' in selected['excluded'][0]['reasons']
    Path(os.environ['ARCHITECT_KNOWLEDGE_OUT']).joinpath('candidate03-original-counterexample-regression.json').write_text(json.dumps({'original_counterexample_now_rejected':True,'matching_current_fact_value':3,'fact_evidence_ids':[],'fact_decision_heads':0,'knowledge_eligible':False,'paid_calls':0},indent=2)+'\n')
