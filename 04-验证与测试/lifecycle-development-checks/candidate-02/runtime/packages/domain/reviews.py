"""评审请求与可恢复API控制面；普通Worker不持API凭据或冻结输入写权限。"""

import os
from datetime import datetime

from psycopg.types.json import Jsonb

from packages.domain.model_recovery import assert_preparation_reconciled, assert_review_reconciled
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.review_data import (
    effective_at,
    frozen_documents,
    publish_sources,
    read_bundle,
    read_fact_documents,
    save_json,
    source_closure,
    verify_bytes,
)
from packages.domain.review_identity import identities
from packages.domain.rule_sets import create_extracted_set, rules
from packages.domain.runtime import (
    ROOT,
    DomainError,
    accepted,
    audit,
    clean,
    connection,
    enqueue_plan,
    fetch,
    now,
    sha,
    uid,
    validate,
)
from packages.identity.repository import snapshot as identity_snapshot


def method_profile():
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    return {
        "engine": "core-review/2",
        "model": model,
        "provider_code_digest": sha((ROOT / "packages/domain/providers/deepseek.py").read_bytes()),
        "prompt_and_pipeline_digest": sha((ROOT / "apps/worker/review_handlers.py").read_bytes()),
        "independent_detector_digest": sha((ROOT / "apps/worker/content_handlers.py").read_bytes()),
        "assertion_and_scope_digest": sha(
            {
                name: sha((ROOT / name).read_bytes())
                for name in ("packages/domain/engineering.py", "packages/domain/content_checks.py")
            }
        ),
        "input_scope": "full_physical_page_text",
        "formal_scoring_eligible": False,
    }


def review_stages(checks):
    from packages.domain.content_checks import KINDS

    optional = [
        {"key": step, "depends_on": ["core-judgment"], "required": False}
        for kind, step in KINDS.items()
        if checks.get(kind, False)
    ]
    return [
        {"key": "core-judgment", "depends_on": []},
        *optional,
        {
            "key": "finalize-review",
            "depends_on": ["core-judgment"] + [stage["key"] for stage in optional],
        },
    ]


POLICY = {
    "version": "prebid-core/2",
    "complete_body_required": True,
    "source_closure_checked": True,
    "formal_scoring_route": "not_qualified",
    "optional_detectors": "independent_full_scope_with_engineering_validation",
}


def _operation(c, operation):
    row = fetch(c, "SELECT * FROM operations WHERE id=%s", (operation,))
    return clean(
        {
            key: row[key]
            for key in ("id", "bid_id", "kind", "input_digest", "state", "handler_version")
        }
        | {"result_ref": None}
    )


def _parse_operation(c, bid, material):
    row = c.execute(
        "SELECT o.id FROM operations o JOIN outbox x ON x.operation_id=o.id WHERE o.bid_id=%s AND o.kind='parse-materials' AND x.payload->>'payload_ref'=%s ORDER BY o.created_at DESC LIMIT 1",
        (bid, str(material)),
    ).fetchone()
    if row is None:
        raise DomainError("INGESTION_NOT_FOUND", "请先将当前资料批次明确入库", 409)
    return row["id"]


def queue_intent(c, bid, command_id, request, *, automatic=False):
    """调用方已经锁bid；自动/按钮/后续构建统一记录同一种请求。"""
    profile = fetch(
        c,
        "SELECT p.*,b.material_revision,b.settings FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s",
        (bid,),
    )
    if profile["current_material_id"] is None or profile["current_body_id"] is None:
        if automatic:
            return None
        raise DomainError("REVIEW_MATERIALS_REQUIRED", "请先入库招标资料和实际受检正文", 409)
    if (
        str(profile["current_body_id"]) != request["body_version_id"]
        or profile["material_revision"] != request["material_revision"]
    ):
        raise DomainError("REVIEW_INPUT_CHANGED", "当前稿件或资料已变化，请核对最新版本", 409)
    slot = fetch(c, "SELECT * FROM review_slots WHERE bid_id=%s FOR UPDATE", (bid,))
    parse = _parse_operation(c, bid, profile["current_material_id"])
    intent = {
        "request_id": str(command_id),
        "command_id": str(command_id),
        "trigger": request["trigger"],
        "body_version_id": request["body_version_id"],
        "material_snapshot_id": str(profile["current_material_id"]),
        "material_revision": request["material_revision"],
        "content_checks": {
            key: bool(profile["settings"].get(key, False))
            for key in ("consistency", "scope_residue")
        },
        "rerun_nonce": request.get("rerun_nonce"),
        "state": "preparing",
        "prepare_operation_id": str(parse),
        "preparation_kind": "parse",
        "prepared_rule_set_id": None,
        "review_id": None,
        "error_code": None,
        "gaps": [],
        "updated_at": now().isoformat(),
    }
    c.execute(
        "UPDATE review_slots SET pending_intent=%s,revision=revision+1 WHERE bid_id=%s",
        (Jsonb(intent), bid),
    )
    audit(
        c,
        None,
        bid,
        "review.requested",
        {
            "request_id": str(command_id),
            "body_version_id": request["body_version_id"],
            "material_snapshot_id": intent["material_snapshot_id"],
            "previous_request_id": (slot["pending_intent"] or {}).get("request_id"),
        },
        command_id,
    )
    value = accepted(_operation(c, parse), command_id, "pending_intent")
    value["disposition"] = "pending_latest" if slot["active_review_id"] else "created"
    return value


def _locked(c, bid):
    row = fetch(
        c,
        "SELECT b.*,p.current_material_id,p.current_body_id,p.current_rule_set_id,p.current_review_id FROM bids b JOIN bid_profiles p ON p.bid_id=b.id WHERE b.id=%s FOR UPDATE OF b",
        (bid,),
    )
    slot = fetch(c, "SELECT * FROM review_slots WHERE bid_id=%s FOR UPDATE", (bid,))
    return row, slot


def _same_head(bid, intent):
    return (
        str(bid["current_body_id"]) == intent["body_version_id"]
        and str(bid["current_material_id"]) == intent["material_snapshot_id"]
        and bid["material_revision"] == intent["material_revision"]
        and intent.get(
            "content_checks",
            {
                key: bool(bid["settings"].get(key, False))
                for key in ("consistency", "scope_residue")
            },
        )
        == {key: bool(bid["settings"].get(key, False)) for key in ("consistency", "scope_residue")}
    )


def _write_intent(c, bid, intent):
    intent["updated_at"] = now().isoformat()
    c.execute(
        "UPDATE review_slots SET pending_intent=%s,revision=revision+1 WHERE bid_id=%s",
        (Jsonb(clean(intent)), bid),
    )


def on_material_commit(c, bid_id, command_id):
    bid, slot = _locked(c, bid_id)
    if bid["settings"].get("auto_review", False):
        tender = c.execute(
            "SELECT 1 FROM material_assignments WHERE bid_id=%s AND snapshot_id=%s AND role='tender' LIMIT 1",
            (bid_id, bid["current_material_id"]),
        ).fetchone()
        if bid["current_body_id"] and tender:
            return queue_intent(
                c,
                bid_id,
                command_id,
                {
                    "body_version_id": str(bid["current_body_id"]),
                    "material_revision": bid["material_revision"],
                    "trigger": "automatic",
                },
                automatic=True,
            )
    intent = slot["pending_intent"]
    if intent and not intent.get("review_id") and intent["state"] in ("preparing", "blocked"):
        _advance_head(c, bid, intent)
    return None


def source_fingerprint(documents):
    return sha(
        clean(
            [
                {
                    key: doc[key]
                    for key in (
                        "document_version_id",
                        "parse_id",
                        "role",
                        "digest",
                        "parser_digest",
                        "config_digest",
                    )
                }
                for doc in documents
                if doc["role"] != "body"
            ]
        )
    )


def _advance_head(c, bid, intent):
    if _same_head(bid, intent):
        return False
    if not bid["settings"].get("auto_review", False):
        intent.update(
            state="superseded",
            error_code="REVIEW_INPUT_SUPERSEDED",
            gaps=["资料已更新，原请求已过期；请使用当前稿开始评审"],
        )
    else:
        intent.update(
            body_version_id=str(bid["current_body_id"]),
            material_snapshot_id=str(bid["current_material_id"]),
            material_revision=bid["material_revision"],
            content_checks={
                key: bool(bid["settings"].get(key, False))
                for key in ("consistency", "scope_residue")
            },
            prepare_operation_id=str(_parse_operation(c, bid["id"], bid["current_material_id"])),
            preparation_kind="parse",
            prepared_rule_set_id=None,
            state="preparing",
            error_code=None,
            gaps=["资料已更新，正在准备新版稿件"],
        )
    _write_intent(c, bid["id"], intent)
    return True


def prepare_intent(bid_id):
    with connection() as c:
        bid, slot = _locked(c, bid_id)
        intent = slot["pending_intent"]
        if not intent or intent["state"] != "preparing" or intent.get("review_id"):
            return
        if _advance_head(c, bid, intent):
            return
        if intent.get("prepared_rule_set_id"):
            return
        operation = fetch(
            c, "SELECT * FROM operations WHERE id=%s", (intent["prepare_operation_id"],)
        )
        if operation["state"] in ("queued", "running", "cancel_requested"):
            return
        if operation["state"] in ("failed", "cancelled"):
            effect = c.execute(
                "SELECT result FROM effect_receipts WHERE operation_id=%s ORDER BY created_at DESC LIMIT 1",
                (operation["id"],),
            ).fetchone()
            intent.update(
                state="blocked",
                error_code=effect["result"].get("error_code", "PREPARATION_FAILED")
                if effect
                else "PREPARATION_FAILED",
                gaps=["要求准备未完成；资料与输入已保留，请查看失败范围后重试并继续评审"],
            )
            _write_intent(c, bid_id, intent)
            return
        if intent["preparation_kind"] == "rules":
            effect = fetch(
                c,
                "SELECT result FROM effect_receipts WHERE operation_id=%s AND effect_key='extract-rules'",
                (operation["id"],),
            )["result"]
            rule_set = fetch(
                c,
                "SELECT * FROM rule_sets WHERE bid_id=%s AND id=%s",
                (bid_id, effect["result_ref"]),
            )
            if rule_set["coverage"] == "conflict":
                intent.update(
                    state="blocked",
                    error_code="SOURCE_EFFECTIVITY_AMBIGUOUS",
                    gaps=effect.get("ambiguities", []),
                )
            else:
                intent.update(prepared_rule_set_id=str(rule_set["id"]))
            _write_intent(c, bid_id, intent)
            return
        snapshot, request_id = intent["material_snapshot_id"], intent["request_id"]
    DeepSeekClient.from_env()  # 仅配置校验，不发模型请求。
    with connection() as c:
        documents = read_bundle(c, bid_id, snapshot)
    verify_bytes(documents)
    method = method_profile()
    with connection() as c:
        bid, slot = _locked(c, bid_id)
        intent = slot["pending_intent"]
        if (
            intent is None
            or intent["state"] != "preparing"
            or intent["request_id"] != request_id
            or not _same_head(bid, intent)
        ):
            return
        if sha(frozen_documents(read_bundle(c, bid_id, snapshot))) != sha(
            frozen_documents(documents)
        ):
            return
        refs = publish_sources(c, bid_id, documents)
        closure = source_closure(c, refs, lock=True)
        at = c.execute("SELECT clock_timestamp() AS at").fetchone()["at"]
        effective_at(closure, at)
        source_hash = source_fingerprint(documents)
        if bid["current_rule_set_id"]:
            previous = c.execute(
                "WITH RECURSIVE chain(id) AS (SELECT %s::text UNION SELECT a.payload->>'from_rule_set_id' FROM chain x JOIN audit_entries a ON a.payload->>'rule_set_id'=x.id WHERE a.bid_id=%s AND a.event_type IN('rules.corrected','rules.interpretation_rejected','sources.effectivity_decided','rules.source_equivalent_cloned') AND a.payload->>'from_rule_set_id' IS NOT NULL) SELECT e.operation_id,e.result FROM effect_receipts e JOIN operations o ON o.id=e.operation_id JOIN chain ON e.result->>'result_ref'=chain.id WHERE o.bid_id=%s AND e.effect_key='extract-rules' ORDER BY e.created_at DESC LIMIT 1",
                (str(bid["current_rule_set_id"]), bid_id, bid_id),
            ).fetchone()
            if (
                previous
                and previous["result"].get("source_fingerprint") == source_hash
                and previous["result"].get("method_digest") == sha(method)
            ):
                old_set = fetch(
                    c, "SELECT * FROM rule_sets WHERE id=%s", (bid["current_rule_set_id"],)
                )
                if old_set["coverage"] == "conflict":
                    intent.update(
                        state="blocked",
                        error_code="SOURCE_EFFECTIVITY_AMBIGUOUS",
                        gaps=previous["result"].get("ambiguities", ["请核对生效要求"]),
                    )
                    _write_intent(c, bid_id, intent)
                    return
                new_set = (
                    old_set["id"]
                    if str(old_set["material_id"]) == str(snapshot)
                    else create_extracted_set(
                        c,
                        bid_id,
                        snapshot,
                        [dict(row) for row in rules(c, old_set["id"])],
                        old_set["coverage"],
                        documents,
                    )
                )
                if new_set != old_set["id"]:
                    audit(
                        c,
                        None,
                        bid_id,
                        "rules.source_equivalent_cloned",
                        {
                            "from_rule_set_id": str(old_set["id"]),
                            "rule_set_id": str(new_set),
                            "material_snapshot_id": snapshot,
                            "source_fingerprint": source_hash,
                            "method_digest": sha(method),
                            "extraction_operation_id": str(previous["operation_id"]),
                            "extraction_effect_digest": sha(previous["result"]),
                        },
                        intent["command_id"],
                    )
                intent["prepared_rule_set_id"] = str(new_set)
                _write_intent(c, bid_id, intent)
                return
        descriptor = {
            "base_rule_set_id": str(bid["current_rule_set_id"])
            if bid["current_rule_set_id"]
            else None,
            "bid_id": str(bid_id),
            "material_snapshot_id": snapshot,
            "source_refs": refs,
            "source_closure": closure,
            "documents": frozen_documents(documents),
            "as_of": at.isoformat(),
            "method": method,
            "method_digest": sha(method),
        }
        preparation_digest = sha(
            {
                "source_fingerprint": source_hash,
                "source_closure": closure,
                "method": method,
                "base_rule_set_id": descriptor["base_rule_set_id"],
            }
        )
    blob = save_json(descriptor)
    with connection() as c:
        bid, slot = _locked(c, bid_id)
        intent = slot["pending_intent"]
        if (
            not intent
            or intent["state"] != "preparing"
            or intent["request_id"] != request_id
            or not _same_head(bid, intent)
        ):
            return
        if sha(source_closure(c, refs, lock=True)) != sha(closure):
            return
        previous = c.execute(
            "SELECT id FROM operations WHERE bid_id=%s AND kind='prepare-review' AND input_digest=%s AND state IN('queued','running')",
            (bid_id, preparation_digest),
        ).fetchone()
        if previous:
            operation_id = previous["id"]
        else:
            assert_preparation_reconciled(c, bid_id, preparation_digest)
            operation_id = enqueue_plan(
                c,
                bid_id,
                intent["command_id"],
                "prepare-review",
                {"id": blob["id"]},
                stages=[{"key": "extract-rules", "depends_on": []}],
                handler_version="prepare/1",
                topic="tender.task.review.v1",
                input_digest=preparation_digest,
            )["id"]
        intent.update(prepare_operation_id=str(operation_id), preparation_kind="rules")
        _write_intent(c, bid_id, intent)


def _facts(c, bid):
    head = c.execute("SELECT * FROM fact_heads WHERE bid_id=%s", (bid,)).fetchone()
    if not head or not head["fact_set_id"]:
        return None, []
    rows = c.execute(
        "SELECT f.* FROM fact_items f JOIN decision_heads h ON h.bid_id=f.bid_id AND h.object_kind='fact' AND h.object_id=f.id JOIN decisions d ON d.id=h.decision_id WHERE f.fact_set_id=%s AND d.decision='confirmed' ORDER BY f.id",
        (head["fact_set_id"],),
    ).fetchall()
    from packages.domain.fact_sets import decision_basis, evidence_basis, identity
    from packages.domain.rule_sets import current_decision

    for fact in rows:
        decision = current_decision(c, bid, "fact", fact["id"])
        try:
            basis = {
                "fact_identity": identity(fact),
                "evidence": evidence_basis(c, bid, fact["evidence_ids"], publish=False),
            }
        except (DomainError, ValueError):
            raise DomainError(
                "FACT_SOURCE_REVALIDATION_NEEDED", "事实依据已变化，请逐项核对后保存并确认事实", 409
            ) from None
        if not decision or decision_basis(c, decision["id"]) != basis:
            raise DomainError(
                "FACT_SOURCE_REVALIDATION_NEEDED", "已确认事实的来源身份已变化，请重新核验", 409
            )
    return str(head["fact_set_id"]), clean(rows)


def freeze_candidate(c, bid_id):
    bid, slot = _locked(c, bid_id)
    intent = slot["pending_intent"]
    if (
        not intent
        or intent["state"] != "preparing"
        or not intent.get("prepared_rule_set_id")
        or not _same_head(bid, intent)
    ):
        return None
    if slot["active_review_id"]:
        active = fetch(
            c,
            "SELECT o.state FROM review_runs r JOIN operations o ON o.id=r.operation_id WHERE r.id=%s",
            (slot["active_review_id"],),
        )
        if active["state"] in ("queued", "running", "cancel_requested"):
            return None
    if str(bid["current_rule_set_id"]) != str(intent["prepared_rule_set_id"]):
        raise DomainError("RULE_SET_CHANGED", "本轮要求已经更正，请使用当前要求重新评审", 409)
    documents = read_bundle(c, bid_id, intent["material_snapshot_id"])
    rule_set = fetch(
        c,
        "SELECT * FROM rule_sets WHERE id=%s AND bid_id=%s AND material_id=%s",
        (intent["prepared_rule_set_id"], bid_id, intent["material_snapshot_id"]),
    )
    fact_id, facts = _facts(c, bid_id)
    documents.extend(read_fact_documents(c, bid_id, facts, documents))
    refs = publish_sources(c, bid_id, documents)
    closure = source_closure(c, refs, lock=True)
    t = c.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
    effective_at(closure, t)
    method = method_profile()
    rule_items = clean(rules(c, rule_set["id"]))
    root = {
        "identity": identity_snapshot(c, bid_id),
        "documents": frozen_documents(documents),
        "rules": rule_items,
        "rule_set": clean(rule_set),
        "confirmed_facts": facts,
        "fact_set_id": fact_id,
        "source_closure": closure,
        "method": method,
        "policy": POLICY,
    }
    content_checks = {
        name: bool(bid["settings"].get(name, False)) for name in ("consistency", "scope_residue")
    }
    fingerprint = sha({"root": root, "checks": content_checks})
    boundary_times = [
        datetime.fromisoformat(row[name])
        for row in closure
        for name in ("known_at", "valid_from", "valid_until")
        if row[name] and datetime.fromisoformat(row[name]) > t
    ]
    valid_until = min(boundary_times) if boundary_times else None
    same = slot["context_fingerprint"] == fingerprint and (
        slot["context_valid_until"] is None or t < slot["context_valid_until"]
    )
    as_of = slot["context_as_of"] if same else t
    context = {
        "fingerprint": fingerprint,
        "identity_digest": root["identity"]["digest"],
        "as_of": as_of.isoformat(),
        "valid_until": valid_until.isoformat() if valid_until else None,
    }
    c.execute(
        "UPDATE review_slots SET context_fingerprint=%s,context_as_of=%s,context_valid_until=%s WHERE bid_id=%s",
        (fingerprint, as_of, valid_until, bid_id),
    )
    root["evaluation_context"] = context
    for ref in refs:
        own = next(
            row for row in closure if row["id"] == ref["id"] and row["source_id"] == ref["id"]
        )
        ref.pop("document_version_id")
        ref.update(
            known_at=datetime.fromisoformat(own["known_at"]).isoformat(),
            lineage_manifest_digest=sha([row for row in closure if row["source_id"] == ref["id"]]),
        )
    manifest = {
        "bid_id": str(bid_id),
        "body_version_id": intent["body_version_id"],
        "material_snapshot_id": intent["material_snapshot_id"],
        "material_revision": intent["material_revision"],
        "rule_set_id": str(rule_set["id"]),
        "fact_set_id": fact_id,
        "parse_run_ids": sorted(str(d["parse_id"]) for d in documents),
        "method_digest": sha(method),
        "policy_digest": sha(POLICY),
        "purpose": "compliance_and_scoring",
        "source_refs": refs,
        "content_checks": content_checks,
        "as_of": as_of.isoformat(),
        "evaluation_context_digest": sha(context),
    }
    return {
        "request_id": intent["request_id"],
        "root": root,
        "manifest": manifest,
        "fingerprint": fingerprint,
        "rerun_nonce": intent.get("rerun_nonce"),
    }


def freeze_intent(bid_id):
    with connection() as c:
        candidate = freeze_candidate(c, bid_id)
    if candidate is None:
        return
    # 实际字节核验和不可变证据根上传都在bid/source锁外。
    from packages.domain.review_data import restore_documents

    with connection() as c:
        documents = restore_documents(c, candidate["root"]["documents"])
    verify_bytes(documents)
    blob = save_json(candidate["root"])
    with connection() as c:
        current = freeze_candidate(c, bid_id)
        if (
            current is None
            or current["request_id"] != candidate["request_id"]
            or sha(current["root"]) != sha(candidate["root"])
        ):
            return
        slot = fetch(c, "SELECT * FROM review_slots WHERE bid_id=%s FOR UPDATE", (bid_id,))
        intent = slot["pending_intent"]
        manifest = current["manifest"] | {
            "frozen_at": c.execute("SELECT clock_timestamp() AS t").fetchone()["t"].isoformat(),
            "evidence_manifest_digest": blob["digest"],
        }
        validate("ReviewManifest", manifest)
        keys = identities(manifest, current["rerun_nonce"])
        previous = c.execute(
            "SELECT r.id,r.operation_id FROM review_runs r JOIN review_inputs i ON i.id=r.input_id JOIN operations o ON o.id=r.operation_id WHERE r.bid_id=%s AND o.input_digest=%s AND o.state IN('queued','running','succeeded') ORDER BY r.created_at DESC LIMIT 1",
            (bid_id, keys["execution_digest"]),
        ).fetchone()
        if previous:
            review, operation = previous["id"], previous["operation_id"]
        else:
            assert_review_reconciled(c, bid_id, keys["input_digest"])
            input_id, review = uid(), uid()
            c.execute(
                "INSERT INTO review_inputs(id,bid_id,material_snapshot_id,body_version_id,input_digest,method_digest,manifest,frozen_at,evidence_manifest_blob_id,manifest_digest,as_of,evaluation_context_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    input_id,
                    bid_id,
                    manifest["material_snapshot_id"],
                    manifest["body_version_id"],
                    keys["input_digest"],
                    manifest["method_digest"],
                    Jsonb(manifest),
                    manifest["frozen_at"],
                    blob["id"],
                    keys["manifest_digest"],
                    manifest["as_of"],
                    manifest["evaluation_context_digest"],
                ),
            )
            operation = enqueue_plan(
                c,
                bid_id,
                intent["command_id"],
                "review",
                {"id": review},
                stages=review_stages(manifest["content_checks"]),
                handler_version="review/1",
                topic="tender.task.review.v1",
                input_digest=keys["execution_digest"],
            )["id"]
            c.execute(
                "INSERT INTO review_runs(id,bid_id,input_id,operation_id) VALUES(%s,%s,%s,%s)",
                (review, bid_id, input_id, operation),
            )
            from packages.domain.finding_lifecycle import retain_critical

            retain_critical(c, bid_id, review)
        intent.update(state="queued", review_id=str(review), error_code=None, gaps=[])
        _write_intent(c, bid_id, intent)
        c.execute("UPDATE review_slots SET active_review_id=%s WHERE bid_id=%s", (review, bid_id))
        c.execute("UPDATE bid_profiles SET current_review_id=%s WHERE bid_id=%s", (review, bid_id))
        audit(
            c,
            None,
            bid_id,
            "review.frozen",
            {
                "review_id": str(review),
                "request_id": intent["request_id"],
                "manifest_digest": keys["manifest_digest"],
            },
        )


def tick(bid_id):
    with connection() as c:
        bid, slot = _locked(c, bid_id)
        intent = slot["pending_intent"]
        if not intent:
            return
        if slot["active_review_id"]:
            active = fetch(
                c,
                "SELECT o.* FROM review_runs r JOIN operations o ON o.id=r.operation_id WHERE r.id=%s",
                (slot["active_review_id"],),
            )
            if active["state"] in ("succeeded", "failed", "cancelled"):
                c.execute(
                    "UPDATE review_slots SET active_review_id=NULL WHERE bid_id=%s", (bid_id,)
                )
            if intent.get("review_id") == str(slot["active_review_id"]):
                state = (
                    "completed"
                    if active["state"] == "succeeded"
                    else "blocked"
                    if active["state"] in ("failed", "cancelled")
                    else active["state"]
                )
                if state != intent["state"]:
                    intent.update(
                        state=state,
                        error_code="REVIEW_EXECUTION_FAILED" if state == "blocked" else None,
                        gaps=["本轮核对未完成，请查看矩阵中失败或未覆盖的范围"]
                        if state == "blocked"
                        else [],
                    )
                    _write_intent(c, bid_id, intent)
    prepare_intent(bid_id)
    freeze_intent(bid_id)
