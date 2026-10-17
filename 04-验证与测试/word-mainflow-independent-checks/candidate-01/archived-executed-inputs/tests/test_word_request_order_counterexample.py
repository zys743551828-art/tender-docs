"""开发反例：事务开始时间不是锁后实际受理顺序；合成原件/无转换或模型。"""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from packages.domain.runtime import Command, connection
from packages.word_intake.repository import queue_render
from tests.test_word_intake import make_docx
from tests.test_word_intake_integration import disabled, original
from tests.test_word_intake_integration import world as world

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WORD_ORDER_COUNTEREXAMPLE") != "1", reason="仅fresh合成PG反例"
)


def test_request_order_after_bid_lock(world):  # noqa: F811
    from apps.worker import engine
    from tests.test_word_intake_integration import message_for

    def pump(operation):
        message = message_for(operation)
        lease = engine.acquire(message)
        assert lease is not None
        result = engine.run_compute(message, lease)
        assert engine.finish(message, lease, result)

    word = original(world, make_docx("Request order fixture " + uuid4().hex), profile=disabled())
    pump(word["published"]["conversion_operation_id"])
    with connection() as older_transaction:
        older_transaction.execute("SELECT now()").fetchone()
        with connection() as earlier_request:
            earlier_request.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (world["bid"],))
            cmd = Command(
                earlier_request,
                world["actor"],
                "synthetic_word_order",
                word["version"],
                uuid4().hex,
                {},
            )
            before = queue_render(
                earlier_request, world["bid"], word["version"], cmd.id, disabled()
            )
            cmd.finish({"operation_id": before["id"]})
        pump(before["id"])
        older_transaction.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (world["bid"],))
        cmd = Command(
            older_transaction,
            world["actor"],
            "synthetic_word_order",
            word["version"],
            uuid4().hex,
            {},
        )
        after = queue_render(older_transaction, world["bid"], word["version"], cmd.id, disabled())
        cmd.finish({"operation_id": after["id"]})
    with connection() as c:
        latest = c.execute(
            "SELECT operation_id FROM word_render_runs WHERE bid_id=%s AND source_document_version_id=%s ORDER BY created_at DESC,operation_id DESC LIMIT 1",
            (world["bid"], word["version"]),
        ).fetchone()["operation_id"]
    result = {
        "scope": "developer nonowner PG counterexample; 0 model/render/storage network",
        "accepted_order": [before["id"], after["id"]],
        "expected_latest": after["id"],
        "observed_latest": str(latest),
        "latest_matches": str(latest) == after["id"],
    }
    path = Path(
        os.environ.get(
            "WORD_ORDER_RESULT_PATH", "docs/word-request-order-development-checks/fixed-result.json"
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    assert result["latest_matches"]


def test_api_cannot_set_server_accepted_timestamp(world):  # noqa: F811
    import psycopg
    from psycopg.types.json import Jsonb

    word = original(
        world, make_docx("Server timestamp restricted columns " + uuid4().hex), profile=disabled()
    )
    operation = word["published"]["conversion_operation_id"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection() as c:
            c.execute(
                "INSERT INTO word_render_runs(operation_id,bid_id,source_document_version_id,profile_digest,frozen_profile,created_at) VALUES(%s,%s,%s,%s,%s,clock_timestamp())",
                (
                    operation,
                    world["bid"],
                    word["version"],
                    disabled()["profile_digest"],
                    Jsonb(disabled()),
                ),
            )


@pytest.mark.skipif(
    os.environ.get("RUN_WORD_BRIDGE_REAL_RENDER") != "1",
    reason="原生固定Docker旧成功PDF与新失败机制",
)
def test_latest_pending_and_failed_never_reuse_old_qualified_pdf(world, monkeypatch):  # noqa: F811
    from apps.worker import engine
    from packages.word_intake.renderer import configured_profile
    from packages.word_intake.repository import completed_pdf, word_view
    from tests.test_word_intake_integration import message_for
    from tests.test_word_mainflow_integration import enable_real_ports

    enable_real_ports(monkeypatch)
    word = original(
        world,
        make_docx("旧受控成功不代表新一轮转换已成功。" + uuid4().hex),
        profile=configured_profile(),
    )
    message = message_for(word["published"]["conversion_operation_id"])
    lease = engine.acquire(message)
    computed = engine.run_compute(message, lease)
    assert computed["outcome"]["coverage"] == "complete"
    assert engine.finish(message, lease, computed)
    with connection() as c:
        before = completed_pdf(c, world["bid"], word["version"])
        assert before is not None
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (world["bid"],))
        cmd = Command(
            c,
            world["actor"],
            "synthetic_word_new_conversion",
            word["version"],
            uuid4().hex,
            {"reason": "验证最新失败不使用旧成功"},
        )
        new = queue_render(c, world["bid"], word["version"], cmd.id, disabled())
        cmd.finish({"operation_id": new["id"]})
    with connection() as c:
        assert completed_pdf(c, world["bid"], word["version"]) is None
    message = message_for(new["id"])
    lease = engine.acquire(message)
    computed = engine.run_compute(message, lease)
    assert computed["outcome"]["state"] == "failed"
    assert engine.finish(message, lease, computed)
    with connection() as c:
        assert completed_pdf(c, world["bid"], word["version"]) is None
        view = word_view(c, world["bid"], word["version"])
        assert (
            view["conversion"]["state"] == "failed"
            and not view["conversion"]["controlled_render_verified"]
        )
        assert c.execute(
            "SELECT 1 FROM document_versions WHERE id=%s", (before["document_version_id"],)
        ).fetchone()
