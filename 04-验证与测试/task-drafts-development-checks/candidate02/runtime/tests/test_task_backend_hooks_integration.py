"""Shared07真实登记/两冻结入口守卫，不以Owner测试的临时registry代替接线。"""

import os

import pytest
from psycopg.types.json import Jsonb

from tests.test_task_templates_integration import world as world

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TASK_BACKEND_HOOKS") != "1", reason="专用PG无费执行"
)


@pytest.mark.parametrize("entry", ["advance", "freeze"])
def test_actual_registered_task_guard_prevents_shared_auto_rebase(world, entry):  # noqa: F811
    from apps.api.main import app
    from packages.domain import reviews
    from packages.domain.runtime import connection
    from packages.tasks import inputs
    from tests.test_task_runs_integration import project, run

    assert app is not None and reviews.TASK_INPUT_GUARD is inputs.review_guard
    client, bids, actors, headers, create = world
    with connection() as c:
        project(c, bids[0], actors[0])
    definition = create(client).json()
    queued = run(client, bids[0], definition, headers)
    assert queued.status_code == 202
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bids[0],))
        c.execute(
            "UPDATE bids SET material_revision=material_revision+1,settings=%s WHERE id=%s",
            (Jsonb({"auto_review": True}), bids[0]),
        )
        bid, slot = reviews._locked(c, bids[0])
        old_revision = slot["pending_intent"]["material_revision"]
        if entry == "advance":
            assert reviews._advance_head(c, bid, slot["pending_intent"])
        else:
            assert reviews.freeze_candidate(c, bids[0]) is None
        actual = c.execute(
            "SELECT pending_intent FROM review_slots WHERE bid_id=%s", (bids[0],)
        ).fetchone()["pending_intent"]
        assert actual["state"] == "superseded" and actual["error_code"] == "TASK_INPUT_SUPERSEDED"
        assert actual["material_revision"] == old_revision
        assert (
            c.execute(
                "SELECT count(*) AS n FROM review_runs WHERE bid_id=%s", (bids[0],)
            ).fetchone()["n"]
            == 0
        )
