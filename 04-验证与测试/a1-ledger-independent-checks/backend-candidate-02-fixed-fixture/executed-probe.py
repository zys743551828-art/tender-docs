"""Independent ordinary ledger checks: actual restricted PG, explicit offline transport."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
from psycopg.types.json import Jsonb

from apps.worker import engine
from packages.domain import model_calls, storage
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError, connection, enqueue, sha

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"const": True}},
    "required": ["ok"],
    "additionalProperties": False,
}
ROOT = Path(__file__).resolve().parents[1]


def main():
    out = Path(os.environ["QA_LEDGER_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    result = dict(
        passed=False,
        checks=[],
        paid_generation_calls=0,
        scope="Actual restricted API/Worker LOGIN and PG transactions with explicit offline MockTransport and in-memory object store; not Kafka or paid-review acceptance",
    )
    candidate = Path(
        os.environ.get("QA_LEDGER_CANDIDATE", str(ROOT / "apps/api/a1-ledger-candidate-01.json"))
    )
    expected = os.environ.get(
        "QA_LEDGER_CANDIDATE_SHA256",
        "78441a80539fa76f128e6dcc56f42476edb7e5daffc3c334d328358f369a7c5f",
    )
    assert hashlib.sha256(candidate.read_bytes()).hexdigest() == expected
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
    frozen = out / "frozen-inputs"
    for item in json.loads(candidate.read_text())["sources"]:
        source = ROOT / item["path"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"], source
        target = frozen / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (frozen / "candidate.json").write_bytes(candidate.read_bytes())
    live_ops = []

    def check(name, value, observation=None):
        result["checks"].append(dict(name=name, passed=bool(value), observation=observation))

    def make(number=1):
        member, bid, command = uuid4(), uuid4(), uuid4()
        with connection() as c:
            c.execute(
                "INSERT INTO members(id,display_name) VALUES(%s,'独立普通模型账本合成夹具')",
                (member,),
            )
            c.execute(
                "INSERT INTO bids(id,title,settings,created_by) VALUES(%s,'独立账本验证','{}',%s)",
                (bid, member),
            )
            c.execute(
                "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'qa-independent-ledger',%s,%s,%s)",
                (command, member, bid, uuid4().hex, sha(str(command))),
            )
            operations = [
                enqueue(c, bid, command, "qa-ledger-stage", {"id": str(uuid4()), "ordinal": i})
                for i in range(number)
            ]
        runs = []
        for operation in operations:
            live_ops.append(operation["id"])
            with connection(True) as c:
                message = c.execute(
                    "SELECT payload FROM outbox WHERE operation_id=%s", (operation["id"],)
                ).fetchone()["payload"]
            lease = engine.acquire(message)
            assert lease
            runs.append((message, lease))
        return runs

    def reserve(run, key="one", tokens=32, request="request", profile="profile"):
        return model_calls.reserve(
            *run,
            logical_key=key,
            request_digest=sha(request),
            profile_digest=sha(profile),
            max_tokens=tokens,
        )

    def count(runs):
        with connection(True) as c:
            return c.execute(
                "SELECT count(*) AS n,coalesce(sum((usage->'reservation'->>'max_tokens')::integer),0) AS tokens FROM model_calls WHERE operation_id=ANY(%s::uuid[])",
                ([r[0]["operation_id"] for r in runs],),
            ).fetchone()

    def rejects(action, code=None):
        try:
            action()
        except DomainError as error:
            return code is None or error.code == code
        return False

    def generate(run, key="logical"):
        return model_calls.generate(
            *run,
            logical_key=key,
            messages=[{"role": "user", "content": "Synthetic offline JSON"}],
            json_schema=SCHEMA,
            max_tokens=32,
        )

    def row(call):
        with connection(True) as c:
            return c.execute("SELECT * FROM model_calls WHERE id=%s", (call,)).fetchone()

    def response():
        return httpx.Response(
            200,
            json={
                "id": "qa-offline-response-" + uuid4().hex,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            },
        )

    @contextmanager
    def transport(handler, objects=None):
        objects = {} if objects is None else objects
        client = DeepSeekClient(
            api_key="synthetic-local-only", transport=httpx.MockTransport(handler)
        )
        with (
            patch.object(DeepSeekClient, "from_env", return_value=client),
            patch.object(storage, "put", side_effect=lambda k, b, m: objects.__setitem__(k, b)),
            patch.object(storage, "get", side_effect=lambda k: objects[k]),
        ):
            yield objects

    try:
        with connection() as c:
            api = c.execute(
                "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
        with connection(True) as c:
            worker = c.execute(
                "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
        check(
            "distinct_restricted_login",
            api["current_user"] != worker["current_user"]
            and not api["rolsuper"]
            and not worker["rolsuper"]
            and not api["rolbypassrls"]
            and not worker["rolbypassrls"],
            dict(api=api, worker=worker),
        )
        run = make()[0]
        barrier = threading.Barrier(8)

        def same(_):
            barrier.wait()
            return reserve(run)

        with ThreadPoolExecutor(max_workers=8) as pool:
            same_results = list(pool.map(same, range(8)))
        check(
            "eight_concurrent_identical_reservations_create_one",
            sum(new for _, new in same_results) == 1
            and len({r["id"] for r, _ in same_results}) == 1
            and count([run])["n"] == 1,
        )
        check(
            "request_and_profile_conflict_have_no_second_reservation",
            rejects(lambda: reserve(run, request="changed"), "MODEL_CALL_IDENTITY_CONFLICT")
            and rejects(lambda: reserve(run, profile="changed"), "MODEL_CALL_IDENTITY_CONFLICT")
            and count([run])["n"] == 1,
        )
        runs = make(2)
        for i in range(9):
            reserve(runs[i % 2], str(i), 1)
        barrier = threading.Barrier(4)

        def near_limit(i):
            barrier.wait()
            try:
                reserve(runs[i % 2], "concurrent-" + str(i), 1)
                return "reserved"
            except DomainError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(near_limit, range(4)))
        check(
            "prepare_review_shared_count_concurrent_boundary",
            outcomes.count("reserved") == 1
            and outcomes.count("MODEL_BUDGET_EXHAUSTED") == 3
            and count(runs) == dict(n=10, tokens=10),
            outcomes,
        )
        runs = make(2)
        for i in range(4):
            reserve(runs[i % 2], str(i), 8192)
        check(
            "shared_output_budget_exact_32768_then_reject",
            rejects(lambda: reserve(runs[1], "overflow", 1), "MODEL_BUDGET_EXHAUSTED")
            and count(runs) == dict(n=4, tokens=32768),
        )
        run = make()[0]

        @contextmanager
        def rollback_after_insert(*args, **kwargs):
            with connection(*args, **kwargs) as c:
                yield c
                raise RuntimeError("QA controlled transaction rollback")

        try:
            with patch.object(model_calls, "connection", rollback_after_insert):
                reserve(run)
        except RuntimeError:
            pass
        check(
            "failed_transaction_persists_neither_call_nor_budget",
            count([run]) == dict(n=0, tokens=0)
            and reserve(run)[1]
            and count([run]) == dict(n=1, tokens=32),
        )
        run = make()[0]
        invalid = [
            (run[0] | {"generation": 2}, run[1]),
            (run[0], run[1] | {"owner": uuid4()}),
            (run[0], run[1] | {"fence": run[1]["fence"] + 1}),
        ]
        denied = all(rejects(lambda r=r: reserve(r)) for r in invalid)
        with connection(True) as c:
            c.execute(
                "UPDATE step_receipts SET lease_until=now()-interval '1 second' WHERE operation_id=%s",
                (run[0]["operation_id"],),
            )
        denied &= rejects(lambda: reserve(run))
        check("generation_fence_owner_expired_lease_no_call", denied and count([run])["n"] == 0)
        run = make()[0]
        seen = []

        def timeout(request):
            seen.append(1)
            raise httpx.ReadTimeout("synthetic unknown", request=request)

        with transport(timeout):
            failed = rejects(lambda: generate(run))
            again = rejects(lambda: generate(run), "MODEL_CALL_REQUIRES_RECONCILIATION")
        with connection(True) as c:
            unknown = c.execute(
                "SELECT state,usage FROM model_calls WHERE operation_id=%s",
                (run[0]["operation_id"],),
            ).fetchone()
        check(
            "unknown_billing_persisted_no_resend",
            failed
            and again
            and len(seen) == 1
            and unknown["state"] == "unknown"
            and unknown["usage"]["billing_state"] == "unknown",
        )
        run = make()[0]
        observed = []

        def committed_before_send(request):
            with connection(True) as c:
                call = c.execute(
                    "SELECT id FROM model_calls WHERE operation_id=%s AND state='reserved'",
                    (run[0]["operation_id"],),
                ).fetchone()
                c.execute("SET LOCAL lock_timeout='1s'")
                locked = c.execute(
                    "SELECT id FROM operations WHERE id=%s FOR UPDATE NOWAIT",
                    (run[0]["operation_id"],),
                ).fetchone()
                observed.append(bool(call and locked))
            return response()

        with transport(committed_before_send) as objects:
            value, call = generate(run)
            again, same_call = generate(run)
            check(
                "reservation_committed_before_single_unlocked_send_then_sha_cache",
                observed == [True]
                and value == again
                and call == same_call
                and row(call)["state"] == "succeeded"
                and row(call)["usage"]["actual"]["total_tokens"] == 7,
            )
            objects[next(iter(objects))] = b"QA corrupted cache"
            check(
                "corrupt_success_cache_rejects_without_paid_repeat",
                rejects(lambda: generate(run), "MODEL_RESULT_CORRUPTED") and observed == [True],
            )
        run = make()[0]
        sent = []
        original_reserve = model_calls.reserve

        def cancel_after_reserve(*args, **kwargs):
            reserved = original_reserve(*args, **kwargs)
            with connection(True) as c:
                c.execute(
                    "UPDATE operations SET state='cancel_requested',cancel_requested_at=now() WHERE id=%s",
                    (run[0]["operation_id"],),
                )
            return reserved

        with (
            transport(lambda request: sent.append(1) or response()),
            patch.object(model_calls, "reserve", side_effect=cancel_after_reserve),
        ):
            cancelled = rejects(lambda: generate(run))
        with connection(True) as c:
            cancelrow = c.execute(
                "SELECT state,usage FROM model_calls WHERE operation_id=%s",
                (run[0]["operation_id"],),
            ).fetchone()
        check(
            "cancel_between_commit_and_send_never_sends",
            cancelled
            and not sent
            and cancelrow["state"] == "failed"
            and cancelrow["usage"]["billing_state"] == "not_sent",
        )
        run = make()[0]
        sent = []

        def late_response(request):
            sent.append(1)
            with connection(True) as c:
                c.execute(
                    "UPDATE operations SET state='cancel_requested',cancel_requested_at=now() WHERE id=%s",
                    (run[0]["operation_id"],),
                )
            return response()

        with transport(late_response):
            value, call = generate(run)
        late_published = engine.finish(*run, {"synthetic_late_result": True})
        with connection(True) as c:
            effects = c.execute(
                "SELECT count(*) AS n FROM effect_receipts WHERE operation_id=%s",
                (run[0]["operation_id"],),
            ).fetchone()["n"]
        check(
            "late_cancelled_response_records_usage_but_no_effect",
            sent == [1]
            and row(call)["usage"]["actual"]["total_tokens"] == 7
            and not late_published
            and effects == 0,
        )
        run = make()[0]
        seen = []
        with (
            transport(lambda request: seen.append(1) or response()),
            patch.object(storage, "put", side_effect=RuntimeError("synthetic persistence fault")),
        ):
            failed = rejects(lambda: generate(run), "MODEL_RESULT_PERSIST_FAILED")
            repeat = rejects(lambda: generate(run), "MODEL_CALL_REQUIRES_RECONCILIATION")
        with connection(True) as c:
            persist = c.execute(
                "SELECT state,usage FROM model_calls WHERE operation_id=%s",
                (run[0]["operation_id"],),
            ).fetchone()
        check(
            "paid_response_persistence_failure_retains_usage_no_resend",
            failed
            and repeat
            and seen == [1]
            and persist["state"] == "failed"
            and persist["usage"]["billing_state"] == "usage_reported"
            and persist["usage"]["actual"]["total_tokens"] == 7,
        )
        if os.environ.get("QA_LEDGER_CROSS_OPERATION") == "1":
            runs = make(3)
            pending, _ = reserve(runs[0], "unresolved-identity")
            with connection(True) as c:
                c.execute(
                    "UPDATE model_calls SET state='unknown',usage=usage||%s WHERE id=%s",
                    (Jsonb({"billing_state": "unknown"}), pending["id"]),
                )
            with connection() as c:
                old = c.execute(
                    "SELECT * FROM commands WHERE id=(SELECT command_id FROM operations WHERE id=%s)",
                    (runs[1][0]["operation_id"],),
                ).fetchone()
                command = uuid4()
                c.execute(
                    "INSERT INTO commands(id,actor_id,command_kind,target_id,idempotency_key,request_digest) VALUES(%s,%s,'qa-cross-command',%s,%s,%s)",
                    (command, old["actor_id"], old["target_id"], uuid4().hex, sha(str(command))),
                )
                c.execute(
                    "UPDATE operations SET command_id=%s WHERE id=%s",
                    (command, runs[1][0]["operation_id"]),
                )
            check(
                "unknown_same_request_blocks_new_operation_and_new_command",
                all(
                    rejects(
                        lambda r=r: reserve(r, "unresolved-identity"),
                        "MODEL_CALL_REQUIRES_RECONCILIATION",
                    )
                    for r in runs[1:]
                )
                and count(runs)["n"] == 1,
            )
            changed_request = reserve(runs[1], "unresolved-identity", request="new-request")[1]
            changed_profile = reserve(runs[2], "unresolved-identity", profile="new-profile")[1]
            check(
                "changed_semantic_request_or_profile_is_distinct_identity",
                changed_request and changed_profile and count(runs)["n"] == 3,
            )
            runs = make(2)
            barrier = threading.Barrier(2)

            def competing_operation(index):
                barrier.wait()
                try:
                    reserve(runs[index], "simultaneous-unresolved")
                    return "created"
                except DomainError as error:
                    return error.code

            with ThreadPoolExecutor(max_workers=2) as pool:
                race = list(pool.map(competing_operation, range(2)))
            check(
                "parallel_operations_only_one_same_request_reservation",
                sorted(race) == ["MODEL_CALL_REQUIRES_RECONCILIATION", "created"]
                and count(runs)["n"] == 1,
                race,
            )
        permission = []
        for sql in [
            "SELECT * FROM outcomes LIMIT 0",
            "UPDATE decisions SET reason=reason WHERE false",
            "UPDATE model_routes SET revision=revision WHERE false",
        ]:
            try:
                with connection(True) as c:
                    c.execute(sql)
            except psycopg.errors.InsufficientPrivilege:
                permission.append(True)
            else:
                permission.append(False)
        check(
            "worker_cannot_read_official_labels_or_write_human_decisions_routes",
            all(permission),
            permission,
        )
        result["passed"] = all(c["passed"] for c in result["checks"])
    except Exception as error:
        result["fatal"] = dict(
            type=type(error).__name__,
            message="Independent ledger probe incomplete; no private configuration or requests printed",
        )
    finally:
        if live_ops:
            with connection(True) as c:
                c.execute(
                    "UPDATE operations SET state='cancelled',cancel_requested_at=coalesce(cancel_requested_at,now()),finished_at=now() WHERE id=ANY(%s::uuid[]) AND state IN ('queued','running','cancel_requested')",
                    (live_ops,),
                )
                c.execute(
                    "UPDATE step_receipts SET state='cancelled',fence=fence+1,lease_until=NULL,owner_token=NULL WHERE operation_id=ANY(%s::uuid[]) AND state='running'",
                    (live_ops,),
                )
    result.update(
        candidate_sha256=expected,
        probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, default=str, indent=2) + "\n"
    )
    print(
        dict(
            passed=result["passed"],
            checks=len(result["checks"]),
            failed=[c["name"] for c in result["checks"] if not c["passed"]],
            fatal=result.get("fatal"),
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
