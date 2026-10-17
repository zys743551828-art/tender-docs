"""Real HTTP smoke against the dedicated fixture; never imports production or DSNs."""

import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from safety import MARKER

BASE = "http://127.0.0.1:8816"
ORIGIN = "http://127.0.0.1:5184"
EVIDENCE = Path("/workspace/tender-docs/04-验证与测试/assistant-browser-environment01/fixture01")
CONTROL = Path("/workspace/tender-workbench/.local/assistant_ui01-control.json")


def control(mode, scopes=None):
    temporary = CONTROL.with_suffix(".smoke-temporary")
    temporary.write_text(
        json.dumps({"marker": MARKER, "mode": mode, "scopes": scopes or ["global"]})
    )
    temporary.chmod(0o600)
    temporary.replace(CONTROL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EVIDENCE / "http-smoke-result.json")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Retain previous smoke result; choose a new output path")
    members = json.loads(
        Path("/workspace/tender-workbench/.local/assistant_ui01-test-members.json").read_text()
    )
    if isinstance(members, dict):
        members = members["members"]
    seed = json.loads((EVIDENCE / "synthetic-source.json").read_text())
    bid = seed["project_id"]
    checks = []
    with httpx.Client(base_url=BASE, trust_env=False, timeout=15) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_name": members[0]["login"], "password": members[0]["password"]},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200, {"stage": "login", "status": login.status_code}
        token = login.json()["csrf_token"]

        def request(method, path, *, body=None, tag=None):
            headers = {"Origin": ORIGIN, "X-CSRF-Token": token, "Idempotency-Key": uuid4().hex}
            if tag:
                headers["If-Match"] = tag
            result = client.request(method, path, json=body, headers=headers)
            assert result.headers.get("X-Assistant-Fixture") == MARKER
            return result

        def conversation(scope):
            prefix = (
                "/api/v1/assistant/global-conversations"
                if scope == "global"
                else f"/api/v1/bids/{bid}/conversations"
            )
            result = request("POST", prefix + "/main", body={})
            assert result.status_code == 200, result.json()
            return prefix, result

        def turn(scope, text, refs=None):
            body = {"client_message_id": str(uuid4()), "text": text, "source_selection": refs or []}
            for _attempt in range(4):
                prefix, main = conversation(scope)
                response = request(
                    "POST",
                    f"{prefix}/{main.json()['id']}/turns",
                    body=body,
                    tag=main.headers["etag"],
                )
                if response.status_code != 412:
                    break
                # This request was refused before acceptance; preserve logical identity.
                time.sleep(0.15)
            assert response.status_code == 202, response.json()
            return prefix, main.json()["id"], response.json()["id"]

        def complete(job_id, state="succeeded"):
            until = time.monotonic() + 25
            while time.monotonic() < until:
                response = request("GET", f"/api/v1/assistant/jobs/{job_id}")
                assert response.status_code == 200, response.json()
                value = response.json()
                if value["operation"]["state"] == state and (
                    state != "succeeded" or value["publication"]
                ):
                    return value
                time.sleep(0.15)
            raise AssertionError({"job_id": job_id, "expected_state": state, "latest": value})

        control("answer")
        prefix, main_id, job = turn("global", "显式合成 global 机制回答 " + uuid4().hex)
        result = complete(job)
        assert (
            result["publication"]["state"] == "published" and result["operation"]["bid_id"] is None
        )
        history = request("GET", f"{prefix}/{main_id}/messages").json()
        assert any(item["role"] == "assistant" for item in history["items"])
        checks.append({"case": "global_answer_persisted", "job_id": job, "state": "passed"})

        refs = []
        for kind in ("project", "document"):
            response = request(
                "GET",
                f"/api/v1/assistant/sources?kind={kind}&bid_id={bid}"
                + ("&physical_page=1" if kind == "document" else ""),
            )
            assert response.status_code == 200, response.json()
            choices = [
                item
                for item in response.json()["items"]
                if item["available"] and item["bid_id"] == bid
            ]
            assert choices, response.json()
            refs.append(choices[0]["reference"])
        control("knowledge_action")
        prefix, main_id, job = turn("global", "显式合成知识候选 " + uuid4().hex, refs)
        result = complete(job)
        history = request("GET", f"{prefix}/{main_id}/messages").json()
        parts = [
            part
            for item in history["items"]
            for part in item["parts"]
            if part.get("type") == "assistant_reference_answer" and part.get("job_id") == job
        ]
        assert len(parts) == 1 and len(parts[0]["action_ids"]) == 1
        action_id = parts[0]["action_ids"][0]
        card = request("GET", "/api/v1/assistant/actions/" + action_id)
        resolved = request(
            "POST",
            f"/api/v1/assistant/actions/{action_id}/resolve",
            body={"decision": "confirm"},
            tag=card.headers["etag"],
        )
        assert resolved.status_code == 200 and resolved.json()["state"] == "executed", (
            resolved.json()
        )
        repeated = request(
            "POST",
            f"/api/v1/assistant/actions/{action_id}/resolve",
            body={"decision": "reject"},
            tag=card.headers["etag"],
        )
        assert repeated.status_code == 200 and repeated.json() == resolved.json()
        checks.append(
            {
                "case": "global_sourced_action_exactly_once",
                "job_id": job,
                "action_id": action_id,
                "state": "passed",
            }
        )

        control("unknown")
        question = "显式合成 unknown " + uuid4().hex
        _, _, job = turn("global", question)
        result = complete(job, "failed")
        assert (
            result["model_call"]["state"] == "unknown" and result["requires_billing_reconciliation"]
        )
        prefix, current = conversation("global")
        blocked = request(
            "POST",
            f"{prefix}/{current.json()['id']}/turns",
            body={"client_message_id": str(uuid4()), "text": question, "source_selection": []},
            tag=current.headers["etag"],
        )
        assert (
            blocked.status_code == 409
            and blocked.json()["code"] == "MODEL_CALL_REQUIRES_RECONCILIATION"
        )
        checks.append(
            {"case": "global_unknown_no_second_attempt", "job_id": job, "state": "passed"}
        )

        control("hold")
        _, _, job = turn("global", "显式合成取消 " + uuid4().hex)
        cancelled = request("POST", f"/api/v1/assistant/jobs/{job}/cancel", body={})
        assert cancelled.status_code == 202 and not cancelled.json()["cancellation_terminal"]
        control("answer")
        result = complete(job, "cancelled")
        assert result["model_call"] is None and result["publication"] is None
        checks.append({"case": "global_cancel_before_mock", "job_id": job, "state": "passed"})

        control("answer", ["project"])
        prefix, main_id, job = turn(
            "project", "显式合成 project 机制回答 " + uuid4().hex, [refs[1]]
        )
        result = complete(job)
        assert (
            result["publication"]["state"] == "published" and result["operation"]["bid_id"] == bid
        )
        history = request("GET", f"{prefix}/{main_id}/messages").json()
        assert any(item["role"] == "assistant" for item in history["items"])
        checks.append(
            {"case": "project_actual_engine_answer_persisted", "job_id": job, "state": "passed"}
        )
    control("hold")
    args.output.write_text(
        json.dumps(
            {
                "checks": checks,
                "real_provider_attempts": 0,
                "transport": "explicit-direct-handler-not-kafka",
                "storage": "explicit-local-synthetic",
                "browser_accepted": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print({"passed": len(checks), "real_provider_attempts": 0, "default_restored": "hold"})


if __name__ == "__main__":
    try:
        main()
    finally:
        control("hold")
