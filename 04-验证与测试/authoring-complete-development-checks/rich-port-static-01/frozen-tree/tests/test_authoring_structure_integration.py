"""Fresh complete-A2 nonowner PG only; no model, Kafka or object I/O here."""

import copy
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
import test_authoring_integration as legacy

from packages.authoring import snapshot_structure, templates
from packages.authoring.content import SCHEMA_DIGEST
from packages.authoring.repository import freeze, latest_sections, save_draft, snapshot_content
from packages.domain.runtime import DomainError, assert_role, connection, etag

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AUTHORING_STRUCTURE_INTEGRATION") != "1",
    reason="Explicit fresh complete-A2 restricted PG profile required",
)


@pytest.fixture
def structured():
    actor, bid, draft, saved, section = legacy.draft.__wrapped__()
    with connection() as c:
        rows = latest_sections(c, bid, draft, include_deleted=True)
        state = snapshot_structure.reconcile(None, rows)
        c.execute("UPDATE drafts SET revision=2 WHERE id=%s", (draft,))
        result = freeze(c, bid, draft, 2, actor=actor, structure_state=state)
    return actor, bid, draft, saved, section, result


def test_frozen_extension_actual_worker_read_without_new_table_capability(structured):
    actor, bid, draft, old, section, result = structured
    assert_role()
    assert_role(True)
    with connection(True) as c:
        for name in (
            "authoring_structure_versions",
            "authoring_structure_nodes",
            "authoring_plan_versions",
            "authoring_template_families",
            "authoring_template_links",
        ):
            assert not c.execute(
                "SELECT has_table_privilege(current_user,%s,'SELECT') AS ok", ("design." + name,)
            ).fetchone()["ok"]
        value = snapshot_content(c, bid, UUID(result["snapshot_id"]))
        extension = value["authoring_structure"]
        assert (
            extension["directory_template"]["kind"] == "outline"
            and extension["style_template"]["kind"] == "layout"
        )
        assert extension["nodes"][0]["section_id"] == str(section["section_id"])
        assert snapshot_content(c, bid, UUID(old["snapshot_id"])).get("authoring_structure") is None


def test_legacy_text_save_carries_structure_and_exact_old_snapshot(structured):
    actor, bid, draft, old, section, result = structured
    with connection() as c:
        before = snapshot_content(c, bid, UUID(result["snapshot_id"]))
        body = copy.deepcopy(section["content"])
        body["content"][0]["content"] = [{"type": "text", "text": "结构注册后仍可人工保存"}]
        saved = save_draft(
            c,
            bid,
            draft,
            actor,
            {
                "base_draft_revision": 2,
                "changes": [
                    {
                        "section_id": str(section["section_id"]),
                        "base_section_revision": 1,
                        "schema_digest": SCHEMA_DIGEST,
                        "content": body,
                    }
                ],
            },
            etag("draft", draft, 2),
        )
        after = snapshot_content(c, bid, UUID(saved["snapshot_id"]))
        assert after["authoring_structure"]["nodes"][0]["section_revision"] == 2
        assert before == snapshot_content(c, bid, UUID(result["snapshot_id"]))
        assert (
            before["authoring_structure"]["directory_template"]
            == after["authoring_structure"]["directory_template"]
        )
        assert (
            before["authoring_structure"]["style_template"]
            == after["authoring_structure"]["style_template"]
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM authoring_structure_versions WHERE draft_id=%s", (draft,)
            ).fetchone()["n"]
            == 2
        )


def test_template_version_append_is_real_cas_and_old_config_immutable(structured):
    actor, *_ = structured
    with connection() as c:
        first = templates.create(
            c, actor, {"kind": "layout", "name": "合成企业版式", "config": templates.DEFAULT_STYLE}
        )
    barrier = Barrier(2)

    def append(size):
        barrier.wait()
        try:
            with connection() as c:
                return templates.append(
                    c,
                    UUID(first["family_id"]),
                    actor,
                    {
                        "base_revision": 1,
                        "name": "合成修订版式",
                        "config": templates.DEFAULT_STYLE | {"font_size_halfpoints": size},
                    },
                    templates.family_tag(first),
                )
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(append, [24, 26]))
    assert sum(isinstance(r, dict) for r in results) == 1 and 412 in results
    with connection() as c:
        assert templates.view(c, UUID(first["id"])) == first
        assert (
            c.execute(
                "SELECT count(*) AS n FROM authoring_template_links WHERE family_id=%s",
                (UUID(first["family_id"]),),
            ).fetchone()["n"]
            == 2
        )


def tree_action(c, actor, bid, draft, revision, changes):
    from packages.authoring.structure_repository import preview, resolve
    from packages.domain.runtime import Command, uid

    candidate = preview(
        c,
        bid,
        draft,
        actor,
        {"base_draft_revision": revision, "changes": changes},
        etag("draft", draft, revision),
    )
    cmd = Command(
        c,
        actor,
        "test-structure-confirm",
        UUID(candidate["action_id"]),
        str(uid()),
        {"decision": "confirm"},
        None,
    )
    result = resolve(
        c,
        bid,
        UUID(candidate["action_id"]),
        actor,
        cmd.id,
        "confirm",
        etag("draft", draft, revision),
    )
    cmd.finish(result)
    return candidate, result


def test_tree_add_copy_delete_restore_and_plan_are_real_revisions(structured):
    from packages.authoring.structure_repository import save_plan, view

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        candidate, added = tree_action(
            c,
            actor,
            bid,
            draft,
            2,
            [
                {
                    "op": "add",
                    "client_key": "child",
                    "title": "施工部署",
                    "node_type": "chapter",
                    "parent": {"section_id": str(section["section_id"])},
                    "ordinal": 0,
                }
            ],
        )
        child = UUID(candidate["created_ids"]["child"])
        assert added["draft_revision"] == 3
        planned = save_plan(
            c,
            bid,
            draft,
            child,
            actor,
            {
                "base_draft_revision": 3,
                "base_plan_revision": 0,
                "state": "ready",
                "goal": "人工编写目标",
                "key_points": ["部署"],
                "rule_ids": [],
                "source_refs": [],
                "pending_facts": ["工期尚待核实"],
                "planned_pages": 8,
                "budget_basis": "manual",
                "budget_rule_id": None,
                "budget_note": "估算",
            },
            etag("draft", draft, 3),
        )
        assert planned["plan"]["state"] == "ready"
        copied, result = tree_action(
            c,
            actor,
            bid,
            draft,
            4,
            [
                {
                    "op": "copy",
                    "section_id": str(child),
                    "client_key": "copy",
                    "parent": None,
                    "ordinal": 1,
                }
            ],
        )
        projected = view(c, bid, draft)
        copy_id = copied["created_ids"]["copy"]
        assert next(n for n in projected["nodes"] if n["section_id"] == copy_id)["plan"] is None
        deleted, result = tree_action(
            c, actor, bid, draft, 5, [{"op": "delete", "section_id": str(child)}]
        )
        assert deleted["deletion_impact"][0]["plan_version_id"] == planned["plan"]["id"]
        assert len(latest_sections(c, bid, draft)) == 2
        with pytest.raises(DomainError, match="计划目标"):
            save_plan(
                c, bid, draft, child, actor, {"base_draft_revision": 6}, etag("draft", draft, 6)
            )
        restored, result = tree_action(
            c,
            actor,
            bid,
            draft,
            6,
            [
                {
                    "op": "restore",
                    "section_id": str(child),
                    "parent": {"section_id": str(section["section_id"])},
                    "ordinal": 0,
                }
            ],
        )
        assert len(latest_sections(c, bid, draft)) == 3
        assert view(c, bid, draft)["page_budget"]["planned_total"] == 8
        assert (
            snapshot_content(c, bid, UUID(initial["snapshot_id"]))["authoring_structure"]["nodes"][
                0
            ]["section_revision"]
            == 1
        )


def test_directory_style_restore_and_consumption_are_immutable(structured):
    from packages.authoring.structure_repository import (
        directory_preview,
        resolve,
        restore_preview,
        style_preview,
        view,
    )
    from packages.domain.runtime import Command, uid

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        directory = templates.create(
            c,
            actor,
            {
                "kind": "outline",
                "name": "施工目录",
                "config": {
                    "schema_version": 1,
                    "nodes": [
                        {
                            "key": "root",
                            "parent_key": None,
                            "ordinal": 0,
                            "title": "总体部署",
                            "node_type": "chapter",
                        },
                        {
                            "key": "appendix",
                            "parent_key": None,
                            "ordinal": 1,
                            "title": "附录",
                            "node_type": "appendix",
                            "goal": "模板追加的建议",
                            "planned_pages": 2,
                        },
                    ],
                },
            },
        )
        candidate = directory_preview(
            c,
            bid,
            draft,
            actor,
            {
                "base_draft_revision": 2,
                "directory_template_version_id": directory["id"],
                "matches": [{"template_key": "root", "section_id": str(section["section_id"])}],
                "delete_section_ids": [],
            },
            etag("draft", draft, 2),
        )

        def accept(value, revision):
            cmd = Command(
                c, actor, "test-confirm-template", UUID(value["action_id"]), str(uid()), {}, None
            )
            result = resolve(
                c,
                bid,
                UUID(value["action_id"]),
                actor,
                cmd.id,
                "confirm",
                etag("draft", draft, revision),
            )
            cmd.finish(result)
            return result

        first = accept(candidate, 2)
        assert candidate["plan_suggestions"]
        assert (
            next(n for n in view(c, bid, draft)["nodes"] if n["template_key"] == "appendix")[
                "plan"
            ]["state"]
            == "not_started"
        )
        # Explicit mapping preserves the manually named source chapter.
        assert view(c, bid, draft)["nodes"][0]["title"] == section["content"]["attrs"]["title"]
        style = templates.create(
            c,
            actor,
            {
                "kind": "layout",
                "name": "页眉版式",
                "config": templates.DEFAULT_STYLE
                | {"header_text": "合成项目", "font_size_halfpoints": 24},
            },
        )
        candidate2 = style_preview(
            c,
            bid,
            draft,
            actor,
            {
                "base_draft_revision": 3,
                "style_template_version_id": style["id"],
                "format_bindings": [],
                "format_review_ack": None,
            },
            etag("draft", draft, 3),
        )
        accept(candidate2, 3)
        frozen = snapshot_content(c, bid, UUID(first["snapshot_id"]))
        assert frozen["authoring_structure"]["directory_template"]["id"] == directory["id"]
        assert frozen["authoring_structure"]["style_template"]["id"] != style["id"]
        restore = restore_preview(
            c,
            bid,
            draft,
            actor,
            {
                "base_draft_revision": 4,
                "structure_version_id": snapshot_content(c, bid, UUID(initial["snapshot_id"]))[
                    "authoring_structure"
                ]["id"],
            },
            etag("draft", draft, 4),
        )
        result = accept(restore, 4)
        current = view(c, bid, draft)
        assert sum(not n["deleted"] for n in current["nodes"]) == 1
        assert current["directory_template_version_id"] != directory["id"]
        # A new command/decision cannot consume the same Action again.
        cmd = Command(
            c, actor, "test-again-template", UUID(restore["action_id"]), str(uid()), {}, None
        )
        replay = resolve(c, bid, UUID(restore["action_id"]), actor, cmd.id, "reject", None)
        cmd.finish(replay)
        assert replay == result and current["draft_revision"] == 5


def test_structured_build_freezes_both_templates_and_real_ooxml(structured, monkeypatch):
    import io
    import zipfile
    from xml.etree.ElementTree import fromstring

    from packages.authoring.build_repository import (
        compute_prepared_build,
        freeze_build,
        prepare_build,
    )
    from packages.domain import storage

    actor, bid, draft, old, section, initial = structured
    objects = {}
    monkeypatch.setattr(storage, "put", lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, "get", objects.__getitem__)
    with connection() as c:
        ext = snapshot_content(c, bid, UUID(initial["snapshot_id"]))["authoring_structure"]
        request = {
            "draft_id": str(draft),
            "draft_revision": 2,
            "directory_template_version_id": ext["directory_template"]["id"],
            "style_template_version_id": ext["style_template"]["id"],
            "formats": ["docx", "pdf"],
            "follow_up": "none",
        }
        input_id, manifest = freeze_build(c, bid, request, actor, None)
        from packages.domain.runtime import Command, enqueue_plan, uid

        cmd = Command(c, actor, "test-structured-build", bid, str(uid()), request, None)
        operation = enqueue_plan(
            c,
            bid,
            cmd.id,
            "build",
            {"id": str(input_id), "manifest": manifest},
            stages=[{"key": "build-document", "depends_on": []}],
            handler_version="export/1",
            topic="tender.task.export.v1",
        )
        build_id = uid()
        c.execute(
            "INSERT INTO builds(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (build_id, bid, input_id, UUID(operation["id"])),
        )
        cmd.finish({"build_id": str(build_id)})
    with connection(True) as c:
        prepared = prepare_build(c, bid, input_id)
    # Word02 known safety blocker: absent approved profile yields independent failure.
    result = compute_prepared_build(prepared)
    word = next(o for o in result["outputs"] if o["format"] == "docx")
    pdf = next(o for o in result["outputs"] if o["format"] == "pdf")
    assert word["state"] == "succeeded" and pdf["state"] == "failed"
    assert "PAGE_CORRESPONDENCE_UNVERIFIED" in word["gaps"]
    assert manifest["actual_page_ranges"] is None
    from packages.authoring.build_repository import publish_build

    with connection(True) as c:
        published = publish_build(c, bid, build_id, result)
        assert "docx" in published["outputs"] and "pdf" not in published["outputs"]
        assert (
            c.execute(
                "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (bid,)
            ).fetchone()["current_body_id"]
            is None
        )
        states = c.execute(
            "SELECT format,state FROM build_outputs WHERE build_id=%s ORDER BY format", (build_id,)
        ).fetchall()
        assert [(row["format"], row["state"]) for row in states] == [
            ("docx", "succeeded"),
            ("pdf", "failed"),
        ]
    raw = objects[word["object_key"]]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = fromstring(archive.read("word/document.xml"))
        text = "".join(root.itertext())
        assert "目录" in text and "1 技术方案" in text
        assert "source_refs" not in text and "待核对" not in text
        assert b"fldSimple" not in archive.read("word/document.xml")
        assert b"fldChar" not in archive.read("word/document.xml")
        assert b"Noto Sans CJK SC" in archive.read("word/styles.xml")
        assert b'TargetMode="External"' not in archive.read("word/_rels/document.xml.rels")


def test_structure_http_csrf_ack_replay_and_stale_action(structured, monkeypatch):
    import hashlib
    from datetime import timedelta
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient

    from apps.api.authoring_structure import router
    from packages.domain.runtime import clean, now, secret_hash

    actor, bid, draft, old, section, initial = structured
    monkeypatch.setenv("APP_ORIGIN", "http://a2-explicit.test")
    token = uuid4().hex
    with connection() as c:
        c.execute(
            "INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)",
            (
                uuid4(),
                actor,
                secret_hash(token),
                hashlib.sha256(b"explicit-a2-csrf").hexdigest(),
                now() + timedelta(hours=1),
            ),
        )
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(DomainError)
    async def error(r, exc):
        return JSONResponse(
            {"code": exc.code, "details": clean(exc.details)}, status_code=exc.status
        )

    with TestClient(app) as api:
        base = f"/api/v1/bids/{bid}/drafts/{draft}"
        headers = {
            "Cookie": "session=" + token,
            "Origin": "http://a2-explicit.test",
            "X-CSRF-Token": "explicit-a2-csrf",
            "Idempotency-Key": uuid4().hex,
            "If-Match": etag("draft", draft, 2),
        }
        read = api.get(base + "/structure", headers={"Cookie": "session=" + token})
        assert read.status_code == 200 and read.headers["ETag"] == headers["If-Match"]
        body = {
            "base_draft_revision": 2,
            "changes": [
                {
                    "op": "rename",
                    "section_id": str(section["section_id"]),
                    "title": "第一次结构修改",
                }
            ],
        }
        denied = api.post(
            base + "/structure/preview",
            headers={k: v for k, v in headers.items() if k != "X-CSRF-Token"},
            json=body,
        )
        assert denied.status_code == 403
        first = api.post(base + "/structure/preview", headers=headers, json=body)
        assert first.status_code == 201
        replay = api.post(base + "/structure/preview", headers=headers, json=body)
        assert replay.json() == first.json() and replay.headers["Idempotent-Replayed"] == "true"
        second_headers = headers | {"Idempotency-Key": uuid4().hex}
        second = api.post(
            base + "/structure/preview",
            headers=second_headers,
            json=body
            | {
                "changes": [
                    {"op": "rename", "section_id": str(section["section_id"]), "title": "并行建议"}
                ]
            },
        )
        action = first.json()["action_id"]
        path = f"/api/v1/bids/{bid}/authoring-structure-actions/{action}/resolve"
        saved = api.post(
            path, headers=headers | {"Idempotency-Key": uuid4().hex}, json={"decision": "confirm"}
        )
        assert saved.status_code == 200 and saved.json()["draft_revision"] == 3
        # Global receipt wins over the later request's different decision/old ETag.
        receipt = api.post(
            path, headers=headers | {"Idempotency-Key": uuid4().hex}, json={"decision": "reject"}
        )
        assert receipt.json() == saved.json()
        stale = api.post(
            f"/api/v1/bids/{bid}/authoring-structure-actions/{second.json()['action_id']}/resolve",
            headers=headers | {"Idempotency-Key": uuid4().hex},
            json={"decision": "confirm"},
        )
        assert stale.status_code == 412
        assert api.get(base + "/structure", headers=headers).json()["draft_revision"] == 3


def test_plan_source_withdrawal_invalidates_pending_tree_action_and_worker_authority(structured):
    from packages.authoring.snapshot_structure import frozen_authority
    from packages.authoring.structure_repository import preview, resolve, save_plan
    from packages.domain.runtime import Command, uid

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        ref, ancestor = legacy.create_authoritative_source(c, bid, actor)
        planned = save_plan(
            c,
            bid,
            draft,
            section["section_id"],
            actor,
            {
                "base_draft_revision": 2,
                "base_plan_revision": 0,
                "state": "needs_basis",
                "goal": "有原页依据的计划",
                "key_points": [],
                "rule_ids": [],
                "source_refs": [ref],
                "pending_facts": [],
                "planned_pages": None,
                "budget_basis": "manual",
                "budget_rule_id": None,
                "budget_note": "",
            },
            etag("draft", draft, 2),
        )
        candidate = preview(
            c,
            bid,
            draft,
            actor,
            {
                "base_draft_revision": 3,
                "changes": [
                    {
                        "op": "rename",
                        "section_id": str(section["section_id"]),
                        "title": "计划引用守卫",
                    }
                ],
            },
            etag("draft", draft, 3),
        )
        frozen = snapshot_content(c, bid, UUID(planned["snapshot_id"]))
    with connection(True) as c:
        assert frozen_authority(c, bid, frozen["authoring_structure"], frozen["sections"])[
            "sources"
        ]
    with connection() as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (ancestor,),
        )
    with pytest.raises(DomainError) as error, connection() as c:
        cmd = Command(
            c, actor, "test-plan-revoked", UUID(candidate["action_id"]), str(uid()), {}, None
        )
        resolve(
            c, bid, UUID(candidate["action_id"]), actor, cmd.id, "confirm", etag("draft", draft, 3)
        )
    assert error.value.code == "AUTHORING_SOURCE_STALE"
    with pytest.raises(DomainError) as worker_error, connection(True) as c:
        frozen_authority(c, bid, frozen["authoring_structure"], frozen["sections"])
    assert worker_error.value.code == "AUTHORING_SOURCE_STALE"
    with connection() as c:
        assert snapshot_content(c, bid, UUID(planned["snapshot_id"])) == frozen
        assert not c.execute(
            "SELECT 1 FROM action_consumptions WHERE action_id=%s", (UUID(candidate["action_id"]),)
        ).fetchone()


def test_closed_project_blocks_new_tree_apply_but_allows_rejection_and_history(structured):
    from packages.authoring.structure_repository import preview, resolve, view
    from packages.domain.runtime import Command, uid

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        candidate = preview(
            c,
            bid,
            draft,
            actor,
            {
                "base_draft_revision": 2,
                "changes": [
                    {
                        "op": "rename",
                        "section_id": str(section["section_id"]),
                        "title": "关闭前建议",
                    }
                ],
            },
            etag("draft", draft, 2),
        )
        c.execute("UPDATE bid_profiles SET phase='terminated' WHERE bid_id=%s", (bid,))
    with pytest.raises(DomainError) as error, connection() as c:
        cmd = Command(
            c, actor, "test-closed-confirm", UUID(candidate["action_id"]), str(uid()), {}, None
        )
        resolve(
            c, bid, UUID(candidate["action_id"]), actor, cmd.id, "confirm", etag("draft", draft, 2)
        )
    assert error.value.code == "PROJECT_WORK_CLOSED"
    with connection() as c:
        cmd = Command(
            c, actor, "test-closed-reject", UUID(candidate["action_id"]), str(uid()), {}, None
        )
        result = resolve(c, bid, UUID(candidate["action_id"]), actor, cmd.id, "reject", None)
        cmd.finish(result)
        assert result["state"] == "rejected" and view(c, bid, draft)["draft_revision"] == 2


def assert_structure_schema(name, value):
    from fastapi import FastAPI
    from jsonschema import Draft202012Validator, FormatChecker

    from apps.api.authoring_structure import router
    from packages.authoring.structure_contracts import extend_openapi

    app = FastAPI()
    app.include_router(router)
    actual = extend_openapi(app.openapi())
    schema = {"$ref": "#/components/schemas/" + name, "components": actual["components"]}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def test_actual_structure_projections_and_cross_scope_are_strict(structured):
    from packages.authoring.structure_repository import history, save_plan, view

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        assert_structure_schema("AuthoringTreeView", view(c, bid, draft))
        assert_structure_schema("AuthoringStructureHistory", history(c, bid, draft))
        assert_structure_schema("AuthoringTemplateCatalog", templates.catalog(c))
        candidate, result = tree_action(
            c,
            actor,
            bid,
            draft,
            2,
            [{"op": "rename", "section_id": str(section["section_id"]), "title": "契约验证"}],
        )
        assert_structure_schema("AuthoringStructurePreview", candidate)
        assert_structure_schema("AuthoringStructureResult", result)
        saved = save_plan(
            c,
            bid,
            draft,
            section["section_id"],
            actor,
            {
                "base_draft_revision": 3,
                "base_plan_revision": 0,
                "state": "drafting",
                "goal": "真实计划",
                "key_points": [],
                "rule_ids": [],
                "source_refs": [],
                "pending_facts": [],
                "planned_pages": 3,
                "budget_basis": "manual",
                "budget_rule_id": None,
                "budget_note": "估算",
            },
            etag("draft", draft, 3),
        )
        assert_structure_schema("AuthoringPlanSaved", saved)
        assert_structure_schema("AuthoringTreeView", view(c, bid, draft))
        # Immutable snapshot has full plan content; no fake plan UUID placeholder.
        frozen = snapshot_content(c, bid, UUID(saved["snapshot_id"]))
        assert_structure_schema("AuthoringStructureExtension", frozen["authoring_structure"])
    actor2, bid2, draft2, saved2, section2 = legacy.draft.__wrapped__()
    with pytest.raises(DomainError) as error, connection() as c:
        view(c, bid2, draft)
    assert error.value.status == 404


def test_two_reviewed_tree_actions_have_one_real_concurrent_cas_winner(structured):
    from packages.authoring.structure_repository import preview, resolve
    from packages.domain.runtime import Command, uid

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        actions = [
            preview(
                c,
                bid,
                draft,
                actor,
                {
                    "base_draft_revision": 2,
                    "changes": [
                        {"op": "rename", "section_id": str(section["section_id"]), "title": title}
                    ],
                },
                etag("draft", draft, 2),
            )["action_id"]
            for title in ["结构并发甲", "结构并发乙"]
        ]
    barrier = Barrier(2)

    def confirm(action):
        barrier.wait()
        try:
            with connection() as c:
                cmd = Command(c, actor, "test-tree-concurrent", UUID(action), str(uid()), {}, None)
                value = resolve(
                    c, bid, UUID(action), actor, cmd.id, "confirm", etag("draft", draft, 2)
                )
                cmd.finish(value)
                return value
        except DomainError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, actions))
    assert sum(isinstance(value, dict) for value in results) == 1 and 412 in results
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM action_consumptions WHERE action_id=ANY(%s::uuid[])",
                (actions,),
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute("SELECT revision FROM drafts WHERE id=%s", (draft,)).fetchone()["revision"]
            == 3
        )


def test_deleted_section_rejects_old_save_generation_and_patch_and_order_bypass(structured):
    from packages.authoring.patches import create_proposal
    from packages.domain.runtime import sha

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        content = copy.deepcopy(section["content"])
        content["attrs"]["ordinal"] = 99
        with pytest.raises(DomainError) as bad_order:
            save_draft(
                c,
                bid,
                draft,
                actor,
                {
                    "base_draft_revision": 2,
                    "changes": [
                        {
                            "section_id": str(section["section_id"]),
                            "base_section_revision": 1,
                            "schema_digest": SCHEMA_DIGEST,
                            "content": content,
                        }
                    ],
                },
                etag("draft", draft, 2),
            )
        assert bad_order.value.code == "AUTHORING_TREE_ORDER_REQUIRED"
        tree_action(
            c, actor, bid, draft, 2, [{"op": "delete", "section_id": str(section["section_id"])}]
        )
    with pytest.raises(DomainError) as error, connection() as c:
        save_draft(
            c,
            bid,
            draft,
            actor,
            {
                "base_draft_revision": 3,
                "changes": [
                    {
                        "section_id": str(section["section_id"]),
                        "base_section_revision": 1,
                        "schema_digest": SCHEMA_DIGEST,
                        "content": section["content"],
                    }
                ],
            },
            etag("draft", draft, 3),
        )
    assert error.value.code == "AUTHORING_SECTION_DELETED"
    paragraph = section["content"]["content"][0]
    with pytest.raises(DomainError) as patch_error, connection() as c:
        create_proposal(
            c,
            bid,
            section["section_id"],
            1,
            paragraph["attrs"]["id"],
            sha(paragraph),
            paragraph,
            [],
        )
    assert patch_error.value.code == "AUTHORING_SECTION_DELETED"
    from packages.authoring.generation import freeze_generation_plan

    with pytest.raises(DomainError) as generation_error, connection() as c:
        freeze_generation_plan(
            c,
            bid,
            {
                "section_id": str(section["section_id"]),
                "base_section_revision": 1,
                "instruction": "删除章节不得生成",
                "source_version_ids": [],
            },
        )
    assert generation_error.value.code == "AUTHORING_SECTION_DELETED"
    with connection() as c:
        assert (
            snapshot_content(c, bid, UUID(initial["snapshot_id"]))["sections"][0]
            == section["content"]
        )


def test_project_format_binding_requires_current_confirmation_and_overrides_enterprise(structured):
    from psycopg.types.json import Jsonb

    from packages.authoring.build_repository import freeze_build
    from packages.authoring.snapshot_structure import frozen_authority
    from packages.authoring.structure_repository import resolve, style_preview
    from packages.domain.fact_sets import evidence_basis
    from packages.domain.human_decisions import append_decision
    from packages.domain.runtime import Command, sha, uid

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        ref, ancestor = legacy.create_authoritative_source(c, bid, actor)
        material = c.execute(
            "SELECT current_material_id FROM bid_profiles WHERE bid_id=%s", (bid,)
        ).fetchone()["current_material_id"]
        rule_set, rule = uid(), uid()
        c.execute(
            "INSERT INTO rule_sets(id,bid_id,material_id,revision,digest,coverage) VALUES(%s,%s,%s,1,%s,'complete')",
            (rule_set, bid, material, sha(str(rule_set))),
        )
        c.execute(
            "INSERT INTO rule_items(id,bid_id,rule_set_id,kind,statement,conditions,exceptions,max_points,evidence_ids,bands,applicability_scope) VALUES(%s,%s,%s,'mandatory','合成人工格式绑定要求','[]','[]',NULL,%s,'[]',%s)",
            (
                rule,
                bid,
                rule_set,
                [UUID(ref["evidence_id"])],
                Jsonb({"bid_id": str(bid), "description": "显式机制夹具，非真实语义效果"}),
            ),
        )
        c.execute("UPDATE bid_profiles SET current_rule_set_id=%s WHERE bid_id=%s", (rule_set, bid))
        ext = snapshot_content(c, bid, UUID(initial["snapshot_id"]))["authoring_structure"]
        binding = {
            "rule_id": str(rule),
            "field": "font_size_halfpoints",
            "value": 24,
            "source_refs": [ref],
            "reason": "显式人工字段绑定机制，不自动证明原文语义",
        }
        body = {
            "base_draft_revision": 2,
            "style_template_version_id": ext["style_template"]["id"],
            "format_bindings": [binding],
            "format_review_ack": None,
        }
        with pytest.raises(DomainError) as missing_decision:
            style_preview(c, bid, draft, actor, body, etag("draft", draft, 2))
        assert missing_decision.value.code == "AUTHORING_FORMAT_RULE_UNCONFIRMED"
        append_decision(
            c,
            bid,
            actor,
            "rule",
            rule,
            {
                "decision": "confirmed",
                "reason": "合成人工核对",
                "evidence_ids": [ref["evidence_id"]],
            },
            basis=evidence_basis(c, bid, [UUID(ref["evidence_id"])], publish=False),
        )
        report = style_preview(c, bid, draft, actor, body, etag("draft", draft, 2))
        assert report["state"] == "requires_format_review" and report["action_id"] is None
        assert report["format_report"]["conflicts"][0]["resolution"] == "project_requirement_wins"
        candidate = style_preview(
            c,
            bid,
            draft,
            actor,
            body | {"format_review_ack": report["format_report"]["review_digest"]},
            etag("draft", draft, 2),
        )
        cmd = Command(
            c, actor, "test-confirm-format", UUID(candidate["action_id"]), str(uid()), {}, None
        )
        saved = resolve(
            c, bid, UUID(candidate["action_id"]), actor, cmd.id, "confirm", etag("draft", draft, 2)
        )
        cmd.finish(saved)
        frozen = snapshot_content(c, bid, UUID(saved["snapshot_id"]))
        assert (
            frozen["authoring_structure"]["metadata"]["effective_style"]["font_size_halfpoints"]
            == 24
        )
        request = {
            "draft_id": str(draft),
            "draft_revision": 3,
            "directory_template_version_id": ext["directory_template"]["id"],
            "style_template_version_id": ext["style_template"]["id"],
            "formats": ["docx"],
            "follow_up": "none",
        }
        freeze_build(c, bid, request, actor)
    with connection(True) as c:
        assert (
            frozen_authority(c, bid, frozen["authoring_structure"], frozen["sections"])["format"][
                "effective_config"
            ]["font_size_halfpoints"]
            == 24
        )
    with connection() as c:
        append_decision(
            c,
            bid,
            actor,
            "rule",
            rule,
            {
                "decision": "rejected",
                "reason": "合成人工变更决定",
                "evidence_ids": [ref["evidence_id"]],
            },
        )
    with pytest.raises(DomainError) as stale, connection(True) as c:
        frozen_authority(c, bid, frozen["authoring_structure"], frozen["sections"])
    assert stale.value.code == "AUTHORING_FORMAT_RULE_UNCONFIRMED"


def test_create_draft_from_real_directory_keeps_keys_and_resets_plan_approval(structured):
    from packages.authoring.repository import create_draft
    from packages.authoring.structure_repository import view

    actor, bid, draft, old, section, initial = structured
    with connection() as c:
        directory = templates.create(
            c,
            actor,
            {
                "name": "带计划目录",
                "kind": "outline",
                "config": {
                    "schema_version": 1,
                    "nodes": [
                        {
                            "key": "root",
                            "parent_key": None,
                            "ordinal": 0,
                            "title": "技术部署",
                            "node_type": "chapter",
                            "goal": "模板提供的建议目标",
                            "planned_pages": 5,
                        },
                        {
                            "key": "child",
                            "parent_key": "root",
                            "ordinal": 0,
                            "title": "进度",
                            "node_type": "chapter",
                        },
                    ],
                },
            },
        )
        new_draft, saved = create_draft(c, bid, actor, "从双模板目录创建", UUID(directory["id"]))
        projected = view(c, bid, new_draft)
        assert len(projected["nodes"]) == 2
        root, child = projected["nodes"]
        assert root["template_key"] == "root" and child["parent_id"] == root["section_id"]
        assert root["plan"]["state"] == "not_started" and root["plan"]["planned_pages"] == 5
        assert (
            root["plan"]["payload"]["source_refs"] == []
            and root["plan"]["payload"]["rule_ids"] == []
        )
        assert root["section_id"] not in {str(section["section_id"]), str(draft)}
        assert projected["page_budget"]["planned_total"] == 5
        assert_structure_schema("AuthoringTreeView", projected)
        assert_structure_schema(
            "AuthoringStructureExtension",
            snapshot_content(c, bid, UUID(saved["snapshot_id"]))["authoring_structure"],
        )
