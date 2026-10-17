"""Independent A1.2 API cases against current frozen runtime and synthetic evidence."""

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from packages.domain.runtime import DomainError, connection, sha
from packages.rag.indexing import original_source_id


def run_cases(
    *,
    client,
    bid,
    headers,
    check,
    calls,
    objects,
    out,
    request,
    finish_request,
    seed_doc,
    review_id,
    view,
    matrix,
    tender,
):
    def get(path):
        response = client.get("/api/v1" + path)
        assert response.status_code == 200
        return response

    def post(path, body, tag, key=None):
        return client.post("/api/v1" + path, json=body, headers=headers(tag, key))

    def rules():
        return get(f"/bids/{bid}/rules")

    (out / "executed-human-cases.py").write_bytes(Path(__file__).read_bytes())
    initial_calls = len(calls)
    mandatory = next(r["rule"] for r in matrix["rows"] if r["rule"]["kind"] == "mandatory")
    disqualification = next(
        r["rule"] for r in matrix["rows"] if r["rule"]["kind"] == "disqualification"
    )
    scoring = next(r["rule"] for r in matrix["rows"] if r["rule"]["kind"] == "scoring")
    path = f"/rules/{mandatory['id']}/decisions"
    decision = {"decision": "confirmed", "reason": "人工核对合成原页", "evidence_ids": []}
    current = get(f"/rules/{mandatory['id']}")
    missing = post(path, decision, None)
    invalid = post(path, decision | {"decision": "confirm"}, current.headers["etag"])
    key = uuid4().hex
    approved = post(path, decision, current.headers["etag"], key)
    replay = post(path, decision, current.headers["etag"], key)
    stale = post(path, decision, current.headers["etag"])
    check(
        "rule_confirmation_cas_kind_and_exact_replay",
        missing.status_code == 428
        and invalid.status_code == 422
        and approved.status_code == 200
        and replay.status_code == 200
        and replay.json() == approved.json()
        and stale.status_code == 412,
    )
    deferred = get(f"/rules/{disqualification['id']}")
    deferred = post(
        f"/rules/{disqualification['id']}/decisions",
        decision | {"decision": "deferred"},
        deferred.headers["etag"],
    )
    latest = get(f"/reviews/{review_id}").json()
    check(
        "confirmed_deferred_change_only_human_layer_without_model",
        deferred.status_code == 200
        and latest["current"]
        and latest["input_digest"] == view["input_digest"]
        and latest["coverage_layers"]["human_confirmation_pending"] == 2
        and len(calls) == initial_calls,
    )
    selected = rules()
    batch = post(
        f"/bids/{bid}/rules/decisions",
        dict(
            base_rule_set_id=selected.json()["id"],
            rule_ids=[scoring["id"], mandatory["id"]],
            reason="Independent atomic batch",
        ),
        selected.headers["etag"],
    )
    check(
        "batch_rejects_already_confirmed_without_partial_write",
        batch.status_code == 409
        and get(f"/rules/{scoring['id']}").json()["confirmation"] == "pending",
    )
    with connection() as c:
        quoted = c.execute(
            "SELECT id,text_content FROM evidence_blocks WHERE bid_id=%s AND text_content LIKE 'A site visit is required.%%'",
            (bid,),
        ).fetchone()
    addition = dict(
        kind="mandatory",
        statement="A site visit is required.",
        conditions=[],
        exceptions=[],
        scope=dict(bid_id=bid, description="Current synthetic project"),
        maximum=None,
        bands=[],
        evidence_quotes=[dict(evidence_id=str(quoted["id"]), quote="A site visit is required.")],
    )
    selected = rules()
    body = dict(
        base_rule_set_id=selected.json()["id"],
        reason="补充并确认模型漏提的有据要求",
        additions=[addition],
    )
    invalid_addition = {
        **addition,
        "evidence_quotes": [dict(evidence_id=str(quoted["id"]), quote="NOT IN ORIGINAL")],
    }
    bad = post(
        f"/bids/{bid}/rule-set-corrections",
        body | {"additions": [addition, invalid_addition]},
        selected.headers["etag"],
    )
    check(
        "invalid_second_addition_rolls_back_entire_manual_pack",
        bad.status_code == 422 and rules().json()["id"] == selected.json()["id"],
    )
    correction_key = uuid4().hex
    added = post(
        f"/bids/{bid}/rule-set-corrections", body, selected.headers["etag"], correction_key
    )
    assert added.status_code == 201
    repeated = post(
        f"/bids/{bid}/rule-set-corrections", body, selected.headers["etag"], correction_key
    )
    manual = next(r for r in added.json()["rules"] if r["origin_kind"] == "human_added")
    check(
        "manual_add_and_confirm_atomic_new_pack_no_auto_model",
        manual["confirmation"] == "confirmed"
        and manual["manual_origin_rule_id"] == manual["id"]
        and added.json()["id"] != selected.json()["id"]
        and not get(f"/reviews/{review_id}").json()["current"]
        and len(calls) == initial_calls
        and repeated.status_code == 201
        and repeated.json() == added.json(),
    )
    old_tag = post(f"/bids/{bid}/rule-set-corrections", body, selected.headers["etag"])
    check("stale_manual_pack_cannot_overwrite_current_pack", old_tag.status_code == 412)
    request(uuid4())
    finish_request()
    retained = [r for r in rules().json()["rules"] if r["manual_origin_rule_id"] == manual["id"]]
    check(
        "reextraction_omission_preserves_human_rule_and_confirmation",
        len(retained) == 1
        and retained[0]["confirmation"] == "confirmed"
        and retained[0]["origin_kind"] == "carried_human"
        and len(calls) == initial_calls + 2,
    )
    # A fact proof is deliberately not in the material snapshot. It must still enter freeze.
    proof_version, proof_parse, _ = seed_doc(
        "fact-proof",
        ["Independent synthetic certificate QA123.", "Certificate QA123 applies to this project."],
    )
    with connection() as c:
        proof = c.execute(
            "SELECT id FROM evidence_blocks WHERE parse_id=%s ORDER BY physical_page DESC LIMIT 1",
            (proof_parse,),
        ).fetchone()["id"]
    facts = get(f"/bids/{bid}/facts")
    fact = dict(
        subject="本投标人",
        scope="当前合成项目",
        name="合成证书编号",
        value="QA123",
        unit=None,
        evidence_ids=[str(proof)],
        reason="保存并确认合成证书原页",
    )
    changed = post(
        f"/bids/{bid}/facts", dict(fact=fact, replaces_fact_id=None), facts.headers["etag"]
    )
    assert changed.status_code == 200
    first_fact = changed.json()["facts"][0]
    check(
        "manual_fact_save_confirms_once_atomically",
        first_fact["confirmation"] == "confirmed"
        and first_fact["decision"]["decision"] == "confirmed"
        and first_fact["carryover"] is None,
    )
    facts = get(f"/bids/{bid}/facts")
    extra = {
        **fact,
        "name": "合成证书说明",
        "value": "Current project scope",
        "reason": "人工补充并确认另一事实",
    }
    more = post(
        f"/bids/{bid}/facts", dict(fact=extra, replaces_fact_id=None), facts.headers["etag"]
    )
    assert more.status_code == 200
    cloned = next(f for f in more.json()["facts"] if f["name"] == fact["name"])
    extra_fact = next(f for f in more.json()["facts"] if f["name"] == extra["name"])
    check(
        "unchanged_fact_confirmation_carryover_explicit",
        cloned["id"] != first_fact["id"]
        and cloned["confirmation"] == "confirmed"
        and cloned["carryover"]["from_fact_id"] == first_fact["id"]
        and cloned["carryover"]["from_decision_id"] == first_fact["decision"]["id"],
    )
    previous_calls = len(calls)
    request(uuid4())
    with_facts = finish_request()
    frozen_review = with_facts["request"]["review_id"]
    manifest = get(f"/reviews/{frozen_review}/manifest").json()
    with connection() as c:
        frozen = c.execute(
            "SELECT b.* FROM review_inputs i JOIN blobs b ON b.id=i.evidence_manifest_blob_id WHERE i.bid_id=%s AND i.manifest_digest=%s",
            (bid, get(f"/reviews/{frozen_review}").json()["manifest_digest"]),
        ).fetchone()
    root = json.loads(objects[frozen["object_key"]])
    proof_doc = next(
        (d for d in root["documents"] if d["document_version_id"] == str(proof_version)), None
    )
    check(
        "confirmed_fact_proof_original_pages_parse_frozen_in_root",
        proof_doc is not None
        and proof_doc["parse_id"] == str(proof_parse)
        and len(proof_doc["pages"]) == 2
        and str(proof_parse) in manifest["parse_run_ids"]
        and len(root["confirmed_facts"]) == 2
        and sha(objects[frozen["object_key"]]) == manifest["evidence_manifest_digest"]
        and len(calls) == previous_calls + 1,
    )
    facts = get(f"/bids/{bid}/facts")
    withdrawn = post(
        f"/facts/{extra_fact['id']}/withdrawals",
        dict(reason="撤回本次合成说明"),
        facts.headers["etag"],
    )
    assert withdrawn.status_code == 200
    check(
        "fact_withdrawal_new_set_keeps_original_history_and_no_model",
        len(withdrawn.json()["facts"]) == 1
        and withdrawn.json()["revision"] > facts.json()["revision"]
        and get(f"/facts/{extra_fact['id']}").status_code == 200
        and len(calls) == previous_calls + 1,
    )
    live_fact = withdrawn.json()["facts"][0]
    before_calls = len(calls)
    with connection() as c:
        c.execute(
            "UPDATE retrieval.source_heads SET status='withdrawn',revision=revision+1 WHERE source_id=%s",
            (original_source_id(UUID(str(proof_version))),),
        )
    invalidated = get(f"/facts/{live_fact['id']}").json()
    check(
        "withdrawn_ancestor_invalidates_live_fact_confirmation",
        invalidated["confirmation"] == "pending"
        and invalidated["source_validity"] == "needs_revalidation"
        and invalidated["decision"]["decision"] == "confirmed",
    )
    request(uuid4())
    try:
        from packages.domain import reviews

        reviews.tick(UUID(bid))
        blocked = False
    except DomainError as error:
        blocked = error.code == "FACT_SOURCE_REVALIDATION_NEEDED"
    check(
        "stale_confirmed_fact_proof_blocks_new_freeze_no_model",
        blocked and len(calls) == before_calls,
    )
    (out / "executed-human-cases.py").write_bytes(Path(__file__).read_bytes())
    return dict(
        human_cases_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        first_human_rule=manual,
        retained_human_rule=retained,
        confirmed_fact_root_manifest=manifest,
        invalidated_fact=invalidated,
        offline_transport_requests=len(calls),
    )
