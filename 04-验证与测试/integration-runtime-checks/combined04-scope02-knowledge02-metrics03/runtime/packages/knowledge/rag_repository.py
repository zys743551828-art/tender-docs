"""Short trusted API freezes and fenced Worker publication of knowledge-only units."""

from __future__ import annotations

import json
import math
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from packages.domain.runtime import DomainError, audit, clean, enqueue_plan, fetch, sha
from packages.knowledge.rag_index import CHUNK_POLICY, PreparedKnowledgeIndex, unit_manifest
from packages.knowledge.rag_types import UnitFragment, assert_unit_envelope
from packages.knowledge.repository import lifecycle, lock_item, version_row
from packages.knowledge.sources import authority_manifest, lock_graph, lock_heads, verify_provenance
from packages.rag.indexing import ensure_profile, original_source_id, parse_source_id
from packages.rag.repository import assert_embedding_profile


def clock(c):
    return c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]


def assert_schema(c, *, worker=False):
    names = [
        "knowledge_indexes",
        "knowledge_index_heads",
        "knowledge_units",
        "knowledge_unit_embeddings",
    ]
    if not worker:
        names += ["knowledge_retrieval_traces", "knowledge_unit_references"]
    for name in names:
        relation = "retrieval." + name
        if c.execute("SELECT to_regclass(%s) AS id", (relation,)).fetchone()["id"] is None:
            raise DomainError("KNOWLEDGE_RAG_NOT_READY", "企业知识检索尚未启用", 503)
        if not c.execute(
            "SELECT has_table_privilege(current_user,%s,'SELECT') AS allowed", (relation,)
        ).fetchone()["allowed"]:
            raise DomainError("KNOWLEDGE_RAG_NOT_READY", "企业知识检索能力尚未启用", 503)


def index_row(c, index_id):
    return fetch(c, "SELECT * FROM retrieval.knowledge_indexes WHERE id=%s", (index_id,))


def assert_profile(c, profile_id, identity):
    profile = fetch(c, "SELECT * FROM retrieval.embedding_profiles WHERE id=%s", (profile_id,))
    if (
        set(identity) != {"model_name", "model_revision", "weights_digest", "preprocessing_digest"}
        or profile["dimensions"] != 1024
        or profile["distance"] != "cosine"
        or not profile["normalized"]
        or any(profile[key] != value for key, value in identity.items())
    ):
        raise ValueError("KNOWLEDGE_INDEX_PROFILE_CHANGED")


def schedule_index(c, knowledge_id, actor, command_id, dense, *, expected_version_id=None):
    """Dense identity is already fetched outside this short API transaction."""
    assert_schema(c)
    item, origin = lock_item(c, knowledge_id)
    if item["published_version_id"] is None:
        raise DomainError("KNOWLEDGE_NOT_PUBLISHED", "请先明确发布有效知识版本", 409)
    if expected_version_id is not None and item["published_version_id"] != expected_version_id:
        raise DomainError("KNOWLEDGE_VERSION_CHANGED", "发布版本已变化，请核对后重新建立索引", 409)
    version = version_row(c, knowledge_id, item["published_version_id"])
    lock_heads(c, [version["source_id"]])
    at = clock(c)
    if lifecycle(c, version, at) != "published":
        raise DomainError("KNOWLEDGE_NOT_PUBLISHED", "知识已失效，不能新建检索索引", 409)
    verify_provenance(c, version["applicability"]["source_refs"], at)
    authority = authority_manifest(c, [version["source_id"]], at)
    profile = ensure_profile(c, dense.identity)
    assert_embedding_profile(c, profile, dense)
    head = c.execute(
        "SELECT * FROM retrieval.knowledge_index_heads WHERE knowledge_version_id=%s AND profile_id=%s FOR UPDATE",
        (version["id"], profile),
    ).fetchone()
    revision = head["revision"] + 1 if head else 1
    index_id = uuid4()
    manifest = clean(
        {
            "schema_version": 1,
            "index_id": index_id,
            "knowledge_id": knowledge_id,
            "knowledge_version_id": version["id"],
            "knowledge_source_id": version["source_id"],
            "origin_bid_id": origin,
            "created_by": actor,
            "profile_id": profile,
            "embedding_identity": dense.identity,
            "lexical_dictionary_digest": dense.lexical_dictionary_digest,
            "chunk_policy": CHUNK_POLICY,
            "requested_head_revision": revision,
            "content": version["content"],
            "source_refs": version["applicability"]["source_refs"],
            "publication_basis": version["applicability"]["publication_basis"],
            "authority_manifest": authority,
        }
    )
    digest = sha(manifest)
    payload = {"id": str(index_id), "manifest_digest": digest}
    operation = enqueue_plan(
        c,
        origin,
        command_id,
        "index-knowledge",
        payload,
        stages=[{"key": "index-knowledge", "depends_on": []}],
        handler_version="knowledge-index/1",
        topic="tender.task.document.v1",
    )
    c.execute(
        """INSERT INTO retrieval.knowledge_indexes(id,knowledge_version_id,profile_id,origin_bid_id,
        operation_id,input_manifest,input_digest,chunk_policy_digest,lexical_dictionary_digest,
        requested_head_revision,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            index_id,
            version["id"],
            profile,
            origin,
            UUID(operation["id"]),
            Jsonb(manifest),
            digest,
            sha(CHUNK_POLICY),
            dense.lexical_dictionary_digest,
            revision,
            actor,
        ),
    )
    c.execute(
        """INSERT INTO retrieval.knowledge_index_heads(knowledge_version_id,profile_id,revision,desired_index_id)
        VALUES(%s,%s,%s,%s) ON CONFLICT(knowledge_version_id,profile_id) DO UPDATE
        SET revision=excluded.revision,desired_index_id=excluded.desired_index_id""",
        (version["id"], profile, revision, index_id),
    )
    audit(
        c,
        actor,
        origin,
        "knowledge.index_requested",
        {
            "index_id": index_id,
            "knowledge_id": knowledge_id,
            "version_id": version["id"],
            "profile_id": profile,
        },
        command_id,
    )
    return {"index_id": str(index_id), "operation": operation}


def prepare_index(c, index_id):
    """Only immutable input reads. Close this connection before all model work."""
    assert_schema(c, worker=True)
    row = index_row(c, index_id)
    m = row["input_manifest"]
    if (
        sha(m) != row["input_digest"]
        or m["schema_version"] != 1
        or m["index_id"] != str(row["id"])
        or m["knowledge_version_id"] != str(row["knowledge_version_id"])
        or m["origin_bid_id"] != str(row["origin_bid_id"])
        or m["profile_id"] != str(row["profile_id"])
        or m["chunk_policy"] != CHUNK_POLICY
        or sha(CHUNK_POLICY) != row["chunk_policy_digest"]
        or m["lexical_dictionary_digest"] != row["lexical_dictionary_digest"]
        or m["requested_head_revision"] != row["requested_head_revision"]
    ):
        raise ValueError("KNOWLEDGE_INDEX_INPUT_INVALID")
    return PreparedKnowledgeIndex(
        row["id"],
        row["input_digest"],
        m["content"],
        tuple(r["excerpt"] for r in m["source_refs"]),
        json.dumps(m["embedding_identity"], sort_keys=True),
        row["lexical_dictionary_digest"],
    )


def fragment_from_row(row):
    return UnitFragment(
        row["id"],
        row["index_id"],
        row["ordinal"],
        row["unit_kind"],
        row["ref_ordinal"],
        row["span_start"],
        row["span_end"],
        row["text_content"],
        row["token_count"],
        row["text_digest"],
    )


def unit_provenance(unit, manifest):
    if unit.unit_kind == "knowledge_text":
        return {
            "kind": "derived_knowledge",
            "offset_basis": "knowledge_content",
            "approved_references": manifest["source_refs"],
            "verbatim_original": False,
        }
    return {
        "kind": "original_quote",
        "offset_basis": "approved_excerpt",
        "reference": manifest["source_refs"][unit.ref_ordinal],
        "verbatim_original": True,
    }


def verify_quote_bindings(c, refs):
    """Worker verifies exact originals without reading company/engineering identity."""
    for ref in refs:
        if UUID(ref["source_id"]) != parse_source_id(UUID(ref["parse_id"])):
            raise ValueError("KNOWLEDGE_QUOTE_SOURCE_INVALID")
        row = c.execute(
            """SELECT e.text_content FROM design.evidence_blocks e
            JOIN design.parse_runs p ON p.id=e.parse_id AND p.bid_id=e.bid_id
            JOIN design.pages page ON page.parse_id=e.parse_id AND page.bid_id=e.bid_id AND page.physical_page=e.physical_page
            JOIN design.document_versions v ON v.id=p.document_version_id AND v.bid_id=p.bid_id
            JOIN design.blobs b ON b.id=v.blob_id
            JOIN retrieval.source_nodes n ON n.id=%s AND n.kind='document' AND n.document_version_id=v.id AND n.digest=b.digest
            JOIN retrieval.source_heads h ON h.source_id=n.id AND h.revision=%s
            WHERE e.id=%s AND p.id=%s AND p.document_version_id=%s AND e.physical_page=%s
            AND p.coverage<>'failed' AND page.quality<>'failed'
            AND EXISTS(SELECT 1 FROM retrieval.lineage_edges edge JOIN retrieval.source_nodes root ON root.id=edge.parent_id
              WHERE edge.child_id=n.id AND root.id=%s AND root.kind='document' AND root.document_version_id=v.id AND root.digest=b.digest)""",
            (
                UUID(ref["source_id"]),
                ref["source_revision"],
                UUID(ref["evidence_id"]),
                UUID(ref["parse_id"]),
                UUID(ref["document_version_id"]),
                ref["physical_page"],
                original_source_id(UUID(ref["document_version_id"])),
            ),
        ).fetchone()
        if not row or ref["excerpt"] not in row["text_content"]:
            raise ValueError("KNOWLEDGE_QUOTE_POSITION_INVALID")


def stored_manifest(c, index_id):
    rows = c.execute(
        """SELECT u.*,e.embedding::text AS vector_text FROM retrieval.knowledge_units u
        JOIN retrieval.knowledge_unit_embeddings e ON e.unit_id=u.id AND e.index_id=u.index_id
        WHERE u.index_id=%s ORDER BY u.ordinal""",
        (index_id,),
    ).fetchall()
    return unit_manifest(
        tuple(fragment_from_row(r) for r in rows),
        tuple(r["lexemes"] for r in rows),
        tuple(tuple(json.loads(r["vector_text"])) for r in rows),
    )


def publish_index(c, outcome):
    """Called only inside the engine's checked fence/effect transaction; no model I/O."""
    assert_schema(c, worker=True)
    row = index_row(c, outcome.prepared.index_id)
    # Engine already holds origin bid lock; direct callers must obey the same order.
    c.execute("SELECT design.lock_worker_bid(%s)", (row["origin_bid_id"],))
    lock_graph(c)
    m = row["input_manifest"]
    if prepare_index(c, row["id"]) != outcome.prepared:
        raise ValueError("KNOWLEDGE_INDEX_INPUT_CHANGED")
    if (
        sha(unit_manifest(outcome.units, outcome.lexical_texts, outcome.vectors))
        != outcome.manifest_digest
    ):
        raise ValueError("KNOWLEDGE_INDEX_OUTPUT_CHANGED")
    for unit in outcome.units:
        assert_unit_envelope(unit, m["content"], tuple(ref["excerpt"] for ref in m["source_refs"]))
    head = fetch(
        c,
        "SELECT * FROM retrieval.knowledge_index_heads WHERE knowledge_version_id=%s AND profile_id=%s FOR UPDATE",
        (row["knowledge_version_id"], row["profile_id"]),
    )
    if row["state"] == "ready":
        if (
            sha(stored_manifest(c, row["id"])) != outcome.manifest_digest
            or row["unit_manifest_digest"] != outcome.manifest_digest
        ):
            raise ValueError("KNOWLEDGE_INDEX_REPLAY_DIFFERENT")
        return {
            "index_id": str(row["id"]),
            "state": "ready",
            "unit_count": row["published_unit_count"],
        }
    if head["desired_index_id"] != row["id"] or head["revision"] != row["requested_head_revision"]:
        c.execute(
            "UPDATE retrieval.knowledge_indexes SET state='superseded',error_code='KNOWLEDGE_INDEX_SUPERSEDED',completed_at=clock_timestamp() WHERE id=%s",
            (row["id"],),
        )
        return {"index_id": str(row["id"]), "state": "superseded", "unit_count": 0}
    version = version_row(c, UUID(m["knowledge_id"]), row["knowledge_version_id"])
    lock_heads(c, [version["source_id"]])
    at = clock(c)
    if (
        version["published_version_id"] != version["id"]
        or lifecycle(c, version, at) != "published"
        or version["content"] != m["content"]
        or version["applicability"]["source_refs"] != m["source_refs"]
        or version["applicability"]["publication_basis"] != m["publication_basis"]
        or authority_manifest(c, [version["source_id"]], at) != m["authority_manifest"]
    ):
        raise ValueError("KNOWLEDGE_INDEX_AUTHORITY_CHANGED")
    verify_quote_bindings(c, m["source_refs"])
    assert_profile(c, row["profile_id"], m["embedding_identity"])
    units, lexemes, vectors = outcome.units, outcome.lexical_texts, outcome.vectors
    if not 1 <= len(units) <= 1000 or len(units) != len(lexemes) or len(units) != len(vectors):
        raise ValueError("KNOWLEDGE_INDEX_INCOMPLETE")
    if sha(unit_manifest(units, lexemes, vectors)) != outcome.manifest_digest:
        raise ValueError("KNOWLEDGE_INDEX_OUTPUT_CHANGED")
    policy = (
        "prebid_content"
        if all(a["policy_class"] == "prebid_content" for a in m["authority_manifest"])
        else "derived_mixed"
    )
    for ordinal, (unit, lexical, vector) in enumerate(zip(units, lexemes, vectors, strict=True)):
        if (
            unit.index_id != row["id"]
            or unit.ordinal != ordinal
            or len(vector) != 1024
            or any(not math.isfinite(v) for v in vector)
            or abs(math.fsum(v * v for v in vector) - 1) >= 0.001
        ):
            raise ValueError("KNOWLEDGE_UNIT_INVALID")
        assert_unit_envelope(unit, m["content"], tuple(r["excerpt"] for r in m["source_refs"]))
        provenance = unit_provenance(unit, m)
        c.execute(
            "INSERT INTO retrieval.source_nodes(id,kind,policy_class,known_at,digest) VALUES(%s,'summary',%s,%s,%s)",
            (unit.id, policy, at, unit.digest),
        )
        c.execute(
            "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from,valid_until) VALUES(%s,1,'published',%s,%s)",
            (unit.id, at, version["valid_until"]),
        )
        parents = [version["source_id"]]
        if unit.unit_kind == "original_quote":
            parents.append(UUID(m["source_refs"][unit.ref_ordinal]["source_id"]))
        for parent in set(parents):
            c.execute(
                "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s)",
                (unit.id, parent),
            )
        c.execute(
            """INSERT INTO retrieval.knowledge_units(id,index_id,knowledge_version_id,ordinal,source_id,unit_kind,
            ref_ordinal,span_start,span_end,text_content,text_digest,token_count,lexemes,provenance)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                unit.id,
                row["id"],
                version["id"],
                ordinal,
                unit.id,
                unit.unit_kind,
                unit.ref_ordinal,
                unit.span_start,
                unit.span_end,
                unit.text,
                unit.digest,
                unit.token_count,
                lexical,
                Jsonb(provenance),
            ),
        )
        c.execute(
            "INSERT INTO retrieval.knowledge_unit_embeddings(index_id,unit_id,profile_id,embedding) VALUES(%s,%s,%s,%s::design.vector)",
            (row["id"], unit.id, row["profile_id"], json.dumps(vector)),
        )
    # Float32 readback independently binds persisted vectors and all unit identities.
    if sha(stored_manifest(c, row["id"])) != outcome.manifest_digest:
        raise ValueError("KNOWLEDGE_INDEX_READBACK_CHANGED")
    authority_manifest(c, [version["source_id"]], clock(c))
    c.execute(
        "UPDATE retrieval.knowledge_indexes SET state='ready',expected_unit_count=%s,published_unit_count=%s,unit_manifest_digest=%s,completed_at=clock_timestamp() WHERE id=%s",
        (len(units), len(units), outcome.manifest_digest, row["id"]),
    )
    c.execute(
        "UPDATE retrieval.knowledge_index_heads SET ready_index_id=%s WHERE knowledge_version_id=%s AND profile_id=%s AND desired_index_id=%s AND revision=%s",
        (row["id"], version["id"], row["profile_id"], row["id"], row["requested_head_revision"]),
    )
    return {"index_id": str(row["id"]), "state": "ready", "unit_count": len(units)}


def index_history(c, knowledge_id):
    assert_schema(c)
    fetch(c, "SELECT id FROM retrieval.knowledge_items WHERE id=%s", (knowledge_id,))
    rows = c.execute(
        """SELECT i.*,o.state AS operation_state,h.ready_index_id,h.desired_index_id,failure.step_error FROM retrieval.knowledge_indexes i
        JOIN retrieval.knowledge_versions v ON v.id=i.knowledge_version_id JOIN design.operations o ON o.id=i.operation_id
        JOIN retrieval.knowledge_index_heads h ON h.knowledge_version_id=i.knowledge_version_id AND h.profile_id=i.profile_id
        LEFT JOIN LATERAL(SELECT s.output->>'error_code' AS step_error FROM step_receipts s
          WHERE s.operation_id=i.operation_id ORDER BY s.attempt_count DESC LIMIT 1) failure ON true
        WHERE v.item_id=%s ORDER BY i.created_at DESC,i.id LIMIT 100""",
        (knowledge_id,),
    ).fetchall()
    return {
        "items": [
            clean(
                {
                    "index_id": r["id"],
                    "knowledge_version_id": r["knowledge_version_id"],
                    "profile_id": r["profile_id"],
                    "operation_id": r["operation_id"],
                    "state": "failed"
                    if r["operation_state"] in ("failed", "cancelled") and r["state"] == "queued"
                    else r["state"],
                    "expected_unit_count": r["expected_unit_count"],
                    "published_unit_count": r["published_unit_count"],
                    "unit_manifest_digest": r["unit_manifest_digest"],
                    "current_ready": r["ready_index_id"] == r["id"],
                    "desired": r["desired_index_id"] == r["id"],
                    "error_code": r["error_code"]
                    or r["step_error"]
                    or (
                        "KNOWLEDGE_INDEX_CANCELLED" if r["operation_state"] == "cancelled" else None
                    ),
                }
            )
            for r in rows
        ]
    }
