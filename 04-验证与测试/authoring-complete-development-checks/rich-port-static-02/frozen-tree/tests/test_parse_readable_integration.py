"""受限PG真实fenced发布协议；解析产物为显式夹具，不作为解析质量或向量效果验收。"""

import os
from uuid import uuid4

import pytest

from apps.worker import engine
from packages.domain.runtime import DomainError, connection, enqueue_plan, sha
from packages.identity.repository import create_association
from packages.knowledge.repository import create_candidate, source_positions
from packages.rag.indexing import original_source_id, parse_source_id

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PARSE_READABLE_INTEGRATION") != "1", reason="需隔离非owner PG"
)


def scheduled(c, actor, bid, version, *, parser, coverage="complete", text="本工程工法原页文字"):
    command, payload = uuid4(), uuid4()
    c.execute(
        "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'parse_readable_probe',%s,%s,%s)",
        (command, actor, bid, uuid4().hex, sha(str(payload))),
    )
    operation = enqueue_plan(
        c,
        bid,
        command,
        "parse-materials",
        {"id": payload},
        stages=[{"key": "parse-materials", "depends_on": []}],
        handler_version="document/2",
        topic="tender.task.document.v1",
    )
    message = c.execute(
        "SELECT payload FROM outbox WHERE operation_id=%s LIMIT 1", (operation["id"],)
    ).fetchone()["payload"]
    result = [
        {
            "doc": {"id": str(version), "bid_id": str(bid)},
            "parser_digest": sha(parser),
            "config_digest": sha({}),
            "coverage": coverage,
            "gaps": [] if coverage == "complete" else ["EXPLICIT_PARTIAL_FIXTURE"],
            "pages": [
                {
                    "physical_page": 1,
                    "width": 595,
                    "height": 842,
                    "rotation": 0,
                    "quality": "usable"
                    if coverage == "complete"
                    else "partial"
                    if text
                    else "failed",
                    "gaps": [],
                    "blocks": [
                        {
                            "ordinal": 0,
                            "kind": "text",
                            "text_content": text,
                            "digest": sha(text),
                            "bbox": [1, 2, 200, 30],
                        }
                    ]
                    if text
                    else [],
                }
            ],
        }
    ]
    return message, result


def test_readable_sources_publish_with_parse_before_any_vector_index_and_withdrawal_is_not_revived():
    actor, bid, version, blob = [uuid4() for _ in range(4)]
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成可读来源协议')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'合成解析发布','{}',%s)",
            (bid, actor),
        )
        ids = create_association(c, {"title": "合成解析发布"})
        c.execute(
            "INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",
            (bid, ids["round_id"], ids["bidder_id"]),
        )
        c.execute("INSERT INTO stream_heads(id,bid_id,last_seq) VALUES(%s,%s,0)", (uuid4(), bid))
        digest = sha(str(blob))
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,18,'application/pdf')",
            (blob, digest, "blobs/" + digest),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'显式原页夹具.pdf',now(),'source_original',%s)",
            (version, bid, blob, actor),
        )
        message, outcome = scheduled(c, actor, bid, version, parser="readable/1")
    lease = engine.acquire(message)
    assert lease and engine.finish(message, lease, outcome)
    with connection() as c:
        parse = c.execute(
            "SELECT id FROM parse_runs WHERE document_version_id=%s", (version,)
        ).fetchone()["id"]
        heads = c.execute(
            "SELECT source_id,status,revision FROM retrieval.source_heads WHERE source_id=ANY(%s)",
            ([original_source_id(version), parse_source_id(parse)],),
        ).fetchall()
        assert len(heads) == 2 and all(head["status"] == "published" for head in heads)
        assert (
            c.execute(
                "SELECT count(*) AS n FROM retrieval.chunks WHERE parse_id=%s", (parse,)
            ).fetchone()["n"]
            == 0
        )
        knowledge = create_candidate(
            c,
            actor,
            {
                "title": "原页可读立即选择",
                "content": "待完善工法",
                "scope": "本项目",
                "source_version_ids": [str(version)],
            },
        )
        selected = source_positions(c, knowledge["id"], version, 1, -1, 30)
        assert (
            selected["readiness"] == "readable_text_positions_only" and len(selected["items"]) == 1
        )
        assert selected["items"][0]["reference"]["source_id"] == str(parse_source_id(parse))
        # 同一已完成解析身份重复发布不会增加head修订，不等向量模型服务。
        repeated, repeated_outcome = scheduled(c, actor, bid, version, parser="readable/1")
    repeated_lease = engine.acquire(repeated)
    assert engine.finish(repeated, repeated_lease, repeated_outcome)
    with connection() as c:
        assert c.execute(
            "SELECT revision FROM retrieval.source_heads WHERE source_id=%s",
            (parse_source_id(parse),),
        ).fetchone()["revision"] == next(
            head["revision"] for head in heads if head["source_id"] == parse_source_id(parse)
        )
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (original_source_id(version),),
        )
        stale, stale_outcome = scheduled(c, actor, bid, version, parser="readable/2")
    stale_lease = engine.acquire(stale)
    with pytest.raises(DomainError) as error:
        engine.finish(stale, stale_lease, stale_outcome)
    assert error.value.code == "PARSE_SOURCE_AUTHORITY_CHANGED"
    assert engine.finish(stale, stale_lease, None, error.value)
    with connection() as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM parse_runs WHERE document_version_id=%s", (version,)
            ).fetchone()["n"]
            == 1
        )
        assert (
            c.execute(
                "SELECT status FROM retrieval.source_heads WHERE source_id=%s",
                (original_source_id(version),),
            ).fetchone()["status"]
            == "withdrawn"
        )


@pytest.mark.parametrize(
    "coverage,text,expected",
    [("partial", "真实可读部分夹具", "published"), ("failed", "", None)],
)
def test_partial_with_real_text_is_readable_but_failed_empty_parse_is_not_published(
    coverage, text, expected
):
    actor, bid, version, blob = [uuid4() for _ in range(4)]
    with connection() as c:
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,'合成解析缺口')", (actor,))
        c.execute(
            "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'解析缺口原页','{}',%s)",
            (bid, actor),
        )
        digest = sha(str(blob))
        c.execute(
            "INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,0,'application/pdf')",
            (blob, digest, "blobs/" + digest),
        )
        c.execute(
            "INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'缺口夹具.pdf',now(),'source_original',%s)",
            (version, bid, blob, actor),
        )
        message, outcome = scheduled(
            c, actor, bid, version, parser="coverage-probe", coverage=coverage, text=text
        )
    lease = engine.acquire(message)
    assert engine.finish(message, lease, outcome)
    with connection() as c:
        head = c.execute(
            "SELECT status FROM retrieval.source_heads WHERE source_id=%s",
            (original_source_id(version),),
        ).fetchone()
        assert (head["status"] if head else None) == expected
        assert (
            c.execute(
                "SELECT coverage FROM parse_runs WHERE document_version_id=%s", (version,)
            ).fetchone()["coverage"]
            == coverage
        )
