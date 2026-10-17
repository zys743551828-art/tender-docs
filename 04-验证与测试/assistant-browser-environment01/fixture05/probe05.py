"""Bounded HTTP-only cold-recovery and task05 delta probe. Zero real provider calls."""

import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from safety import MARKER

BASE = Path("/workspace/tender-docs/04-验证与测试/assistant-browser-environment01")
CONTROL = Path("/workspace/tender-workbench/.local/assistant_ui01-control.json")


def control(mode):
    temporary = CONTROL.with_suffix(".probe05-temporary")
    temporary.write_text(json.dumps({"marker": MARKER, "mode": mode, "scopes": ["global"]}))
    temporary.chmod(0o600)
    temporary.replace(CONTROL)


def main():
    output = BASE / "fixture05/http-delta-probe.json"
    if output.exists():
        raise ValueError("Probe is one-shot; retain existing evidence")
    members = json.loads(
        Path("/workspace/tender-workbench/.local/assistant_ui01-test-members.json").read_text()
    )
    if isinstance(members, dict):
        members = members["members"]
    seed = json.loads((BASE / "fixture01/synthetic-source.json").read_text())
    bid = seed["project_id"]
    checks = []
    with httpx.Client(base_url="http://127.0.0.1:8816", trust_env=False, timeout=15) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"login_name": members[0]["login"], "password": members[0]["password"]},
            headers={"Origin": "http://127.0.0.1:5184"},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]

        def headers(tag=None, key=None):
            result = {
                "Origin": "http://127.0.0.1:5184",
                "X-CSRF-Token": csrf,
                "Idempotency-Key": key or uuid4().hex,
            }
            if tag:
                result["If-Match"] = tag
            return result

        def request(method, path, body=None, tag=None, key=None):
            response = client.request(method, path, json=body, headers=headers(tag, key))
            assert response.headers.get("X-Assistant-Fixture") == MARKER
            return response

        # Retain every old terminal receipt without invoking or resolving it again.
        for folder in ("fixture01", "fixture04-final"):
            old = json.loads((BASE / folder / "http-smoke-result.json").read_text())
            for check in old["checks"]:
                result = request("GET", "/api/v1/assistant/jobs/" + check["job_id"])
                assert result.status_code == 200
                expected = (
                    "cancelled"
                    if "cancel" in check["case"]
                    else "failed"
                    if "unknown" in check["case"]
                    else "succeeded"
                )
                assert result.json()["operation"]["state"] == expected
                if check.get("action_id"):
                    card = request("GET", "/api/v1/assistant/actions/" + check["action_id"])
                    assert card.status_code == 200 and card.json()["state"] == "executed"
            checks.append(
                {
                    "case": "old_jobs_and_executed_action_read_only",
                    "source": folder,
                    "jobs": len(old["checks"]),
                    "state": "passed",
                }
            )

        # Exactly one new global job; no repetition of the prior five groups.
        control("answer")
        turn_body = {
            "client_message_id": str(uuid4()),
            "text": "显式合成05冷启动单条机制验证 " + uuid4().hex,
            "source_selection": [],
        }
        for _attempt in range(4):
            main = request("POST", "/api/v1/assistant/global-conversations/main", {})
            assert main.status_code == 200 and main.json()["state"] == "active"
            accepted = request(
                "POST",
                f"/api/v1/assistant/global-conversations/{main.json()['id']}/turns",
                turn_body,
                main.headers["etag"],
            )
            if accepted.status_code != 412:
                break
            time.sleep(0.15)
        assert accepted.status_code == 202, accepted.json()
        job = accepted.json()["id"]
        until = time.monotonic() + 25
        while time.monotonic() < until:
            result = request("GET", "/api/v1/assistant/jobs/" + job).json()
            if result["publication"]:
                break
            time.sleep(0.15)
        assert (
            result["operation"]["state"] == "succeeded"
            and result["publication"]["state"] == "published"
        )
        assert result["operation"]["bid_id"] is None
        control("hold")
        checks.append(
            {
                "case": "one_new_global_job_real_persisted_publication",
                "job_id": job,
                "state": "passed",
            }
        )

        drafts = f"/api/v1/bids/{bid}/task-definition-drafts"
        current_bid = request("GET", f"/api/v1/bids/{bid}")
        assert current_bid.status_code == 200
        created = request(
            "POST", drafts, {"request": "每天9点以后核对否决项"}, current_bid.headers["etag"]
        )
        assert created.status_code == 201, created.json()
        initial = created.json()
        assert initial["interpretation"]["trigger"]["mode"] == "manual"
        assert (
            initial["interpretation"]["unresolved"]
            and not initial["interpretation"]["ready_for_template_form"]
        )
        draft = drafts + "/" + initial["id"]
        blocked = request("POST", draft + "/save", {}, created.headers["etag"])
        assert (
            blocked.status_code == 409
            and blocked.json()["code"] == "TASK_DRAFT_NEEDS_CLARIFICATION"
        )
        checks.append(
            {
                "case": "ambiguous_daily_after_time_remains_pending_no_save",
                "draft_id": initial["id"],
                "state": "passed",
            }
        )

        runs_before = request("GET", f"/api/v1/bids/{bid}/task-runs").json()["items"]
        definitions_before = request(
            "GET", f"/api/v1/bids/{bid}/task-definitions?limit=100"
        ).json()["items"]
        edited = request(
            "PUT",
            draft,
            {
                "name": "显式合成05手动保存验证",
                "task_kind": "denial_check",
                "instructions": "仅核对否决项；缺失依据保留待补。",
                "trigger": {"mode": "manual"},
            },
            created.headers["etag"],
        )
        assert edited.status_code == 200, edited.json()
        old_tag = edited.headers["etag"]
        save_key = uuid4().hex
        saved = request("POST", draft + "/save", {}, old_tag, save_key)
        assert saved.status_code == 201, saved.json()
        current = request("GET", draft)
        assert current.status_code == 200
        assert current.json()["revision"] == edited.json()["revision"] + 1
        assert current.headers["etag"] != old_tag
        assert current.json()["updated_at"] >= edited.json()["updated_at"]
        assert current.json()["saved_template_id"] == saved.json()["id"]
        stale = request("POST", draft + "/save", {}, old_tag)
        assert stale.status_code == 412
        repeated = request("POST", draft + "/save", {}, current.headers["etag"])
        assert repeated.status_code == 409 and repeated.json()["code"] == "TASK_DRAFT_ALREADY_SAVED"
        replay = request("POST", draft + "/save", {}, old_tag, save_key)
        assert (
            replay.status_code == 201
            and replay.json() == saved.json()
            and replay.headers["Idempotent-Replayed"] == "true"
        )
        assert request("GET", f"/api/v1/bids/{bid}/task-runs").json()["items"] == runs_before
        definitions_after = request("GET", f"/api/v1/bids/{bid}/task-definitions?limit=100").json()[
            "items"
        ]
        assert len(definitions_after) == len(definitions_before) + 1
        assert (
            saved.json()["configuration"]["trigger"]["mode"] == "manual"
            and saved.json()["next_run_at"] is None
        )
        checks.append(
            {
                "case": "save_appends_revision_new_etag_412_409_and_original_ack201",
                "draft_id": initial["id"],
                "template_id": saved.json()["id"],
                "old_revision": edited.json()["revision"],
                "new_revision": current.json()["revision"],
                "template_count_increment": 1,
                "new_task_runs": 0,
                "state": "passed",
            }
        )
    output.write_text(
        json.dumps(
            {
                "checks": checks,
                "one_new_mock_job_only": True,
                "real_provider_attempts": 0,
                "kafka_delivery_accepted": False,
                "control_restored": "hold",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(
        {"passed": len(checks), "new_mock_jobs": 1, "real_provider_attempts": 0, "control": "hold"}
    )


if __name__ == "__main__":
    try:
        main()
    finally:
        control("hold")
