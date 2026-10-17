"""Independent fixture/command/payload boundary checks, actual restricted PG; no models."""

import copy
import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from apps.worker import engine  # noqa: F401 -- verify actually loaded source identities
from deploy import review_acceptance as tool
from packages.domain import model_calls, reviews  # noqa: F401
from packages.domain.review_data import frozen_documents, read_bundle
from packages.domain.rule_sets import rules
from packages.domain.runtime import DomainError, assert_role, clean, connection, sha
from psycopg.types.json import Jsonb


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(os.environ["QA_GUARD_ROOT"])
    out = root / os.environ.get("QA_GUARD_EXECUTION", "scope-delta-execution")
    out.mkdir(exist_ok=False)
    manifest = root / "runtime-candidate.json"
    manifest_sha = "000aa789b2c0f31e530323022795529823b2fddffbaf1280a532a2fe7c1806ff"
    assert digest(manifest) == manifest_sha
    assert digest(Path(tool.__file__)) == "26587edab50b7d903f919fbab5a76d83eaa9466f0c539706372d5ffc65eee1fd"
    assert Path(tool.__file__).is_relative_to(root / "execution-inputs")
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
    assert_role(True)
    bid = UUID("31e41441-d92b-4dfa-ad24-57532ab73811")
    result = dict(passed=False, checks=[], paid_generation_calls=0, real_kafka_consumers_started=0,
                  execute_entry_invocations=0, fixture_bid_id=str(bid),
                  scope="Actual restricted PG, own prior offline fixture; direct guard and fake upstream, no engine execution or external model")

    def check(name, value, observation=None):
        result["checks"].append(dict(name=name, passed=bool(value), observation=observation))

    def denied(fn, code):
        try:
            fn()
        except DomainError as error:
            return error.code == code
        return False

    def user(payload):
        return [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]

    def save(name, data):
        path = out / (name + ".json")
        path.write_text(json.dumps(clean(data), ensure_ascii=False, indent=2) + "\n")
        return path, digest(path)

    try:
        verify = tool.frozen_sources(manifest, manifest_sha)
        verify()
        with connection(True) as c:
            identity = c.execute("SELECT current_user,rolsuper FROM pg_roles WHERE rolname=current_user").fetchone()
            profile = c.execute("SELECT p.current_body_id,p.current_material_id,b.material_revision FROM bid_profiles p JOIN bids b ON b.id=p.bid_id WHERE p.bid_id=%s", (bid,)).fetchone()
            documents = frozen_documents(read_bundle(c, bid, profile["current_material_id"]))
            rows = c.execute("SELECT DISTINCT ON(o.handler_version) o.*,b.payload FROM operations o JOIN outbox b ON b.operation_id=o.id WHERE o.bid_id=%s AND o.handler_version IN ('prepare/1','review/1') ORDER BY o.handler_version,o.created_at,b.available_at", (bid,)).fetchall()
            by_handler = {row["handler_version"]: row for row in rows}
            prep, core = by_handler["prepare/1"], by_handler["review/1"]
            command = core["command_id"]
            assert prep["command_id"] == command
            prepared = c.execute("SELECT result FROM effect_receipts WHERE operation_id=%s AND effect_key='extract-rules'", (prep["id"],)).fetchone()
            rule_set_id = prepared["result"]["result_ref"]
            core_rules = clean([r for r in rules(c, rule_set_id) if r["kind"] != "scoring" and r["provenance_status"] == "effective"])
            old_model_calls = c.execute("SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s", (bid,)).fetchone()["n"]
        fixture, fixture_sha = save("own-approved-fixture", dict(bid_id=str(bid), heads=profile, documents=documents))
        scope = tool.FixtureScope(fixture, fixture_sha, bid, command, connection)
        body = next(d for d in documents if d["role"] == "body")
        extract = dict(bid_id=str(bid), documents=[dict(role=d["role"], document_version_id=d["document_version_id"], coverage=d["coverage"], gaps=d["gaps"], pages=d["pages"], evidence_blocks=d["blocks"]) for d in documents if d["role"] != "body"])
        payload = dict(rules=core_rules, confirmed_facts=[], full_body_pages=body["pages"], full_body_blocks=body["blocks"], known_body_gaps=body["gaps"])
        pm, cm = prep["payload"], core["payload"]
        scope.verify(pm, "extract-rules", user(extract))
        scope.verify(cm, "core-judgment", user(payload))
        check("actual_nonowner_and_exact_three_physical_pages_and_prepared_rules_accepted", not identity["rolsuper"] and len(body["pages"]) == 3 and len(core_rules) == 2)

        for name, mutate in [
            ("replace_full_body_text", lambda p: p["full_body_blocks"][0].update(text_content="Different body 999 days")),
            ("omit_blank_physical_page", lambda p: p["full_body_pages"].pop(1)),
            ("omit_last_page_block", lambda p: p["full_body_blocks"].pop()),
            ("add_unapproved_fact", lambda p: p["confirmed_facts"].append({"name": "Unapproved", "value": 90})),
            ("add_extra_payload_field", lambda p: p.update(unapproved_context="Another document")),
            ("replace_prepared_rule_statement", lambda p: p["rules"][0].update(statement="Invented rule")),
            ("drop_prepared_rule", lambda p: p["rules"].pop()),
            ("change_evidence_id", lambda p: p["full_body_blocks"][0].update(id=str(uuid4()))),
            ("change_known_gap", lambda p: p["known_body_gaps"].append({"code": "fake"})),
        ]:
            altered = copy.deepcopy(payload)
            mutate(altered)
            check(name + "_rejected", denied(lambda: scope.verify(cm, "core-judgment", user(altered)), "ACCEPTANCE_PAYLOAD_MISMATCH"))
        altered = copy.deepcopy(extract)
        altered["documents"][0]["pages"].pop()
        check("omit_tender_page_rejected", denied(lambda: scope.verify(pm, "extract-rules", user(altered)), "ACCEPTANCE_PAYLOAD_MISMATCH"))
        check("two_user_payloads_rejected", denied(lambda: scope.verify(cm, "core-judgment", user(payload) + user(payload)), "ACCEPTANCE_PAYLOAD_MISMATCH"))
        check("unrelated_review_run_ref_cannot_supply_rules_chain", denied(lambda: scope.verify(dict(cm, payload_ref=str(uuid4())), "core-judgment", user(payload)), "ACCEPTANCE_PAYLOAD_MISMATCH"))
        check("actual_operation_input_digest_rechecked", denied(lambda: scope.verify(dict(cm, input_digest="0" * 64), "core-judgment", user(payload)), "ACCEPTANCE_COMMAND_MISMATCH"))
        check("actual_handler_rechecked", not scope.accepts_operation(dict(cm, handler_version="prepare/1")))
        wrong_scope = tool.FixtureScope(fixture, fixture_sha, bid, uuid4(), connection)
        check("same_bid_other_approved_command_rejected", denied(lambda: wrong_scope.verify(cm, "core-judgment", user(payload)), "ACCEPTANCE_COMMAND_MISMATCH"))
        check("foreign_operation_rejected", not scope.accepts_operation(dict(cm, operation_id=str(uuid4()))))
        fixture.write_text(fixture.read_text() + " ")
        check("fixture_bytes_rechecked_at_each_send", denied(lambda: scope.verify(cm, "core-judgment", user(payload)), "ACCEPTANCE_FIXTURE_CHANGED"))
        fixture.write_text(fixture.read_text()[:-1])

        sent = []
        boundary = tool.AcceptanceBoundary(bid, lambda *a, **kw: sent.append(kw["logical_key"]), verify, scope=scope)
        bad = copy.deepcopy(payload)
        bad["confirmed_facts"] = [{"forbidden": True}]
        check("scope_rejection_does_not_enter_upstream_or_consume_window", denied(lambda: boundary(cm, {}, logical_key="core-judgment", messages=user(bad), json_schema={}, max_tokens=8192), "ACCEPTANCE_PAYLOAD_MISMATCH") and boundary.started == 0 and sent == [])

        def race(i):
            key, message, data = ("extract-rules", pm, extract) if i % 2 else ("core-judgment", cm, payload)
            try:
                boundary(message, {}, logical_key=key, messages=user(data), json_schema={}, max_tokens=8192)
                return True
            except DomainError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = list(pool.map(race, range(16)))
        check("parallel_exact_payloads_each_logical_key_at_most_once", sum(accepted) == 2 and boundary.started == 2 and sorted(sent) == ["core-judgment", "extract-rules"])
        check("foreign_bid_rejected_before_upstream", denied(lambda: boundary(dict(cm, bid_id=str(uuid4())), {}, logical_key="core-judgment", messages=user(payload), json_schema={}, max_tokens=8192), "ACCEPTANCE_SCOPE_REJECTED") and len(sent) == 2)

        # Mutate only this independent prior synthetic bid, restoring heads/rules in finally.
        original_body = profile["current_body_id"]
        tender = next(d for d in documents if d["role"] == "tender")
        try:
            with connection(True) as c:
                c.execute("UPDATE bid_profiles SET current_body_id=%s WHERE bid_id=%s", (UUID(tender["document_version_id"]), bid))
            check("same_bid_current_body_head_drift_rejected", denied(lambda: scope.verify(cm, "core-judgment", user(payload)), "ACCEPTANCE_INPUT_CHANGED"))
        finally:
            with connection(True) as c:
                c.execute("UPDATE bid_profiles SET current_body_id=%s WHERE bid_id=%s", (original_body, bid))
        try:
            with connection() as c:
                c.execute("UPDATE bids SET material_revision=material_revision+1 WHERE id=%s", (bid,))
            check("same_bid_material_revision_drift_rejected", denied(lambda: scope.verify(cm, "core-judgment", user(payload)), "ACCEPTANCE_INPUT_CHANGED"))
        finally:
            with connection() as c:
                c.execute("UPDATE bids SET material_revision=%s WHERE id=%s", (profile["material_revision"], bid))
        with connection() as c:
            prior_facts = c.execute("SELECT * FROM fact_heads WHERE bid_id=%s", (bid,)).fetchone()
            fs = uuid4()
            c.execute("INSERT INTO fact_sets(id,bid_id,digest) VALUES(%s,%s,%s)", (fs, bid, sha(str(fs).encode())))
            c.execute("INSERT INTO fact_heads(bid_id,fact_set_id,revision) VALUES(%s,%s,1) ON CONFLICT(bid_id) DO UPDATE SET fact_set_id=excluded.fact_set_id,revision=1", (bid, fs))
        try:
            check("real_fact_head_even_empty_fact_set_rejected", denied(lambda: scope.verify(cm, "core-judgment", user(payload)), "ACCEPTANCE_INPUT_CHANGED"))
        finally:
            with connection() as c:
                c.execute("UPDATE fact_heads SET fact_set_id=%s,revision=%s WHERE bid_id=%s", (prior_facts["fact_set_id"] if prior_facts else None, prior_facts["revision"] if prior_facts else 0, bid))
        # Immutable records remain immutable: construct separate explicitly synthetic
        # command/input/effect rows rather than editing a historical rule or receipt.
        def clone(c, table, identity, changes):
            c.execute(f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table},to_jsonb(t)||%s)).* FROM {table} t WHERE id=%s", (Jsonb(clean(changes)), identity))

        def alternate_chain(mode):
            cmd, po, ro, ri, rr = (uuid4() for _ in range(5))
            with connection() as c:
                clone(c, "commands", command, dict(id=cmd, idempotency_key="qa-guard03-"+str(cmd), request_digest=sha(str(cmd).encode())))
                clone(c, "operations", core["id"], dict(id=ro, command_id=cmd, input_digest=sha(str(ro).encode())))
                original_run = c.execute("SELECT input_id FROM review_runs WHERE id=%s", (UUID(cm["payload_ref"]),)).fetchone()
                manifest = c.execute("SELECT manifest FROM review_inputs WHERE id=%s", (original_run["input_id"],)).fetchone()["manifest"]
                expected = core_rules
                target_set = rule_set_id
                if mode == "human":
                    target_set = uuid4()
                    clone(c, "rule_sets", UUID(rule_set_id), dict(id=target_set))
                    for index, rule in enumerate(rules(c, rule_set_id)):
                        identity = uuid4()
                        changes = dict(id=identity, rule_set_id=target_set)
                        if index == 0:
                            changes.update(origin_kind="human_added", manual_origin_rule_id=identity)
                        clone(c, "rule_items", rule["id"], changes)
                    manifest = dict(manifest, rule_set_id=str(target_set))
                    expected = clean([r for r in rules(c, target_set) if r["kind"] != "scoring" and r["provenance_status"] == "effective"])
                clone(c, "review_inputs", original_run["input_id"], dict(id=ri, manifest=manifest))
                clone(c, "review_runs", UUID(cm["payload_ref"]), dict(id=rr, input_id=ri, operation_id=ro))
                if mode != "missing":
                    clone(c, "operations", prep["id"], dict(id=po, command_id=cmd, input_digest=sha(str(po).encode())))
                    c.execute("INSERT INTO effect_receipts(operation_id,effect_key,result) VALUES(%s,'extract-rules',%s)", (po, Jsonb(dict(prepared["result"], result_ref=str(uuid4()) if mode == "wrong-reference" else str(target_set)))))
                if mode == "ambiguous":
                    second = uuid4()
                    clone(c, "operations", prep["id"], dict(id=second, command_id=cmd, input_digest=sha(str(second).encode())))
                    c.execute("INSERT INTO effect_receipts(operation_id,effect_key,result) VALUES(%s,'extract-rules',%s)", (second, Jsonb(prepared["result"])))
            message = dict(cm, operation_id=str(ro), input_digest=sha(str(ro).encode()), payload_ref=str(rr))
            return tool.FixtureScope(fixture, fixture_sha, bid, cmd, connection), message, dict(payload, rules=expected)

        for mode, name in [
            ("missing", "core_without_same_command_prepare_effect_rejected"),
            ("wrong-reference", "actual_prepare_effect_ruleset_must_match_frozen_manifest"),
            ("human", "matching_actual_human_rule_cannot_enter_core_window"),
            ("ambiguous", "two_same_command_prepare_effects_cannot_choose_arbitrarily"),
        ]:
            alternate, message, data = alternate_chain(mode)
            check(name, denied(lambda: alternate.verify(message, "core-judgment", user(data)), "ACCEPTANCE_PAYLOAD_MISMATCH"))
            check("original_scope_rejects_real_other_command_"+mode, not scope.accepts_operation(message))
        scope.verify(cm, "core-judgment", user(payload))
        check("original_immutable_rules_and_effect_validate_again", True)
        # A new immutable parse of identical text is a different approved identity.
        new_parse = uuid4()
        with connection(True) as c:
            c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) SELECT %s,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps FROM parse_runs WHERE id=%s", (new_parse, UUID(body["parse_id"])))
            c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,printed_label,width,height,rotation,quality,gaps) SELECT bid_id,%s,physical_page,printed_label,width,height,rotation,quality,gaps FROM pages WHERE parse_id=%s", (new_parse, UUID(body["parse_id"])))
            c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,bbox,structure,digest) SELECT gen_random_uuid(),bid_id,%s,physical_page,ordinal,kind,text_content,bbox,structure,digest FROM evidence_blocks WHERE parse_id=%s", (new_parse, UUID(body["parse_id"])))
        check("new_actual_parse_same_text_rejected_by_identity", denied(lambda: scope.verify(cm, "core-judgment", user(payload)), "ACCEPTANCE_INPUT_CHANGED"))

        stdout = io.StringIO()
        args = ["review-acceptance", "--bid-id", str(bid), "--candidate", str(manifest), "--candidate-sha256", manifest_sha, "--fixture", str(fixture), "--fixture-sha256", fixture_sha]
        with patch("sys.argv", args), patch.object(tool, "execute", side_effect=AssertionError("execute forbidden")), redirect_stdout(stdout):
            tool.main()
        check("default_preview_does_not_start_executor", json.loads(stdout.getvalue())["mode"] == "preview_only_no_external_calls")
        verify()
        with connection(True) as c:
            after = c.execute("SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s", (bid,)).fetchone()["n"]
        check("no_model_ledger_or_external_calls_created", after == old_model_calls)
        result["passed"] = all(c["passed"] for c in result["checks"])
    except Exception as error:
        result["fatal"] = dict(type=type(error).__name__, message="Independent guard03 probe incomplete; no private configuration logged")
    result.update(runtime_candidate_sha256=manifest_sha, tool_sha256=digest(Path(tool.__file__)), probe_sha256=digest(Path(__file__)))
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(dict(passed=result["passed"], checks=len(result["checks"]), failed=[c["name"] for c in result["checks"] if not c["passed"]], fatal=result.get("fatal")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
