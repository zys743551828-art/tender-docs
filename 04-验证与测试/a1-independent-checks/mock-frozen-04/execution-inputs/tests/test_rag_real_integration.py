"""Explicit real BGE/PG tests using synthetic parse-table fixtures, not business quality labels."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from packages.rag.core import validate_citation
from packages.rag.http_port import DenseHttp, LexicalHttp, RerankHttp
from packages.rag.indexing import (
    compute_index,
    ensure_parse_sources,
    prepare_index,
    publish_index,
    withdraw_source,
)
from packages.rag.repository import RetrievalScope, assert_current_sources, retrieve

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_RAG_REAL") != "1", reason="Explicit real model/PG opt-in"
)
ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def database(role="worker"):
    with psycopg.connect(
        os.environ[f"{role.upper()}_DATABASE_DSN"], row_factory=dict_row
    ) as connection:
        yield connection


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def seed_parse(bid=None, version=None, text="道路施工现场必须设置围挡，围挡高度不低于2米。"):
    with database("api") as c:
        if bid is None:
            bid, member = uuid4(), uuid4()
            c.execute(
                "INSERT INTO design.members(id,display_name) VALUES(%s,'RAG synthetic fixture')",
                (member,),
            )
            c.execute(
                "INSERT INTO design.bids(id,title,settings,created_by) VALUES(%s,'RAG合成隔离检验','{}',%s)",
                (bid, member),
            )
        else:
            member = c.execute("SELECT created_by FROM design.bids WHERE id=%s", (bid,)).fetchone()[
                "created_by"
            ]
        if version is None:
            version, blob = uuid4(), uuid4()
            c.execute(
                "INSERT INTO design.blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,1,'application/pdf')",
                (blob, digest(str(blob)), "synthetic-rag-sql-fixture/" + str(blob)),
            )
            c.execute(
                """INSERT INTO design.document_versions
                (id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by)
                VALUES(%s,%s,%s,'SQL夹具非实际原件.pdf',now()-interval '1 day','source_original',%s)""",
                (version, bid, blob, member),
            )
        parse, evidence = uuid4(), uuid4()
        c.execute(
            """INSERT INTO design.parse_runs
            (id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps)
            VALUES(%s,%s,%s,%s,%s,1,'partial',%s)""",
            (
                parse,
                bid,
                version,
                digest("fixture parser"),
                digest(str(parse)),
                Jsonb(["Synthetic parse-table fixture; not actual PDF parsing"]),
            ),
        )
        c.execute(
            """INSERT INTO design.pages
            (bid_id,parse_id,physical_page,width,height,rotation,quality,gaps)
            VALUES(%s,%s,1,595,842,0,'usable','[]')""",
            (bid, parse),
        )
        c.execute(
            """INSERT INTO design.evidence_blocks
            (id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest)
            VALUES(%s,%s,%s,1,0,'text',%s,%s)""",
            (evidence, bid, parse, text, digest(text)),
        )
    return bid, version, parse


@pytest.fixture(scope="module")
def models():
    dense = DenseHttp()
    return dense, LexicalHttp(dense)


@pytest.fixture
def indexed(models):
    dense, lexical = models
    bid, version, parse = seed_parse()
    with database() as c:
        prepared = prepare_index(c, bid, parse, dense, lexical)
    computed = compute_index(prepared, dense)
    with database() as c:
        result = publish_index(c, computed)
        assert (
            c.execute("SELECT rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()[
                "rolsuper"
            ]
            is False
        )
    return bid, version, parse, computed, result


def search(models, indexed, excluded=()):
    dense, lexical = models
    bid, _, _, _, index = indexed
    scope = RetrievalScope(
        bid, "compliance", (index["source_id"],), datetime.now(timezone.utc), excluded
    )
    with database("api") as c:
        return retrieve(
            c, scope, "道路围挡高度", dense, index["profile_id"], digest("policy fixture"), lexical
        )


def test_real_dense_lexical_pg_and_rerank(indexed, models):
    dense, lexical = models
    result = search(models, indexed)
    assert result["evidence"] and "+rrf" in result["route"]
    evidence = result["evidence"][0]
    assert validate_citation(evidence.citation_id, "围挡高度不低于2米", 1, result["evidence"])
    with database("api") as c:
        assert_current_sources(c, result)
        row = c.execute(
            "SELECT vector_dims(embedding) AS dim,vector_norm(embedding) AS norm FROM retrieval.embeddings WHERE profile_id=%s LIMIT 1",
            (indexed[4]["profile_id"],),
        ).fetchone()
        assert row["dim"] == 1024 and abs(row["norm"] - 1) < 0.001
    reranker = RerankHttp(dense)
    values = reranker.rank("围挡高度要求", [evidence.text, "办公用品采购清单。"])
    assert len(values) == 2 and all(isinstance(value, float) for value in values)
    # This only proves actual model execution; these two examples are not a quality benchmark.


def test_cross_project_and_profile_identity_rejected(indexed, models):
    dense, lexical = models
    result = search(models, indexed)
    with database("api") as c:
        with pytest.raises(ValueError):
            retrieve(
                c,
                replace(result["scope"], bid_id=uuid4()),
                "围挡",
                dense,
                indexed[4]["profile_id"],
                digest("p"),
                lexical,
            )
    changed = dict(dense.identity)
    changed["preprocessing_digest"] = digest("different preprocessing")

    class WrongProfile:
        identity = changed

        def embed(self, texts):
            pytest.fail("Profile mismatch must reject before inference")

    with database("api") as c:
        with pytest.raises(ValueError):
            retrieve(
                c,
                result["scope"],
                "围挡",
                WrongProfile(),
                indexed[4]["profile_id"],
                digest("p"),
                lexical,
            )


def test_new_excluded_ancestor_group_invalidates_application(indexed, models):
    group = uuid4()
    with database("api") as c:
        c.execute(
            "INSERT INTO retrieval.source_groups(id,reason) VALUES(%s,'synthetic exclusion')",
            (group,),
        )
    result = search(models, indexed, (group,))
    assert result["evidence"]
    with database("api") as c:
        c.execute("SELECT pg_advisory_xact_lock(90127,1)")
        c.execute(
            "INSERT INTO retrieval.source_group_memberships(source_id,group_id) VALUES(%s,%s)",
            (indexed[4]["original_source_id"], group),
        )
    with database("api") as c:
        with pytest.raises(ValueError):
            assert_current_sources(c, result)
    assert not search(models, indexed, (group,))["evidence"]


def test_root_withdrawal_blocks_application_and_reparse(indexed, models):
    result = search(models, indexed)
    bid, version, _, _, index = indexed
    with database() as c:
        revision = c.execute(
            "SELECT revision FROM retrieval.source_heads WHERE source_id=%s",
            (index["original_source_id"],),
        ).fetchone()["revision"]
        withdraw_source(c, bid, index["source_id"], revision)
    with database("api") as c:
        with pytest.raises(ValueError):
            assert_current_sources(c, result)
    assert not search(models, indexed)["evidence"]
    _, _, new_parse = seed_parse(bid, version)
    with database() as c:
        with pytest.raises(ValueError):
            ensure_parse_sources(c, bid, new_parse, publish_readable=True)


def test_future_and_validity_filters_in_real_pg(indexed):
    index = indexed[4]
    with database("api") as c:
        earlier = datetime.now(timezone.utc) - timedelta(days=2)
        rows = c.execute(
            "SELECT id FROM retrieval.eligible_chunks(%s,%s,%s,false)",
            ([index["source_id"]], earlier, []),
        ).fetchall()
        assert rows == []
