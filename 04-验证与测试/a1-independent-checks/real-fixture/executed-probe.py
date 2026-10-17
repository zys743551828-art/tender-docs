"""One synthetic real OSS/Kafka/parser fixture; never schedules or calls review."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from packages.domain.runtime import connection
from qa.helpers import Client, ensure, members, pdf_bytes
from qa.provider_proxy_probe import bytes_call


def main():
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "external-release.env"
    out = Path(os.environ["QA_A1_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    result = dict(
        passed=False,
        paid_generation_calls=0,
        review_consumers_started=0,
        review_ticks=0,
        scope="Only real synthetic original upload/verification/material commit/parser03; no paid-review execution",
    )
    stage = "login"
    try:
        client = Client(members()[0])
        bid = client.bid("独立QA受控工期90与120-" + uuid4().hex[:8])
        result["bid_id"] = bid
        status, headers, _ = client.call("GET", f"/bids/{bid}/settings")
        ensure(status == 200, "read settings")
        status, _, _ = client.call(
            "PATCH",
            f"/bids/{bid}/settings",
            {"auto_review": False},
            headers={"If-Match": headers["ETag"]},
        )
        ensure(status == 200, "disable automatic review for fixture only")
        result["auto_review_disabled_for_this_synthetic_bid"] = True
        batch = client.batch(bid)
        versions = []
        for role, text in [
            (
                "tender",
                "Mandatory construction period is 90 days. A period over 90 days disqualifies the bid.",
            ),
            ("body", "We commit to completing all construction in 120 days."),
        ]:
            stage = "real_upload_" + role
            data = pdf_bytes(text)
            digest = hashlib.sha256(data).hexdigest()
            (out / (role + ".pdf")).write_bytes(data)
            status, headers, grant = client.call(
                "POST",
                f"/bids/{bid}/uploads",
                dict(
                    batch_id=batch,
                    role=role,
                    filename="合成工期-" + role + ".pdf",
                    byte_size=len(data),
                    declared_sha256=digest,
                    target={"kind": "new_file"},
                ),
            )
            ensure(status == 200, "real proxy grant")
            status, _, _ = bytes_call(client, grant["upload_url"], data)
            ensure(status == 200, "real same-origin OSS PUT")
            status, _, accepted = client.call(
                "POST",
                f"/uploads/{grant['upload_id']}/complete",
                dict(parts=[], declared_sha256=digest),
                headers={"If-Match": headers["ETag"]},
            )
            ensure(status == 202, "real verification accepted")
            _, verified = client.poll(
                f"/uploads/{grant['upload_id']}",
                lambda u: u["state"] in ("verified", "quarantined"),
                "actual document worker verification",
                seconds=45,
            )
            ensure(verified["state"] == "verified", "real original SHA accepted")
            versions.append(verified)
            result.setdefault("inputs", []).append(
                dict(
                    role=role,
                    filename="合成工期-" + role + ".pdf",
                    sha256=digest,
                    byte_size=len(data),
                    upload_id=grant["upload_id"],
                    source_text=text,
                )
            )
        stage = "commit_and_parse"
        status, headers, _ = client.call("GET", f"/bids/{bid}")
        ensure(status == 200, "read material head")
        status, _, accepted = client.call(
            "POST",
            f"/bids/{bid}/upload-batches/{batch}/commit",
            dict(
                accepted_upload_ids=[u["id"] for u in versions],
                accepted_binding_ids=[u["binding_id"] for u in versions],
                explicit_partial_acceptance=False,
            ),
            headers={"If-Match": headers["ETag"]},
        )
        ensure(status == 202, "commit accepted")
        operation = accepted["operation"]["id"]
        _, operation_view = client.poll(
            f"/operations/{operation}",
            lambda o: o["state"] in ("succeeded", "failed", "cancelled"),
            "actual Kafka document parse",
            seconds=60,
        )
        ensure(operation_view["state"] == "succeeded", "actual parser operation success")
        result["parse_operation_id"] = operation
        for info, upload in zip(result["inputs"], versions, strict=True):
            status, _, current = client.call("GET", f"/uploads/{upload['id']}")
            ensure(status == 200, "read committed upload")
            version = current["document_version_id"]
            status, _, evidence = client.call("GET", f"/documents/{version}/evidence")
            ensure(status == 200, "read real page evidence")
            info.update(
                document_version_id=version,
                parse_run_id=evidence["parse_run_id"],
                coverage=evidence["coverage"],
                gaps=evidence["gaps"],
                evidence_ids=[e["id"] for e in evidence["items"]],
            )
            ensure(
                bool(evidence["items"]) and all(e["physical_page"] == 1 for e in evidence["items"]),
                "one real physical page",
            )
            ensure(
                any(info["source_text"] == e["quote"] for e in evidence["items"]),
                "complete exact original line preserved",
            )
            (out / (info["role"] + "-evidence.json")).write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
            )
        status, _, state = client.call("GET", f"/bids/{bid}/review-state")
        ensure(status == 200, "read durable review state")
        (out / "review-state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        )
        with connection() as c:
            calls = c.execute(
                "SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s",
                (bid,),
            ).fetchone()["n"]
            operations = c.execute(
                "SELECT id,kind,state FROM operations WHERE bid_id=%s ORDER BY created_at", (bid,)
            ).fetchall()
            parses = c.execute(
                "SELECT parser_digest,config_digest,page_count,coverage FROM parse_runs WHERE bid_id=%s ORDER BY created_at",
                (bid,),
            ).fetchall()
        ensure(
            calls == 0 and all(o["kind"] not in ("prepare-review", "review") for o in operations),
            "no model work queued or billed",
        )
        ensure(
            all(
                p["parser_digest"]
                == "0578f501d377855240d5dae3e42cd295a43496cfc3bf28674e63a1e83ab49165"
                for p in parses
            ),
            "actual parser03 source identity",
        )
        result.update(
            passed=True,
            model_call_rows=calls,
            operations=operations,
            actual_parses=parses,
            review_state=state,
        )
    except Exception as error:
        result["failure"] = dict(
            stage=stage,
            type=type(error).__name__,
            message="Fixture preparation incomplete; no credentials, ticket or provider response logged",
        )
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, default=str, indent=2) + "\n"
    )
    print({key: result.get(key) for key in ["passed", "bid_id", "model_call_rows", "failure"]})
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
