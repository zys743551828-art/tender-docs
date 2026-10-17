"""受控Word转换的PDF原件祖先；不登记Word文字块或向量。"""

from uuid import UUID

from packages.domain.runtime import DomainError, fetch, require_read_committed
from packages.domain.word_materials import configured
from packages.rag.indexing import original_source_id
from packages.word_intake.inspection import DOCX, PDF
from packages.word_intake.repository import controlled_qualified


def ensure_controlled_ancestry(c, bid, document):
    require_read_committed(c)
    document = UUID(str(document))
    if not configured(c):
        return None
    rows = c.execute(
        "SELECT w.document_version_id AS source,r.outcome,r.profile_digest FROM document_relations d JOIN word_intake_receipts w ON w.document_version_id=d.source_document_version_id AND w.bid_id=d.bid_id JOIN word_render_runs r ON r.source_document_version_id=w.document_version_id AND r.pdf_document_version_id=d.document_version_id AND r.bid_id=d.bid_id WHERE d.bid_id=%s AND d.document_version_id=%s AND d.kind='controlled_render' AND d.verification='verified'",
        (bid, document),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1 or not controlled_qualified(rows[0]["outcome"]):
        raise DomainError("WORD_LINEAGE_INVALID", "转换原件关系缺少完整受控依据", 409)
    source = rows[0]["source"]
    nodes = []
    c.execute("SELECT pg_advisory_xact_lock(90127,1)")
    official = c.execute(
        "SELECT EXISTS(SELECT 1 FROM material_assignments WHERE bid_id=%s AND document_version_id IN (%s,%s) AND role='official_result') AS value",
        (bid, source, document),
    ).fetchone()["value"]
    policy = "official_label" if official else "prebid_content"
    for version, expected, digest in (
        (source, DOCX, rows[0]["outcome"]["chain"]["source_docx_digest"]),
        (document, PDF, rows[0]["outcome"]["chain"]["pdf_digest"]),
    ):
        identity = original_source_id(version)
        row = fetch(
            c,
            "SELECT v.obtained_at,b.digest,b.media_type FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s AND v.bid_id=%s",
            (version, bid),
        )
        if row["digest"] != digest or row["media_type"] != expected:
            raise DomainError("WORD_LINEAGE_INVALID", "受控转换两端原件身份不一致", 409)
        c.execute(
            "INSERT INTO retrieval.source_nodes(id,document_version_id,kind,policy_class,known_at,digest) VALUES(%s,%s,'document',%s,%s,%s) ON CONFLICT(id) DO NOTHING",
            (identity, version, policy, row["obtained_at"], digest),
        )
        node = fetch(c, "SELECT * FROM retrieval.source_nodes WHERE id=%s", (identity,))
        if (
            node["document_version_id"] != version
            or node["digest"] != digest
            or node["policy_class"] != policy
        ):
            raise DomainError("WORD_LINEAGE_INVALID", "来源不可变身份或用途限制变化", 409)
        c.execute(
            "INSERT INTO retrieval.source_heads(source_id,revision,status,valid_from) VALUES(%s,1,'candidate',%s) ON CONFLICT(source_id) DO NOTHING",
            (identity, row["obtained_at"]),
        )
        head = fetch(
            c, "SELECT * FROM retrieval.source_heads WHERE source_id=%s FOR UPDATE", (identity,)
        )
        withdrawn = c.execute(
            "SELECT 1 FROM retrieval.source_ancestors a JOIN retrieval.source_heads h ON h.source_id=a.ancestor_id WHERE a.source_id=%s AND h.status='withdrawn' LIMIT 1",
            (identity,),
        ).fetchone()
        if head["status"] == "withdrawn" or withdrawn:
            raise DomainError("WORD_SOURCE_WITHDRAWN", "Word或派生祖先已撤回，不能重新发布", 409)
        invalid = c.execute(
            "SELECT 1 FROM retrieval.source_ancestors a JOIN retrieval.source_nodes n ON n.id=a.ancestor_id JOIN retrieval.source_heads h ON h.source_id=a.ancestor_id WHERE a.source_id=%s AND (n.known_at>clock_timestamp() OR h.valid_from>clock_timestamp() OR h.valid_until<=clock_timestamp() OR (a.ancestor_id<>%s AND h.status<>'published')) LIMIT 1",
            (identity, identity),
        ).fetchone()
        if invalid:
            raise DomainError(
                "WORD_SOURCE_NOT_EFFECTIVE", "Word或派生祖先当前效力不明，未采用转换结果", 409
            )
        nodes.append(identity)
    c.execute(
        "INSERT INTO retrieval.lineage_edges(child_id,parent_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
        (nodes[1], nodes[0]),
    )
    # The original root can be valid provenance while having no readable text/chunks.
    c.execute(
        "UPDATE retrieval.source_heads SET status='published',revision=revision+1 WHERE source_id=%s AND status='candidate'",
        (nodes[0],),
    )
    return nodes[0]
