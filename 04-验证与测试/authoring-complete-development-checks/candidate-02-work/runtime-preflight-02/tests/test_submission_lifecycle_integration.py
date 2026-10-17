"""实交事实/更正不得隐式重开已结束/归档项目；真实受限PG领域事务。"""

import os
from uuid import uuid4

import pytest

from packages.domain.runtime import connection, sha
from packages.domain.submissions import register
from packages.identity.repository import create_association

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SUBMISSION_LIFECYCLE_INTEGRATION") != "1", reason="需要专用真实PG"
)


@pytest.mark.parametrize(
    "phase,archived",
    [
        ("closed", False),
        ("terminated", False),
        ("working", True),
        ("preparation", False),
        ("working", False),
    ],
)
def test_offline_fact_and_explicit_correction_preserve_terminal_or_archived_state(phase, archived):
    actor, bid, version, blob = [uuid4() for _ in range(4)]
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成实交阶段保持')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成实交阶段保持','{}',%s)",
            (bid, actor),
        )
        ids = create_association(c, {"title": "合成实交阶段保持"})
        archived_at = (
            c.execute("SELECT now()-interval '1 hour' AS at").fetchone()["at"] if archived else None
        )
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase,archived_at) VALUES(%s,%s,%s,%s,%s)",
            (bid, ids["round_id"], ids["bidder_id"], phase, archived_at),
        )
        digest = sha(str(blob))
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,10,'application/pdf')",
            (blob, digest, "blobs/" + digest),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'合成已收稿.pdf',now(),'bid_body',%s)",
            (version, bid, blob, actor),
        )
    latest = None
    for index in range(2):
        with connection() as c:
            c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,)).fetchone()
            command = uuid4()
            c.execute(
                "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'lifecycle_submission_probe',%s,%s,%s)",
                (command, actor, bid, uuid4().hex, sha(str(command))),
            )
            at = (
                c.execute("SELECT clock_timestamp()-interval '2 hours' AS at")
                .fetchone()["at"]
                .isoformat()
            )
            latest = register(
                c,
                bid,
                actor,
                command,
                {
                    "actual_document_version_id": str(version),
                    "submitted_at": at,
                    "evidence_ref": str(version),
                    "explanation": "合成已发生提交事实，未授予事前通过或平台真实性",
                },
                kind="offline_fact",
                supersedes=latest,
                correction_reason="合成明确更正" if index else None,
            )
            current = c.execute(
                "SELECT phase,archived_at FROM bid_profiles WHERE bid_id=%s", (bid,)
            ).fetchone()
            assert current["phase"] == (
                phase if phase in ("closed", "terminated") or archived else "submitted"
            )
            assert current["archived_at"] == archived_at
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM submission_records WHERE bid_id=%s", (bid,)
            ).fetchone()["n"]
            == 2
        )
        assert (
            c.execute(
                "SELECT submission_id FROM submission_heads WHERE bid_id=%s", (bid,)
            ).fetchone()["submission_id"]
            == latest
        )
