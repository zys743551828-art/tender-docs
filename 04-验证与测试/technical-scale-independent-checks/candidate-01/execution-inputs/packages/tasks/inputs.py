"""Server-side task context; instruction text is never executable configuration."""

from packages.domain.reviews import method_profile
from packages.domain.runtime import audit, clean, fetch, sha


def capture(c, bid, configuration):
    profile = fetch(
        c,
        "SELECT b.material_revision,b.settings,p.current_body_id,p.current_material_id,p.current_rule_set_id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id WHERE b.id=%s",
        (bid,),
    )
    fact = c.execute(
        "SELECT fact_set_id,revision FROM fact_heads WHERE bid_id=%s", (bid,)
    ).fetchone()
    versions = configuration["source_selection"]["version_ids"]
    if not versions and profile["current_material_id"]:
        versions = [
            str(r["document_version_id"])
            for r in c.execute(
                "SELECT document_version_id FROM material_assignments WHERE bid_id=%s AND snapshot_id=%s ORDER BY ordinal,document_version_id",
                (bid, profile["current_material_id"]),
            ).fetchall()
        ]
    documents = []
    for version in sorted(versions):
        row = fetch(
            c,
            "SELECT v.id,b.digest,(SELECT p.id FROM parse_runs p WHERE p.document_version_id=v.id AND p.bid_id=v.bid_id ORDER BY p.created_at DESC,p.id DESC LIMIT 1) AS parse_id FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.bid_id=%s AND v.id=%s",
            (bid, version),
        )
        documents.append(clean(row))
    section = None
    if configuration["section_id"]:
        section = clean(
            fetch(
                c,
                "SELECT id,draft_id,head_revision FROM sections WHERE bid_id=%s AND id=%s",
                (bid, configuration["section_id"]),
            )
        )
    return clean(
        {
            "body_version_id": profile["current_body_id"],
            "material_snapshot_id": profile["current_material_id"],
            "material_revision": profile["material_revision"],
            "rule_set_id": profile["current_rule_set_id"],
            "fact_head": fact,
            "documents": documents,
            "section": section,
            "method_digest": sha(method_profile()),
            "content_checks": {
                k: bool(profile["settings"].get(k, False)) for k in ("consistency", "scope_residue")
            },
        }
    )


def equivalent_context(expected, current, *, preparing=False):
    comparison = clean(current)
    if preparing:
        if expected["rule_set_id"] is None:
            comparison["rule_set_id"] = None
        expected_docs = {d["id"]: d for d in expected["documents"]}
        for document in comparison["documents"]:
            if (
                document["id"] in expected_docs
                and expected_docs[document["id"]]["parse_id"] is None
            ):
                document["parse_id"] = None
    return expected == comparison


def review_guard(c, bid, intent):
    """Call under the bid lock BEFORE _advance_head and freeze_candidate. No network/DDL."""
    binding = intent.get("task_binding")
    if not binding:
        return True
    row = fetch(
        c,
        "SELECT frozen_manifest FROM task_runs WHERE bid_id=%s AND id=%s",
        (bid, binding["run_id"]),
    )
    frozen = row["frozen_manifest"]
    if (
        c.execute(
            "SELECT count(*) AS n FROM audit_entries WHERE bid_id=%s AND event_type='task.review_context_frozen' AND payload->>'run_id'=%s",
            (bid, binding["run_id"]),
        ).fetchone()["n"]
        > 1
    ):
        intent.update(
            state="superseded",
            error_code="TASK_CONTEXT_AUTHORITY_AMBIGUOUS",
            gaps=["任务输入权威不唯一，未授权继续执行"],
        )
        return False
    current = capture(c, bid, frozen["configuration"])
    resolved = prepared_context(c, bid, binding["run_id"])
    expected = resolved["payload"]["context"] if resolved else frozen["context"]
    valid = (
        frozen["review_request_id"] == intent["request_id"]
        and frozen["actor_id"] == binding["actor_id"]
        and frozen["input_digest"] == binding["input_digest"]
    )
    if resolved and (
        str(resolved["actor_id"]) != frozen["actor_id"]
        or str(resolved["command_id"]) != frozen["request_id"]
    ):
        valid = False
    # An unknown parse/rules preparation may resolve once for the same immutable original scope.
    valid = valid and equivalent_context(expected, current, preparing=not bool(resolved))
    if (
        valid
        and not resolved
        and current["rule_set_id"]
        and all(d["parse_id"] for d in current["documents"])
    ):
        rules = c.execute(
            "SELECT 1 FROM rule_sets WHERE id=%s AND bid_id=%s AND material_id=%s",
            (current["rule_set_id"], bid, current["material_snapshot_id"]),
        ).fetchone()
        if rules:
            audit(
                c,
                frozen["actor_id"],
                bid,
                "task.review_context_frozen",
                {"run_id": binding["run_id"], "context": current, "context_digest": sha(current)},
                frozen["request_id"],
            )
    if not valid:
        intent.update(
            state="superseded",
            error_code="TASK_INPUT_SUPERSEDED",
            gaps=["任务冻结的资料或输入已变化，原运行不自动改用新稿"],
        )
    return valid


def prepared_context(c, bid, run_id):
    rows = c.execute(
        "SELECT actor_id,payload,command_id FROM audit_entries WHERE bid_id=%s AND event_type='task.review_context_frozen' AND payload->>'run_id'=%s",
        (bid, str(run_id)),
    ).fetchall()
    if len(rows) > 1:
        return None
    return rows[0] if rows else None
