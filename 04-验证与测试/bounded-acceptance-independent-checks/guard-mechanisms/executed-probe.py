"""Independent bounded-window checks; no real consumer, provider or execute entry."""

import hashlib
import io
import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from deploy import review_acceptance as tool
from packages.domain.runtime import DomainError, connection


def main():
    out = Path(os.environ["QA_GUARD_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    source = Path(tool.__file__)
    expected = "b69a359f2099f844e3af164d3221eed607d5af4d99167fc8870eef5f83a53d85"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
    result = dict(
        passed=False, checks=[], paid_model_calls=0, real_kafka_consumers=0, tool_sha256=expected
    )

    def check(name, value, observation=None):
        result["checks"].append(dict(name=name, passed=bool(value), observation=observation))

    def invoke(boundary, bid, key="extract-rules", tokens=8192, text="bounded synthetic"):
        return boundary(
            {"bid_id": str(bid)},
            {},
            logical_key=key,
            messages=[{"role": "user", "content": text}],
            json_schema={"type": "object"},
            max_tokens=tokens,
        )

    def denied(action, code=None):
        try:
            action()
        except DomainError as error:
            return code is None or error.code == code
        return False

    try:
        bid = uuid4()
        seen = []
        boundary = tool.AcceptanceBoundary(
            bid, lambda *a, **kw: seen.append(kw["logical_key"]), lambda: None
        )
        barrier = threading.Barrier(20)

        def simultaneous(index):
            barrier.wait()
            return not denied(
                lambda: invoke(boundary, bid, "extract-rules" if index % 2 else "core-judgment")
            )

        with ThreadPoolExecutor(max_workers=20) as pool:
            accepted = list(pool.map(simultaneous, range(20)))
        check(
            "concurrent_two_logical_keys_each_at_most_once",
            sum(accepted) == 2
            and sorted(seen) == ["core-judgment", "extract-rules"]
            and boundary.started == 2,
        )
        seen = []
        boundary = tool.AcceptanceBoundary(bid, lambda *a, **kw: seen.append(1), lambda: None)
        cases = [
            lambda: invoke(boundary, uuid4()),
            lambda: invoke(boundary, "malformed-uuid"),
            lambda: invoke(boundary, bid, key="other"),
            lambda: invoke(boundary, bid, tokens=True),
            lambda: invoke(boundary, bid, tokens=8193),
            lambda: invoke(boundary, bid, tokens=0),
            lambda: invoke(boundary, bid, text="中" * 8192),
        ]
        check(
            "foreign_malformed_purpose_size_token_limits_before_delegation",
            all(denied(case, "ACCEPTANCE_SCOPE_REJECTED") for case in cases)
            and boundary.started == 0
            and not seen,
        )
        boundary.deadline = time.monotonic() - 1
        check(
            "deadline_rejects_before_delegation",
            denied(lambda: invoke(boundary, bid), "ACCEPTANCE_SCOPE_REJECTED")
            and not seen
            and boundary.started == 0,
        )
        failures = []

        def unknown(*args, **kwargs):
            failures.append(1)
            raise DomainError(
                "MODEL_TIMEOUT", "Controlled unknown", 503, {"billing_state": "unknown"}
            )

        boundary = tool.AcceptanceBoundary(bid, unknown, lambda: None)
        first = denied(lambda: invoke(boundary, bid), "MODEL_TIMEOUT")
        repeat = denied(lambda: invoke(boundary, bid), "ACCEPTANCE_LOGICAL_CALL_REPEATED")
        check(
            "unknown_attempt_consumes_logical_slot_and_cannot_retry",
            first and repeat and failures == [1] and boundary.started == 1,
        )
        with tempfile.TemporaryDirectory(prefix="acceptance-source-", dir="/tmp") as directory:
            temp = Path(directory)
            source_file = temp / "bound.py"
            source_file.write_text("stable\n")
            candidate = temp / "candidate.json"
            candidate.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "path": str(source_file),
                                "sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
                            }
                        ]
                    }
                )
            )
            with patch.object(tool, "ROOT", temp):
                verify = tool.frozen_sources(candidate)
                source_file.write_text("changed\n")
                boundary = tool.AcceptanceBoundary(bid, lambda *a, **kw: seen.append(1), verify)
                check(
                    "actual_frozen_source_mutation_blocks_before_upstream",
                    denied(lambda: invoke(boundary, bid), "ACCEPTANCE_SOURCE_CHANGED")
                    and boundary.started == 0
                    and not seen,
                )
        with connection(True) as c:
            role = c.execute(
                "SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
        with tool.exclusive_window(bid):
            with ThreadPoolExecutor(max_workers=1) as pool:

                def competitor():
                    try:
                        with tool.exclusive_window(bid):
                            return False
                    except DomainError as error:
                        return error.code == "ACCEPTANCE_WINDOW_BUSY"

                excluded = pool.submit(competitor).result(10)
            with connection(True) as c:
                sessions = c.execute(
                    "SELECT a.state,a.xact_start FROM pg_stat_activity a WHERE a.pid IN (SELECT pid FROM pg_locks WHERE locktype='advisory' AND granted) AND a.usename=current_user"
                ).fetchall()
            check(
                "real_pg_second_executor_rejected_without_open_business_transaction",
                excluded
                and any(row["state"] == "idle" and row["xact_start"] is None for row in sessions)
                and not role["rolsuper"]
                and not role["rolbypassrls"],
                dict(role=role, lock_sessions=sessions),
            )
        with tool.exclusive_window(bid):
            reentered = True
        check("real_pg_lock_released_on_exit", reentered)
        try:
            with tool.exclusive_window(bid):
                raise RuntimeError("Controlled executor crash")
        except RuntimeError:
            pass
        with tool.exclusive_window(bid):
            released = True
        check("real_pg_lock_released_on_exception", released)
        stdout = io.StringIO()
        with (
            patch(
                "sys.argv", ["acceptance", "--bid-id", str(bid), "--candidate", "nonexistent.json"]
            ),
            patch.object(tool, "execute", side_effect=AssertionError("must not execute")),
            redirect_stdout(stdout),
        ):
            tool.main()
        preview = json.loads(stdout.getvalue())
        check(
            "default_preview_never_enters_execute",
            preview["mode"] == "preview_only_no_external_calls"
            and preview["max_invocations"] == 2
            and preview["max_input_bytes_each"] == 24576
            and preview["max_output_tokens_each"] == 8192
            and preview["seconds"] == 180,
        )
        with (
            patch(
                "sys.argv",
                [
                    "acceptance",
                    "--bid-id",
                    str(bid),
                    "--candidate",
                    "nonexistent.json",
                    "--seconds",
                    "181",
                    "--execute",
                ],
            ),
            patch.object(tool, "execute", side_effect=AssertionError("must not execute")),
            redirect_stderr(io.StringIO()),
        ):
            try:
                tool.main()
            except SystemExit as error:
                rejected = error.code == 2
            else:
                rejected = False
        check("cli_cannot_open_more_than_180_seconds", rejected)
        result["passed"] = all(c["passed"] for c in result["checks"])
    except Exception as error:
        result["fatal"] = dict(
            type=type(error).__name__,
            message="Independent guard probe incomplete; no private values logged",
        )
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "frozen-review_acceptance.py").write_bytes(source.read_bytes())
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
