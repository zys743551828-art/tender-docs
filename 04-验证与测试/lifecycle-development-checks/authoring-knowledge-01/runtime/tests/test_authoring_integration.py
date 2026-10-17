"""Real nonowner PG: immutable snapshots, concurrent CAS, idempotency and invalid provenance."""

import copy
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from packages.authoring.content import SCHEMA_DIGEST
from packages.authoring.patches import apply_proposal, create_proposal
from packages.authoring.repository import create_draft, draft_view, save_draft, snapshot_content
from packages.domain.runtime import Command, DomainError, assert_role, connection, etag, sha

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AUTHORING_INTEGRATION") != "1", reason="需独立真实PG非owner API账号"
)


@pytest.fixture
def draft():
    with connection() as c:
        actor, bid, project, round_id, bidder = [uuid4() for _ in range(5)]
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成章节测试成员')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成章节测试','{}',%s)",
            (bid, actor),
        )
        c.execute(
            "INSERT INTO engineering_projects(id,title) VALUES(%s,'合成章节工程')", (project,)
        )
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
            (round_id, project),
        )
        c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成投标主体')", (bidder,))
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
            (bid, round_id, bidder),
        )
        identity, saved = create_draft(c, bid, actor, "合成正文")
        section = c.execute(
            "SELECT * FROM section_revisions WHERE draft_id=%s", (identity,)
        ).fetchone()
    return actor, bid, identity, saved, section


def change(section, text):
    content = copy.deepcopy(section["content"])
    content["content"][0]["content"] = [{"type": "text", "text": text}]
    return {
        "base_draft_revision": 1,
        "changes": [
            {
                "section_id": str(section["section_id"]),
                "base_section_revision": 1,
                "schema_digest": SCHEMA_DIGEST,
                "content": content,
            }
        ],
    }


def test_real_nonowner_concurrent_cas_and_frozen_history(draft):
    assert_role()
    actor, bid, identity, saved, section = draft
    barrier = Barrier(2)

    def save(text):
        barrier.wait()
        try:
            with connection() as c:
                return save_draft(
                    c, bid, identity, actor, change(section, text), etag("draft", identity, 1)
                )
        except DomainError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["并发稿甲", "并发稿乙"]))
    assert sum(isinstance(r, dict) for r in results) == 1 and 412 in results
    with connection() as c:
        old = snapshot_content(c, bid, saved["snapshot_id"])
        assert old["sections"][0]["content"][0]["content"] == []
        view = draft_view(c, bid, identity)
        assert view["revision"] == 2
        assert (
            c.execute(
                "SELECT count(*) AS n FROM section_revisions WHERE draft_id=%s", (identity,)
            ).fetchone()["n"]
            == 2
        )


def test_real_command_replay_returns_original_ack_after_head_moved(draft):
    actor, bid, identity, _, section = draft
    body = change(section, "幂等正文")
    key = uuid4().hex
    condition = etag("draft", identity, 1)
    with connection() as c:
        command = Command(c, actor, "save-authoring-test", identity, key, body, condition)
        value = save_draft(c, bid, identity, actor, body, condition)
        command.finish(value)
    with connection() as c:
        command = Command(c, actor, "save-authoring-test", identity, key, body, condition)
        assert command.replay["body"]["snapshot_id"] == value["snapshot_id"]
        assert (
            c.execute(
                "SELECT count(*) AS n FROM section_revisions WHERE draft_id=%s", (identity,)
            ).fetchone()["n"]
            == 2
        )


def test_illegal_reference_rolls_back_revision_and_snapshot(draft):
    actor, bid, identity, _, section = draft
    body = change(section, "引用非法来源不得应用")
    body["changes"][0]["content"]["attrs"]["source_refs"] = [
        {
            "source_id": str(uuid4()),
            "source_revision": 1,
            "evidence_id": str(uuid4()),
            "document_version_id": str(uuid4()),
        }
    ]
    with pytest.raises(DomainError) as exc, connection() as c:
        save_draft(c, bid, identity, actor, body, etag("draft", identity, 1))
    assert exc.value.code == "AUTHORING_SOURCE_STALE"
    with connection() as c:
        assert draft_view(c, bid, identity)["revision"] == 1
        assert (
            c.execute(
                "SELECT count(*) AS n FROM section_revisions WHERE draft_id=%s", (identity,)
            ).fetchone()["n"]
            == 1
        )


def test_patch_apply_appends_revision_and_cannot_be_consumed_twice(draft):
    actor, bid, identity, _, section = draft
    node = section["content"]["content"][0]
    replacement = copy.deepcopy(node)
    replacement["content"] = [{"type": "text", "text": "人工明确确认的建议"}]
    with connection() as c:
        proposal = create_proposal(
            c, bid, section["section_id"], 1, node["attrs"]["id"], sha(node), replacement, []
        )
    with connection() as c:
        command = Command(
            c, actor, "apply-authoring-test", identity, uuid4().hex, {}, etag("draft", identity, 1)
        )
        result = apply_proposal(
            c, bid, proposal["action_id"], actor, command.id, etag("draft", identity, 1)
        )
        command.finish(result)
    assert result["draft_revision"] == 2
    with pytest.raises(DomainError) as exc, connection() as c:
        apply_proposal(c, bid, proposal["action_id"], actor, command.id, etag("draft", identity, 2))
    assert exc.value.code == "ACTION_CONSUMED"


def create_authoritative_source(c, bid, actor):
    from packages.rag.indexing import ensure_parse_sources

    blob, version, material, parse, evidence = [uuid4() for _ in range(5)]
    digest = sha(str(blob).encode())
    c.execute(
        "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
        (blob, digest, "synthetic-authoring/" + str(blob)),
    )
    c.execute(
        "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'synthetic.pdf',now(),'source_original',%s)",
        (version, bid, blob, actor),
    )
    c.execute(
        "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",
        (material, bid, sha({})),
    )
    c.execute(
        "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0)",
        (material, bid, version),
    )
    c.execute("UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s", (material, bid))
    c.execute(
        "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
        (parse, bid, version, sha(b"parser"), sha({})),
    )
    c.execute(
        "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,595,842,0,'usable','[]')",
        (bid, parse),
    )
    c.execute(
        "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text','合成施工要求',%s)",
        (evidence, bid, parse, sha("合成施工要求")),
    )
    ancestor, source = ensure_parse_sources(c, bid, parse, publish_readable=True)
    head = c.execute(
        "SELECT revision FROM retrieval.source_heads WHERE source_id=%s", (source,)
    ).fetchone()
    return {
        "source_id": str(source),
        "source_revision": head["revision"],
        "evidence_id": str(evidence),
        "document_version_id": str(version),
    }, ancestor


def test_real_withdrawn_ancestor_between_preview_and_apply_is_rejected(draft):
    actor, bid, identity, _, section = draft
    node = section["content"]["content"][0]
    replacement = copy.deepcopy(node)
    replacement["content"] = [{"type": "text", "text": "参考已发表来源的建议"}]
    with connection() as c:
        ref, ancestor = create_authoritative_source(c, bid, actor)
        proposal = create_proposal(
            c, bid, section["section_id"], 1, node["attrs"]["id"], sha(node), replacement, [ref]
        )
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (ancestor,),
        )
    with pytest.raises(DomainError) as exc, connection() as c:
        command = Command(c, actor, "apply-revoked-authoring-test", identity, uuid4().hex, {})
        apply_proposal(c, bid, proposal["action_id"], actor, command.id, etag("draft", identity, 1))
    assert exc.value.code == "AUTHORING_SOURCE_STALE"
    with connection() as c:
        assert draft_view(c, bid, identity)["revision"] == 1
        assert not c.execute(
            "SELECT 1 FROM action_consumptions WHERE action_id=%s", (proposal["action_id"],)
        ).fetchone()


def test_real_worker_build_uses_frozen_draft_and_publishes_independent_outputs(draft, monkeypatch):
    from packages.authoring.build_repository import (
        DEFAULT_TEMPLATE_ID,
        compute_prepared_build,
        configured_render_identity,
        freeze_build,
        prepare_build,
        publish_build,
    )
    from packages.domain.runtime import enqueue_plan

    actor, bid, identity, _, section = draft
    body = {
        "draft_id": str(identity),
        "draft_revision": 1,
        "template_version_id": str(DEFAULT_TEMPLATE_ID),
        "formats": ["docx", "pdf"],
        "follow_up": "none",
    }
    renderer = configured_render_identity(body["formats"])
    with connection() as c:
        command = Command(c, actor, "authoring-build-test", bid, uuid4().hex, body)
        input_id, manifest = freeze_build(c, bid, body, actor, renderer)
        operation = enqueue_plan(
            c,
            bid,
            command.id,
            "build",
            {"id": str(input_id), "manifest": manifest},
            stages=[{"key": "build-document", "depends_on": []}],
            handler_version="export/1",
            topic="tender.task.export.v1",
        )
        build = uuid4()
        c.execute(
            "INSERT INTO builds(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (build, bid, input_id, operation["id"]),
        )
        command.finish({"build_id": str(build)})
    # Editing after freeze must not alter this build.
    with connection() as c:
        save_draft(
            c,
            bid,
            identity,
            actor,
            change(section, "后续编辑不进入旧构建"),
            etag("draft", identity, 1),
        )
    assert_role(True)
    with connection(True) as c:
        prepared = prepare_build(c, bid, input_id)
        assert prepared["snapshot"]["sections"][0]["content"][0]["content"] == []
        assert not c.execute(
            "SELECT has_table_privilege(current_user,'design.commands','SELECT') AS allowed"
        ).fetchone()["allowed"]
    computed = compute_prepared_build(prepared)
    assert computed["outputs"][0]["state"] == "succeeded"
    with connection(True) as c:
        published = publish_build(c, bid, build, computed)
    with connection() as c:
        rows = c.execute("SELECT * FROM build_outputs WHERE build_id=%s", (build,)).fetchall()
        assert len(rows) == 2
        assert rows[0]["state"] in ("succeeded", "failed")
        docx = next(o for o in rows if o["format"] == "docx")
        assert docx["state"] == "succeeded" and docx["document_version_id"]
        pdf = next(o for o in rows if o["format"] == "pdf")
        if renderer:
            assert pdf["state"] == "succeeded", pdf["gaps"]
            relation = c.execute(
                "SELECT * FROM document_relations WHERE document_version_id=%s",
                (pdf["document_version_id"],),
            ).fetchone()
            assert relation["source_document_version_id"] == docx["document_version_id"]
            assert published["conversion_chain"]["pdf_digest"]
        assert (
            c.execute(
                "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (bid,)
            ).fetchone()["current_body_id"]
            is None
        )


def test_fact_head_change_invalidates_prior_patch(draft):
    actor, bid, identity, _, section = draft
    node = section["content"]["content"][0]
    replacement = copy.deepcopy(node)
    replacement["content"] = [{"type": "text", "text": "按当前事实生成的建议"}]
    with connection() as c:
        proposal = create_proposal(
            c, bid, section["section_id"], 1, node["attrs"]["id"], sha(node), replacement, []
        )
    with connection() as c:
        new_facts = uuid4()
        c.execute(
            "INSERT INTO fact_sets(id,bid_id,digest) VALUES(%s,%s,%s)",
            (new_facts, bid, sha({"synthetic": str(new_facts)})),
        )
        c.execute(
            "INSERT INTO fact_heads(bid_id,fact_set_id,revision) VALUES(%s,%s,1)", (bid, new_facts)
        )
    with pytest.raises(DomainError) as exc, connection() as c:
        command = Command(c, actor, "apply-stale-facts-authoring-test", identity, uuid4().hex, {})
        apply_proposal(c, bid, proposal["action_id"], actor, command.id, etag("draft", identity, 1))
    assert exc.value.code == "AUTHORING_CONTEXT_CHANGED"


def test_real_authenticated_http_save_replay_restore_and_build_freeze(draft):
    from fastapi.testclient import TestClient

    from apps.api.auth import password_hash
    from apps.api.authoring import router
    from apps.api.main import app
    from packages.authoring.build_repository import DEFAULT_TEMPLATE_ID
    from packages.domain.runtime import required, validate

    actor, bid, identity, _, section = draft
    login, password = "authoring-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (actor, login, password_hash(password)),
        )
    if not any(
        getattr(route, "path", "") == "/api/v1/authoring/capabilities" for route in app.routes
    ):
        app.include_router(router)
    with TestClient(app) as client:
        origin = required("APP_ORIGIN")
        signed_in = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert signed_in.status_code == 200
        headers = {
            "Origin": origin,
            "X-CSRF-Token": signed_in.json()["csrf_token"],
            "Idempotency-Key": uuid4().hex,
        }
        fetched = client.get(f"/api/v1/bids/{bid}/drafts/{identity}")
        validate("DraftView", fetched.json())
        body = change(section, "实际HTTP保存后冻结正文")
        path = f"/api/v1/bids/{bid}/drafts/{identity}"
        assert client.patch(path, json=body, headers=headers).status_code == 428
        headers["If-Match"] = fetched.headers["etag"]
        saved = client.patch(path, json=body, headers=headers)
        assert saved.status_code == 200, saved.json()
        validate("DraftSaved", saved.json())
        repeated = client.patch(path, json=body, headers=headers)
        assert repeated.status_code == 200 and repeated.headers["Idempotent-Replayed"] == "true"
        assert repeated.json() == saved.json()
        assert (
            client.patch(
                path, json=body, headers=headers | {"Idempotency-Key": uuid4().hex}
            ).status_code
            == 412
        )
        restored = client.post(
            f"/api/v1/bids/{bid}/sections/{section['section_id']}/restore",
            json={"revision": 1, "base_draft_revision": 2, "base_section_revision": 2},
            headers=headers | {"Idempotency-Key": uuid4().hex, "If-Match": saved.headers["etag"]},
        )
        assert restored.status_code == 200, restored.json()
        assert restored.json()["draft_revision"] == 3
        revisions = client.get(f"/api/v1/sections/{section['section_id']}/revisions?limit=2").json()
        assert len(revisions["items"]) == 2 and revisions["next_cursor"] == 2
        created_build = client.post(
            f"/api/v1/bids/{bid}/builds",
            json={
                "draft_id": str(identity),
                "draft_revision": 3,
                "template_version_id": str(DEFAULT_TEMPLATE_ID),
                "formats": ["docx"],
                "follow_up": "none",
            },
            headers=headers | {"Idempotency-Key": uuid4().hex},
        )
        assert created_build.status_code == 202, created_build.json()
        view = client.get("/api/v1/builds/" + created_build.json()["resource_id"])
        validate_actual_build(view.json())
        assert view.json()["outputs"][0]["state"] == "pending"
        assert view.json()["follow_up"]["state"] == "not_requested"


def test_generation_port_never_applies_text_and_rejects_unoffered_evidence(draft):
    from packages.authoring.generation import accept_generated_proposal, freeze_generation_plan

    actor, bid, identity, _, section = draft
    with connection() as c:
        frozen = freeze_generation_plan(
            c,
            bid,
            {
                "section_id": str(section["section_id"]),
                "base_section_revision": 1,
                "instruction": "提供一个改写建议",
                "source_version_ids": [],
            },
        )
    original = section["content"]["content"][0]
    replacement = copy.deepcopy(original)
    replacement["content"] = [{"type": "text", "text": "仅预览的模型建议"}]
    generated = {
        "node_id": original["attrs"]["id"],
        "before_digest": sha(original),
        "replacement": replacement,
        "evidence_ids": [str(uuid4())],
    }
    with pytest.raises(DomainError) as exc, connection() as c:
        accept_generated_proposal(c, bid, frozen["writing_plan_id"], generated)
    assert exc.value.code == "MODEL_CITATION_INVALID"
    generated["evidence_ids"] = []
    with connection() as c:
        proposal = accept_generated_proposal(c, bid, frozen["writing_plan_id"], generated)
        assert proposal["action_id"]
        assert draft_view(c, bid, identity)["revision"] == 1


def test_republished_ancestor_still_invalidates_prior_patch_identity(draft):
    actor, bid, identity, _, section = draft
    node = section["content"]["content"][0]
    replacement = copy.deepcopy(node)
    replacement["content"] = [{"type": "text", "text": "曾有有效来源的旧建议"}]
    with connection() as c:
        ref, ancestor = create_authoritative_source(c, bid, actor)
        proposal = create_proposal(
            c, bid, section["section_id"], 1, node["attrs"]["id"], sha(node), replacement, [ref]
        )
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=2 WHERE source_id=%s",
            (ancestor,),
        )
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='published',revision=3 WHERE source_id=%s",
            (ancestor,),
        )
    with pytest.raises(DomainError) as exc, connection() as c:
        command = Command(c, actor, "apply-republished-authoring-test", identity, uuid4().hex, {})
        apply_proposal(c, bid, proposal["action_id"], actor, command.id, etag("draft", identity, 1))
    assert exc.value.code == "AUTHORING_AUTHORITY_CHANGED"


def next_source_parse(c, bid, version):
    from packages.rag.indexing import ensure_parse_sources

    parse, evidence = uuid4(), uuid4()
    c.execute(
        "INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",
        (parse, bid, version, sha(b"next-parser"), sha(b"next-config")),
    )
    c.execute(
        "INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,595,842,0,'usable','[]')",
        (bid, parse),
    )
    c.execute(
        "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text','新的解析证据',%s)",
        (evidence, bid, parse, sha("新的解析证据")),
    )
    root, child = ensure_parse_sources(c, bid, parse, publish_readable=True)
    head = c.execute(
        "SELECT revision FROM retrieval.source_heads WHERE source_id=%s", (child,)
    ).fetchone()
    return parse, evidence, root, child, head["revision"]


def test_source_root_and_previous_parse_cannot_claim_latest_parse_evidence(draft):
    actor, bid, identity, _, section = draft
    with connection() as c:
        old_ref, _ = create_authoritative_source(c, bid, actor)
    with connection() as c:
        _, evidence, root, new_child, revision = next_source_parse(
            c, bid, old_ref["document_version_id"]
        )
    body = change(section, "引用最新解析的合法正文")
    valid_ref = {
        "source_id": str(new_child),
        "source_revision": revision,
        "evidence_id": str(evidence),
        "document_version_id": old_ref["document_version_id"],
    }
    for wrong_source in [old_ref["source_id"], str(root)]:
        wrong_ref = valid_ref | {"source_id": wrong_source}
        with connection() as c:
            wrong_ref["source_revision"] = c.execute(
                "SELECT revision FROM retrieval.source_heads WHERE source_id=%s", (wrong_source,)
            ).fetchone()["revision"]
        body["changes"][0]["content"]["attrs"]["source_refs"] = [wrong_ref]
        with pytest.raises(DomainError) as exc, connection() as c:
            save_draft(c, bid, identity, actor, body, etag("draft", identity, 1))
        assert exc.value.code == "AUTHORING_SOURCE_STALE"
        with connection() as c:
            assert draft_view(c, bid, identity)["revision"] == 1
    body["changes"][0]["content"]["attrs"]["source_refs"] = [valid_ref]
    with connection() as c:
        saved = save_draft(c, bid, identity, actor, body, etag("draft", identity, 1))
        assert saved["draft_revision"] == 2
        citation = c.execute(
            "SELECT source_id,evidence_id,purpose FROM retrieval.citations WHERE bid_id=%s", (bid,)
        ).fetchone()
        assert (
            str(citation["source_id"]) == str(new_child)
            and citation["evidence_id"] == evidence
            and citation["purpose"] == "authoring"
        )


def test_generation_uses_only_latest_parse_child_and_unique_251_evidence(draft):
    from packages.authoring.generation import freeze_generation_plan

    actor, bid, _, _, section = draft
    with connection() as c:
        ref, _ = create_authoritative_source(c, bid, actor)
    with connection() as c:
        parse, evidence, root, child, _ = next_source_parse(c, bid, ref["document_version_id"])
        for ordinal in range(1, 251):
            c.execute(
                "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,%s,'text','合成要求',%s)",
                (uuid4(), bid, parse, ordinal, sha("合成要求")),
            )
    request = {
        "section_id": str(section["section_id"]),
        "base_section_revision": 1,
        "instruction": "仅使用选中资料证据提出建议",
        "source_version_ids": [ref["document_version_id"]],
    }
    with connection() as c:
        prepared = freeze_generation_plan(c, bid, request)
    blocks = prepared["plan"]["source_blocks"]
    assert len(blocks) == 251 and len({b["evidence_id"] for b in blocks}) == 251
    assert {b["source_ref"]["source_id"] for b in blocks} == {str(child)}
    assert str(root) in {a["ancestor_id"] for a in prepared["plan"]["authority_manifest"]}
    assert str(evidence) in {b["evidence_id"] for b in blocks}
    # The documented limit counts unique evidence, including its exact boundary.
    with connection() as c:
        for ordinal in range(251, 500):
            c.execute(
                "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,%s,'text','合成要求',%s)",
                (uuid4(), bid, parse, ordinal, sha("合成要求")),
            )
    with connection() as c:
        boundary = freeze_generation_plan(c, bid, request)
        assert len(boundary["plan"]["source_blocks"]) == 500
    with connection() as c:
        c.execute(
            "INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,500,'text','第501个唯一证据',%s)",
            (uuid4(), bid, parse, sha("第501个唯一证据")),
        )
    with pytest.raises(DomainError) as exc, connection() as c:
        freeze_generation_plan(c, bid, request)
    assert exc.value.code == "AUTHORING_SOURCE_NOT_READY"


def test_follow_up_context_is_trusted_frozen_and_explicit_not_project_default(draft):
    from packages.authoring.build_repository import DEFAULT_TEMPLATE_ID, freeze_build

    actor, bid, identity, saved, section = draft
    body = {
        "draft_id": str(identity),
        "draft_revision": 1,
        "template_version_id": str(DEFAULT_TEMPLATE_ID),
        "formats": ["docx"],
        "follow_up": "review",
    }
    with connection() as c:
        _, manifest = freeze_build(c, bid, body, actor)
        context = manifest["follow_up_context"]
        assert context["enabled"] is True
        assert context["draft_id"] == str(identity) and context["draft_revision"] == 1
        assert context["draft_snapshot_id"] == saved["snapshot_id"]
        assert context["current_draft_id"] == str(identity)
        assert context["current_body_id"] is None and context["current_material_id"] is None
        assert context["material_revision"] == context["settings_revision"] == 1
        assert (
            context["current_rule_set_id"] is None
            and context["fact_set_id"] is None
            and context["fact_revision"] == 0
        )
    with connection() as c:
        c.execute(
            "UPDATE bids SET settings=settings||'{\"auto_review\":true}'::jsonb,settings_revision=settings_revision+1 WHERE id=%s",
            (bid,),
        )
        _, none_manifest = freeze_build(c, bid, body | {"follow_up": "none"}, actor)
        assert none_manifest["follow_up_context"]["enabled"] is False
        assert none_manifest["follow_up_context"]["settings_revision"] == 2
    assert context["settings_revision"] == 1
    with connection() as c:
        save_draft(
            c,
            bid,
            identity,
            actor,
            change(section, "编辑不回写冻结评审接续上下文"),
            etag("draft", identity, 1),
        )
    assert context["draft_revision"] == 1


def validate_actual_build(value):
    import json

    from jsonschema import Draft202012Validator, FormatChecker

    from packages.authoring.contracts import extend_openapi
    from packages.domain.runtime import ROOT

    actual = json.loads((ROOT / "packages/contracts/openapi/openapi.json").read_text())
    extend_openapi(actual)
    Draft202012Validator(
        {"$ref": "#/components/schemas/BuildView", "components": actual["components"]},
        format_checker=FormatChecker(),
    ).validate(value)


@pytest.mark.parametrize(
    "state", ["not_requested", "pending", "adopted", "needs_selection", "failed"]
)
def test_actual_build_http_projects_real_followup_state_and_no_invented_review(
    draft, monkeypatch, state
):
    from uuid import UUID

    from fastapi.testclient import TestClient

    from apps.api.auth import password_hash
    from apps.api.main import app
    from apps.worker.engine import acquire, finish, run_compute
    from packages.authoring.build_repository import DEFAULT_TEMPLATE_ID
    from packages.domain.build_followups import tick
    from packages.domain.runtime import required

    monkeypatch.delenv("PARSING_RUNTIME_TOKEN", raising=False)
    actor, bid, draft_id, _, section = draft
    login, password = "build-projection-" + uuid4().hex, uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (actor, login, password_hash(password)),
        )
        c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (bid,))
        c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (uuid4(), bid))
    with TestClient(app) as client:
        origin = required("APP_ORIGIN")
        signed = client.post(
            "/api/v1/auth/login",
            json={"login_name": login, "password": password},
            headers={"Origin": origin},
        )
        assert signed.status_code == 200
        formats = ["docx", "pdf"] if state in ["adopted", "needs_selection"] else ["docx"]
        created = client.post(
            f"/api/v1/bids/{bid}/builds",
            json={
                "draft_id": str(draft_id),
                "draft_revision": 1,
                "template_version_id": str(DEFAULT_TEMPLATE_ID),
                "formats": formats,
                "follow_up": "none" if state == "not_requested" else "review",
            },
            headers={
                "Origin": origin,
                "X-CSRF-Token": signed.json()["csrf_token"],
                "Idempotency-Key": uuid4().hex,
            },
        )
        assert created.status_code == 202, created.json()
        build_id = UUID(created.json()["resource_id"])
        if state in ["adopted", "needs_selection", "failed"]:
            with connection() as c:
                message = c.execute(
                    "SELECT o.payload FROM builds b JOIN outbox o ON o.operation_id=b.operation_id WHERE b.id=%s AND o.topic='tender.task.export.v1'",
                    (build_id,),
                ).fetchone()["payload"]
            lease = acquire(message)
            assert lease
            computed = run_compute(message, lease)
            assert finish(message, lease, computed)
            if state == "needs_selection":
                with connection() as c:
                    save_draft(
                        c,
                        bid,
                        draft_id,
                        actor,
                        change(section, "构建后编辑保持当前正文"),
                        etag("draft", draft_id, 1),
                    )
            assert tick(build_id)["state"] == state
        view = client.get(f"/api/v1/builds/{build_id}")
        assert view.status_code == 200
        value = view.json()
        validate_actual_build(value)
        follow = value["follow_up"]
        assert follow["state"] == state and follow["requested"] == (state != "not_requested")
        assert follow["review_id"] is None
        if state == "adopted":
            assert (
                follow["adopted_document_version_id"]
                and follow["parse_operation_id"]
                and follow["request_id"]
            )
            assert all(output["state"] == "succeeded" for output in value["outputs"])
        else:
            assert not any(
                follow[k]
                for k in [
                    "adopted_document_version_id",
                    "parse_operation_id",
                    "request_id",
                    "review_id",
                ]
            )
