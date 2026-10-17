"""Fixed isolated child for score checks. Never used as a general paid runner."""

if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    from uuid import UUID, uuid4

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import httpx

    from packages.domain.providers.deepseek import DeepSeekClient
    from packages.domain.runtime import DomainError
    from packages.model_governance.scoring_runtime import execute_job, score_connection

    requests = []
    observations = []
    mode = sys.argv[1]

    def handler(request):
        requests.append(request)
        # The score role can observe its OWN sessions, so null fields from a different
        # user's pg_stat_activity cannot masquerade as a no-idle-transaction success.
        with score_connection() as c:
            assert not c.execute(
                "SELECT pid FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'"
            ).fetchall()
        observations.append("http_without_open_score_transaction")
        if mode == "timeout":
            raise httpx.ReadTimeout("explicit synthetic unknown billing")
        if mode == "hold" and len(requests) == 1:
            sys.stdout.write(
                json.dumps({"event": "mock_request_started", "no_idle_score_transaction": True})
                + "\n"
            )
            sys.stdout.flush()
            if sys.stdin.buffer.read(1) != b"1":
                raise RuntimeError("test coordination cancelled")
        body = json.loads(json.loads(request.content)["messages"][-1]["content"])
        block = body["sections"][0]["blocks"][0]
        output = {
            "band_id": body["rule"]["bands"][0]["id"],
            "reason": "显式合成完整判断",
            "status": "valid",
            "coverage": "complete",
            "evidence": [{"evidence_id": block["evidence_id"], "quote": block["text"]}],
        }
        return httpx.Response(
            200,
            json={
                "id": "synthetic-" + uuid4().hex,
                "model": "deepseek-flash",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output, ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    try:
        if mode == "repeatable_read":
            import psycopg

            try:
                with score_connection() as c:
                    c.commit()
                    c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    c.execute(
                        "SELECT score_execution.claim_job(%s,%s,%s)",
                        (UUID(sys.argv[2]), int(sys.argv[3]), uuid4()),
                    )
            except psycopg.Error as error:
                assert error.sqlstate == "55000"
                output = {"ok": True, "checks": ["repeatable_read_refused"], "mock_calls": 0}
        elif mode == "permissions":
            checks = []
            import psycopg

            for query in (
                "SELECT * FROM design.outcomes",
                "SELECT * FROM model_governance.exports",
                "SELECT * FROM historical_backtest.proofs",
                "SELECT * FROM score_execution.calls",
                "SELECT review_root_content FROM score_execution.inputs",
                "SELECT * FROM score_execution.current_chapters",
            ):
                try:
                    with score_connection() as c:
                        c.execute(query)
                except psycopg.Error as error:
                    assert error.sqlstate == "42501"
                    checks.append("denied_42501")
            output = {"ok": True, "checks": checks, "mock_calls": 0}
        else:
            client = DeepSeekClient(
                api_key="explicit-synthetic-only", transport=httpx.MockTransport(handler)
            )
            result = execute_job(UUID(sys.argv[2]), int(sys.argv[3]), transport=client)
            output = {
                "ok": True,
                "items": len(result["items"]),
                "formal_eligibility": result["formal_eligibility"],
                "prediction_value": result["prediction"]["value"],
                "mock_calls": len(requests),
                "observations": observations,
            }
    except DomainError as error:
        output = {
            "ok": False,
            "code": error.code,
            "mock_calls": len(requests),
            "observations": observations,
        }
    except Exception as error:
        output = {
            "ok": False,
            "code": type(error).__name__,
            "sqlstate": getattr(error, "sqlstate", None),
            "diagnostic": error.diag.message_primary
            if getattr(error, "sqlstate", None) == "42702"
            else None,
            "mock_calls": len(requests),
        }
    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
