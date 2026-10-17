"""真实PG/S3/LibreOffice控制面机制；不执行模型或Kafka消费者。"""

import copy
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from apps.worker.engine import acquire, finish, run_compute
from packages.authoring.build_repository import (
    DEFAULT_TEMPLATE_ID,
    configured_render_identity,
    freeze_build,
)
from packages.authoring.content import SCHEMA_DIGEST
from packages.authoring.repository import create_draft, save_draft
from packages.domain.build_followups import EVENT, follow_up_view, tick
from packages.domain.runtime import Command, connection, enqueue_plan, etag

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BUILD_FOLLOW_UP_INTEGRATION") != "1", reason="需隔离PG/S3/本地转换器"
)


@pytest.fixture
def context(monkeypatch):
    monkeypatch.delenv("PARSING_RUNTIME_TOKEN", raising=False)
    with connection() as c:
        actor, bid, project, round_id, bidder = [uuid4() for _ in range(5)]
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成构建接续')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成构建接续','{}',%s)",
            (bid, actor),
        )
        c.execute("INSERT INTO engineering_projects(id,title) VALUES(%s,'合成工程')", (project,))
        c.execute(
            "INSERT INTO bid_rounds(id,engineering_project_id,round_no) VALUES(%s,%s,1)",
            (round_id, project),
        )
        c.execute("INSERT INTO bidders(id,name) VALUES(%s,'合成主体')", (bidder,))
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
            (bid, round_id, bidder),
        )
        c.execute("INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)", (bid,))
        c.execute("INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)", (uuid4(), bid))
        draft, saved = create_draft(c, bid, actor, "合成接续正文")
        section = c.execute(
            "SELECT * FROM section_revisions WHERE draft_id=%s", (draft,)
        ).fetchone()
    return {"actor": actor, "bid": bid, "draft": draft, "saved": saved, "section": section}


def build(context, *, follow_up="review", formats=None, renderer=True):
    actor, bid, draft = (context[key] for key in ("actor", "bid", "draft"))
    body = {
        "draft_id": str(draft),
        "draft_revision": 1,
        "template_version_id": str(DEFAULT_TEMPLATE_ID),
        "formats": formats or ["docx", "pdf"],
        "follow_up": follow_up,
    }
    identity = configured_render_identity(body["formats"]) if renderer else None
    if renderer and "pdf" in body["formats"]:
        assert identity, "需真实转换器"
    with connection() as c:
        command = Command(c, actor, "test-build-follow-up", bid, uuid4().hex, body)
        input_id, manifest = freeze_build(c, bid, body, actor, identity)
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
        build_id = uuid4()
        c.execute(
            "INSERT INTO builds(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
            (build_id, bid, input_id, operation["id"]),
        )
        command.finish({"build_id": str(build_id)})
        message = c.execute(
            "SELECT payload FROM outbox WHERE operation_id=%s AND topic='tender.task.export.v1'",
            (operation["id"],),
        ).fetchone()["payload"]
    # 直接执行已提交Outbox协议而非Kafka；采用真实普通Worker fence/publish路径。
    lease = acquire(message)
    assert lease
    computed = run_compute(message, lease)
    assert finish(message, lease, computed)
    return build_id, manifest, computed


def edit(context):
    section = context["section"]
    content = copy.deepcopy(section["content"])
    content["content"][0]["content"] = [{"type": "text", "text": "生成后的新编辑不得被旧稿覆盖"}]
    with connection() as c:
        save_draft(
            c,
            context["bid"],
            context["draft"],
            context["actor"],
            {
                "base_draft_revision": 1,
                "changes": [
                    {
                        "section_id": str(section["section_id"]),
                        "base_section_revision": 1,
                        "schema_digest": SCHEMA_DIGEST,
                        "content": content,
                    }
                ],
            },
            etag("draft", context["draft"], 1),
        )


def test_real_pdf_adoption_is_atomic_once_and_records_request_not_fake_review(context):
    build_id, manifest, computed = build(context)
    assert all(output["state"] == "succeeded" for output in computed["outputs"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(tick, [build_id, build_id]))
    assert results[0] == results[1] and results[0]["state"] == "adopted"
    value = results[0]
    assert value["review_id"] is None and value["request_id"] and value["parse_operation_id"]
    with connection() as c:
        profile = c.execute(
            "SELECT p.*,b.material_revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s",
            (context["bid"],),
        ).fetchone()
        assert str(profile["current_body_id"]) == value["adopted_document_version_id"]
        assert profile["material_revision"] == 2 and profile["current_review_id"] is None
        assert (
            c.execute(
                "SELECT count(*) AS n FROM material_snapshots WHERE bid_id=%s", (context["bid"],)
            ).fetchone()["n"]
            == 1
        )
        intent = c.execute(
            "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (context["bid"],)
        ).fetchone()["pending_intent"]
        assert intent["trigger"] == "build_follow_up" and intent["state"] == "preparing"
        assert intent["request_id"] == value["request_id"]
        assert (
            c.execute(
                "SELECT count(*) AS n FROM audit_entries WHERE event_type=%s AND payload->>'build_id'=%s",
                (EVENT, str(build_id)),
            ).fetchone()["n"]
            == 1
        )
        assert follow_up_view(c, build_id, manifest) == value
        assert not c.execute(
            "SELECT 1 FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s",
            (context["bid"],),
        ).fetchone()


@pytest.mark.parametrize("change", ["draft", "heads", "historical_draft"])
def test_changed_inputs_keep_old_output_without_body_or_paid_request(context, change):
    if change == "historical_draft":
        with connection() as c:
            create_draft(c, context["bid"], context["actor"], "新当前草稿")
    build_id, _, _ = build(context)
    if change == "draft":
        edit(context)
    elif change == "heads":
        with connection() as c:
            c.execute(
                "UPDATE bids SET settings_revision=settings_revision+1 WHERE id=%s",
                (context["bid"],),
            )
    value = tick(build_id)
    assert value["state"] == "needs_selection" and value["request_id"] is None
    with connection() as c:
        assert (
            c.execute(
                "SELECT current_body_id FROM bid_profiles WHERE bid_id=%s", (context["bid"],)
            ).fetchone()["current_body_id"]
            is None
        )
        assert (
            c.execute(
                "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (context["bid"],)
            ).fetchone()["pending_intent"]
            is None
        )
        assert (
            c.execute(
                "SELECT count(*) AS n FROM build_outputs WHERE build_id=%s AND state='succeeded'",
                (build_id,),
            ).fetchone()["n"]
            == 2
        )


def test_word_success_does_not_hide_pdf_failure_or_create_review(context):
    build_id, _, computed = build(context, renderer=False)
    assert (
        next(row for row in computed["outputs"] if row["format"] == "docx")["state"] == "succeeded"
    )
    assert next(row for row in computed["outputs"] if row["format"] == "pdf")["state"] == "failed"
    value = tick(build_id)
    assert value["state"] == "failed" and value["request_id"] is None


def test_follow_up_none_stays_none_even_if_project_auto_review_enabled(context):
    with connection() as c:
        c.execute(
            "UPDATE bids SET settings=%s WHERE id=%s",
            (Jsonb({"auto_review": True}), context["bid"]),
        )
    build_id, _, _ = build(context, follow_up="none", formats=["docx"])
    value = tick(build_id)
    assert value["state"] == "not_requested" and not value["requested"]
    with connection() as c:
        assert not c.execute(
            "SELECT 1 FROM audit_entries WHERE event_type=%s AND payload->>'build_id'=%s",
            (EVENT, str(build_id)),
        ).fetchone()
