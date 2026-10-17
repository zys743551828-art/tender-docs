"""API控制面采用已完成的受控PDF；Worker只登记产物，不持控制面权限。"""

from botocore.exceptions import BotoCoreError, ClientError
from psycopg.types.json import Jsonb

from packages.authoring.repository import assert_sources, snapshot_content
from packages.domain import storage
from packages.domain.parsing_profile import configured_parser
from packages.domain.parsing_profile import profile as parser_profile
from packages.domain.reviews import queue_intent
from packages.domain.runtime import (
    DomainError,
    audit,
    clean,
    connection,
    enqueue,
    enqueue_plan,
    fetch,
    sha,
    uid,
)

EVENT = "build.follow_up_decided"
TERMINAL = ("succeeded", "failed", "cancelled")


def _decision(c, build_id):
    row = c.execute(
        "SELECT payload FROM audit_entries WHERE event_type=%s AND payload->>'build_id'=%s ORDER BY created_at,id LIMIT 1",
        (EVENT, str(build_id)),
    ).fetchone()
    return row["payload"] if row else None


def follow_up_view(c, build_id, manifest):
    """采用与核对是独立状态；adopted只代表正文和后续请求已原子登记。"""
    requested = manifest.get("follow_up") == "review"
    value = {
        "requested": requested,
        "state": "pending" if requested else "not_requested",
        "adopted_document_version_id": None,
        "parse_operation_id": None,
        "request_id": None,
        "review_id": None,
        "reasons": [],
    }
    if not requested:
        return value
    decision = _decision(c, build_id)
    if decision:
        value.update({key: decision[key] for key in value if key in decision})
        if value["request_id"]:
            frozen = c.execute(
                "SELECT r.id FROM audit_entries a JOIN review_runs r ON r.id=(a.payload->>'review_id')::uuid WHERE a.event_type='review.frozen' AND a.payload->>'request_id'=%s ORDER BY a.created_at,a.id LIMIT 1",
                (value["request_id"],),
            ).fetchone()
            value["review_id"] = str(frozen["id"]) if frozen else None
    elif not manifest.get("follow_up_context"):
        value.update(state="needs_selection", reasons=["BUILD_FOLLOW_UP_CONTEXT_MISSING"])
    return value


def _build(c, build_id):
    return fetch(
        c,
        "SELECT b.*,i.draft_snapshot_id,i.manifest,o.state AS operation_state,o.command_id FROM builds b JOIN build_inputs i ON i.id=b.input_id JOIN operations o ON o.id=b.operation_id WHERE b.id=%s",
        (build_id,),
    )


def _heads(c, bid):
    return clean(
        fetch(
            c,
            "SELECT b.revision AS bid_revision,b.material_revision,b.settings_revision,p.current_material_id,p.current_body_id,p.current_draft_id,p.current_rule_set_id,f.fact_set_id,coalesce(f.revision,0) AS fact_revision FROM bids b JOIN bid_profiles p ON p.bid_id=b.id LEFT JOIN fact_heads f ON f.bid_id=b.id WHERE b.id=%s FOR UPDATE OF b",
            (bid,),
        )
    )


def _matches(c, build, heads):
    context = build["manifest"].get("follow_up_context")
    if not context or context.get("enabled") is not True:
        return False
    if any(key not in context or context[key] != value for key, value in heads.items()):
        return False
    if context.get("current_draft_id") != context.get("draft_id"):
        return False
    draft = c.execute(
        "SELECT revision FROM drafts WHERE bid_id=%s AND id=%s",
        (build["bid_id"], context.get("draft_id")),
    ).fetchone()
    snapshot = c.execute(
        "SELECT digest,draft_id,revision FROM draft_snapshots WHERE bid_id=%s AND id=%s",
        (build["bid_id"], build["draft_snapshot_id"]),
    ).fetchone()
    return bool(
        draft
        and snapshot
        and draft["revision"] == context.get("draft_revision") == snapshot["revision"]
        and str(snapshot["draft_id"]) == context.get("draft_id")
        and str(build["draft_snapshot_id"]) == context.get("draft_snapshot_id")
        and snapshot["digest"]
        == context.get("draft_snapshot_digest")
        == build["manifest"].get("snapshot_digest")
    )


def _pdf(c, build):
    """格式成功、同构建Word以及受控关系三者同时存在才有资格采用。"""
    return c.execute(
        "SELECT v.id,b.object_key,b.digest,b.byte_size,f.file_id FROM build_outputs p JOIN build_outputs w ON w.build_id=p.build_id AND w.bid_id=p.bid_id AND w.format='docx' AND w.state='succeeded' JOIN document_relations r ON r.bid_id=p.bid_id AND r.document_version_id=p.document_version_id AND r.source_document_version_id=w.document_version_id AND r.kind='controlled_render' AND r.verification='verified' JOIN document_versions v ON v.id=p.document_version_id AND v.bid_id=p.bid_id JOIN blobs b ON b.id=v.blob_id JOIN document_file_versions f ON f.version_id=v.id AND f.bid_id=v.bid_id WHERE p.build_id=%s AND p.bid_id=%s AND p.format='pdf' AND p.state='succeeded' AND b.media_type='application/pdf'",
        (build["id"], build["bid_id"]),
    ).fetchone()


def _record(c, build, state, reasons, **identities):
    actor = build["manifest"]["created_by"]
    value = {"build_id": str(build["id"]), "state": state, "reasons": reasons} | identities
    audit(c, actor, build["bid_id"], EVENT, value, build["command_id"])
    return follow_up_view(c, build["id"], build["manifest"])


def _adopt(c, build, heads, pdf, parser):
    bid = build["bid_id"]
    prior = (
        c.execute(
            "SELECT a.document_version_id,a.role,f.file_id FROM material_assignments a JOIN document_file_versions f ON f.version_id=a.document_version_id AND f.bid_id=a.bid_id WHERE a.bid_id=%s AND a.snapshot_id=%s ORDER BY a.role,a.ordinal",
            (bid, heads["current_material_id"]),
        ).fetchall()
        if heads["current_material_id"]
        else []
    )
    entries = [row for row in prior if row["role"] != "body"] + [
        {"document_version_id": pdf["id"], "role": "body", "file_id": pdf["file_id"]}
    ]
    snapshot, revision = uid(), heads["material_revision"] + 1
    manifest = clean(entries)
    c.execute(
        "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,%s,%s,%s)",
        (snapshot, bid, revision, Jsonb({"documents": manifest}), sha(manifest)),
    )
    counters: dict[str, int] = {}
    for row in entries:
        role = row["role"]
        ordinal = counters.get(role, 0)
        counters[role] = ordinal + 1
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,%s,%s)",
            (snapshot, bid, row["document_version_id"], role, ordinal),
        )
    c.execute(
        "UPDATE bids SET revision=revision+1,material_revision=%s WHERE id=%s", (revision, bid)
    )
    c.execute(
        "UPDATE bid_profiles SET current_material_id=%s,current_body_id=%s WHERE bid_id=%s",
        (snapshot, pdf["id"], bid),
    )
    payload = {"id": snapshot, "digest": sha(manifest)}
    if parser is None:
        operation = enqueue(c, bid, build["command_id"], "parse-materials", payload)
    else:
        operation = enqueue_plan(
            c,
            bid,
            build["command_id"],
            "parse-materials",
            payload,
            stages=[{"key": "parse-materials", "depends_on": []}],
            handler_version="document/2",
            topic="tender.task.document.v1",
            input_digest=sha(payload | {"parse_profile": parser_profile(parser)}),
        )
    request = queue_intent(
        c,
        bid,
        build["command_id"],
        {
            "body_version_id": str(pdf["id"]),
            "material_revision": revision,
            "trigger": "build_follow_up",
        },
    )
    return _record(
        c,
        build,
        "adopted",
        [],
        adopted_document_version_id=str(pdf["id"]),
        material_snapshot_id=str(snapshot),
        parse_operation_id=operation["id"],
        request_id=request["resource_id"],
    )


def _verify_pdf(pdf):
    try:
        body = storage.client().get_object(Bucket=storage.bucket(), Key=pdf["object_key"])["Body"]
        try:
            raw = body.read()
        finally:
            body.close()
    except (BotoCoreError, ClientError) as error:
        raise DomainError(
            "BUILD_PDF_STORAGE_UNAVAILABLE", "生成PDF暂时无法读取；产物已保留", 503, retryable=True
        ) from error
    if len(raw) != pdf["byte_size"] or sha(raw) != pdf["digest"]:
        raise DomainError("BUILD_PDF_BYTES_CORRUPTED", "生成PDF实际字节校验失败", 409)


def tick(build_id):
    """短事务读→锁外字节/解析身份核验→bid锁内CAS；重复控制器只采用一次。"""
    with connection() as c:
        build = _build(c, build_id)
        if _decision(c, build_id) or build["manifest"].get("follow_up") != "review":
            return follow_up_view(c, build_id, build["manifest"])
        if build["operation_state"] not in TERMINAL:
            return follow_up_view(c, build_id, build["manifest"])
        pdf = _pdf(c, build)
    # 原件和解析配置的I/O不得保持bid锁或数据库连接。
    parser, failure = None, None
    if pdf and build["operation_state"] == "succeeded":
        try:
            _verify_pdf(pdf)
            parser = configured_parser()
        except DomainError as error:
            if error.retryable:
                raise
            failure = error.code
    with connection() as c:
        heads = _heads(c, build["bid_id"])
        build = _build(c, build_id)
        if _decision(c, build_id):
            return follow_up_view(c, build_id, build["manifest"])
        if failure:
            return _record(c, build, "failed", [failure])
        if build["operation_state"] != "succeeded" or not pdf:
            return _record(c, build, "failed", ["BUILD_REVIEW_PDF_UNAVAILABLE"])
        if not _matches(c, build, heads):
            return _record(c, build, "needs_selection", ["BUILD_FOLLOW_UP_INPUT_CHANGED"])
        try:
            with c.transaction():
                snapshot = snapshot_content(c, build["bid_id"], build["draft_snapshot_id"])
                for section in snapshot["sections"]:
                    assert_sources(c, build["bid_id"], section)
        except DomainError as error:
            return _record(c, build, "needs_selection", [error.code])
        return _adopt(c, build, heads, pdf, parser)
