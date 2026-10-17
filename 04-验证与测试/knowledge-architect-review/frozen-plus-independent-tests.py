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
from packages.knowledge import bridge, repository
from packages.rag.indexing import ensure_parse_sources

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_KNOWLEDGE_INTEGRATION") != "1", reason="需要独立真实PG/S3受限API/Worker环境"
)


def new_bid(c, actor, bidder, title, region="浙江", kind="市政道路"):
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
        actor, bidder, other = uuid4(), uuid4(), uuid4()
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成知识维护成员')", (actor,))
        for ident, name in [(bidder, "合成本公司"), (other, "合成其他企业")]:
            c.execute("INSERT INTO bidders(id,name) VALUES(%s,%s)", (ident, name))
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
        assert len(selected['eligible'])==1
    Path(os.environ['ARCHITECT_KNOWLEDGE_OUT']).joinpath('unconfirmed-fact-counterexample.json').write_text(json.dumps({'reproduced':True,'matching_current_fact_value':3,'fact_evidence_ids':[],'fact_decision_heads':0,'knowledge_eligible':True,'paid_calls':0},indent=2)+'\n')
