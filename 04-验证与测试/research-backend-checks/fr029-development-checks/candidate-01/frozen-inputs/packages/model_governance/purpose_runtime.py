"""CPU-purpose runner: no .env loader, model transport, object store or shared Worker imports."""

import argparse
import json
import os
import subprocess  # nosec B404
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from packages.model_governance import evaluation, training
from packages.model_governance.common import GovernanceError, digest

ROLES = {
    "train": ("MODEL_TRAIN_DATABASE_DSN", "tender_model_train"),
    "evaluate": ("MODEL_EVALUATE_DATABASE_DSN", "tender_model_evaluate"),
}


def child_environment(role, environment):
    if role not in ROLES:
        raise GovernanceError("PURPOSE_INVALID", "Unsupported CPU process purpose")
    key = ROLES[role][0]
    if not environment.get(key):
        raise GovernanceError("PURPOSE_NOT_CONFIGURED", "Separate purpose LOGIN DSN required")
    # Deliberately do not inherit API/owner DSN, general .env selection, bucket credentials,
    # provider keys, proxy auth, PYTHONPATH, or arbitrary environment variables.
    return {key: environment[key], "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}


def launch(role, job_id, *, timeout=300):
    role = str(role)
    job = str(UUID(str(job_id)))
    root = str(Path(__file__).resolve().parents[2])
    bootstrap = 'import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module("packages.model_governance.purpose_runtime",run_name="__main__")'
    return subprocess.run(  # nosec B603
        [sys.executable, "-I", "-c", bootstrap, root, "--role", role, "--job-id", job],
        cwd=root,
        env=child_environment(role, os.environ),
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
    )


@contextmanager
def connection(role):
    key, capability = ROLES[role]
    if set(os.environ) - {key, "LANG", "LC_ALL", "LC_CTYPE", "TZ"}:
        raise GovernanceError(
            "PURPOSE_ENVIRONMENT_NOT_ISOLATED", "Launch with the purpose environment whitelist"
        )
    if not os.environ.get(key):
        raise GovernanceError("PURPOSE_NOT_CONFIGURED", "Separate purpose LOGIN DSN required")
    with psycopg.connect(os.environ[key], row_factory=dict_row) as c:
        who = c.execute(
            "SELECT current_user,rolsuper,rolbypassrls,pg_has_role(current_user,%s,'MEMBER') AS allowed FROM pg_roles WHERE rolname=current_user",
            (capability,),
        ).fetchone()
        if who is None or who["rolsuper"] or who["rolbypassrls"] or not who["allowed"]:
            raise GovernanceError(
                "PURPOSE_ROLE_INVALID", "Dedicated nonowner purpose LOGIN required"
            )
        ownership = c.execute(
            "SELECT EXISTS(SELECT FROM pg_class WHERE relnamespace IN (to_regnamespace('design'),to_regnamespace('retrieval'),to_regnamespace('model_governance')) AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)) AS owns"
        ).fetchone()
        if ownership is None or ownership["owns"]:
            raise GovernanceError(
                "PURPOSE_ROLE_INVALID", "Purpose role must not own business tables"
            )
        for forbidden in (
            "tender_api",
            "tender_worker",
            "tender_research_worker",
            "tender_research_read",
            "tender_score_read",
            *(value[1] for key, value in ROLES.items() if key != role),
        ):
            membership = c.execute(
                "SELECT pg_has_role(current_user,%s,'MEMBER') AS allowed", (forbidden,)
            ).fetchone()
            if membership is None or membership["allowed"]:
                raise GovernanceError(
                    "PURPOSE_ROLE_INVALID", "Purpose LOGIN must not inherit another business role"
                )
        yield c


def evaluate_bundle(bundle):
    protocol = bundle["protocol"]
    training.validate_protocol(protocol)
    labels = bundle["labels"]
    if (
        digest(labels) != bundle["labels_digest"]
        or labels["schema"] != "sealed-final-label-export/1"
        or labels["origin"] != bundle["origin"]
    ):
        raise GovernanceError(
            "FINAL_EXPORT_CORRUPTED", "Sealed final label bytes or origin mismatch"
        )
    predictions = bundle["predictions"]
    by_id = {row["id"]: row for row in predictions["rows"]}
    if len(by_id) != len(predictions["rows"]) or set(by_id) != {
        row["id"] for row in labels["rows"]
    }:
        raise GovernanceError(
            "DENOMINATOR_MISMATCH",
            "Predictions must be frozen for every final member before labels",
        )
    rows = []
    for label in labels["rows"]:
        prediction = by_id[label["id"]]
        rows.append(
            {**label, "prediction": prediction.get("prediction"), "status": prediction["status"]}
        )
    report = evaluation.metrics(
        rows,
        expected_ids=sorted(by_id),
        intervals=predictions["intervals"],
        seed=protocol["seed"],
        bootstrap_iterations=protocol["bootstrap_iterations"],
        baseline_predictions=predictions["baseline_predictions"],
    )
    return {
        "schema": "independent-final-evaluation/1",
        "origin": bundle["origin"],
        "research_only": bundle["origin"] == "synthetic",
        "formal_eligibility": "unavailable",
        "model_digest": bundle["model_digest"],
        "protocol_digest": digest(protocol),
        "predictions_digest": digest(predictions),
        "labels_digest": digest(labels),
        "report": report,
        "blocked_reasons": [
            "business_acceptance_thresholds_and_observation_not_approved",
            *(["synthetic_permanent_research_only"] if bundle["origin"] == "synthetic" else []),
        ],
    }


def heartbeat(role, job_id, token, fence, stop, lost):
    while not stop.wait(30):
        try:
            with connection(role) as c:
                row = c.execute(
                    "SELECT model_governance.renew_job(%s,%s,%s) AS value", (job_id, token, fence)
                ).fetchone()
                if not row or not row["value"]:
                    lost.set()
                    return
        except Exception:
            lost.set()
            return


def process(role, job_id):
    token = uuid4()
    with connection(role) as c:
        lease = c.execute(
            "SELECT model_governance.claim_job(%s,%s) AS value", (job_id, token)
        ).fetchone()["value"]
    if lease is None:
        return {"state": "not_acquired"}
    fence = lease["fence"]
    stop, lost = threading.Event(), threading.Event()
    thread = threading.Thread(
        target=heartbeat, args=(role, job_id, token, fence, stop, lost), daemon=True
    )
    thread.start()
    try:
        if role == "train":
            with connection(role) as c:
                bound = c.execute(
                    "SELECT model_governance.read_training(%s,%s,%s) AS value",
                    (job_id, token, fence),
                ).fetchone()["value"]
            if (
                not bound
                or digest(bound["export"]) != bound["export_digest"]
                or bound["export"]["protocol"] != bound["protocol"]
                or bound["export"]["origin"] != bound["origin"]
            ):
                raise GovernanceError(
                    "TRAINING_EXPORT_CORRUPTED", "Purpose-bound training digest mismatch"
                )
            # CPU computation begins only after the connection/transaction is closed.
            result = training.run(bound["export"])
        else:
            with connection(role) as c:
                c.execute(
                    "SELECT model_governance.open_final(%s,%s,%s,%s)",
                    (job_id, token, fence, uuid4()),
                )
            # Separate committed transaction: cancellation or failure cannot erase exposure.
            with connection(role) as c:
                bound = c.execute(
                    "SELECT model_governance.read_final(%s,%s,%s) AS value", (job_id, token, fence)
                ).fetchone()["value"]
            result = evaluate_bundle(bound)
        if lost.is_set():
            raise GovernanceError(
                "PURPOSE_LEASE_LOST", "CPU result did not receive a current source-bound lease"
            )
        with connection(role) as c:
            # SQL JSONB text is a separately explicit storage digest, not Python canonical digest.
            sql_digest = c.execute(
                "SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex') AS value",
                (Jsonb(result),),
            ).fetchone()["value"]
            c.execute(
                "SELECT model_governance.finish_job(%s,%s,%s,%s,%s)",
                (job_id, token, fence, sql_digest, Jsonb(result)),
            )
        return {
            "state": "succeeded",
            "job_id": str(job_id),
            "result_digest": digest(result),
            "formal_eligibility": "unavailable",
        }
    finally:
        stop.set()
        thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument("--job-id", type=UUID, required=True)
    arguments = parser.parse_args()
    try:
        result = process(arguments.role, arguments.job_id)
        print(json.dumps(result))
    except GovernanceError as error:
        print(json.dumps({"state": "failed", "code": error.code}))
        raise SystemExit(1) from None
    except Exception:
        # No exception text/DSN, source content or label payload on stdout/stderr.
        print(json.dumps({"state": "failed", "code": "PURPOSE_JOB_FAILED"}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
