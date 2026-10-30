"""Explicit synthetic HTTP transport acceptance in the complete desktop Compose group."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import psycopg
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from psycopg.rows import dict_row

from deploy import fusion_desktop_compose as desktop
from deploy import fusion_full_probe as probe
from deploy import fusion_full_spec as spec


def require(condition, code):
    if not condition:
        raise spec.ConfigurationError(code)


def private_login(profile):
    directory = Path("/validation-login")
    path = directory / "profile.json"
    require(os.geteuid() == 65532, "DESKTOP_VALIDATION_UID_REQUIRED")
    for target, mode in ((directory, 0o700), (path, 0o600)):
        info = target.lstat()
        require(
            not target.is_symlink() and info.st_uid == 65532 and stat.S_IMODE(info.st_mode) == mode,
            "DESKTOP_VALIDATION_PRIVATE_LOGIN_REQUIRED",
        )
    value = spec.load_private_json(path)
    require(
        isinstance(value, dict)
        and set(value) == {"version", "plan_digest", "origin", "login_name", "password"}
        and value["version"] == desktop.VERSION
        and value["plan_digest"] == profile["plan_digest"]
        and value["origin"] == profile["plan"]["origin"]
        and value["login_name"] == "admin"
        and isinstance(value["password"], str)
        and len(value["password"]) >= 32,
        "DESKTOP_VALIDATION_LOGIN_BINDING_CHANGED",
    )
    return value


def pdf():
    # Fixed two-page synthetic fixture, produced with the existing ReportLab5.0.1
    # check lock and invariant=1. Verification needs no PDF-authoring dependency.
    return base64.b64decode(
        "JVBERi0xLjMKJZOMi54gUmVwb3J0TGFiIEdlbmVyYXRlZCBQREYgZG9jdW1lbnQgKG9wZW5zb3VyY2UpCjEgMCBv"
        "YmoKPDwKL0YxIDIgMCBSCj4+CmVuZG9iagoyIDAgb2JqCjw8Ci9CYXNlRm9udCAvSGVsdmV0aWNhIC9FbmNvZGlu"
        "ZyAvV2luQW5zaUVuY29kaW5nIC9OYW1lIC9GMSAvU3VidHlwZSAvVHlwZTEgL1R5cGUgL0ZvbnQKPj4KZW5kb2Jq"
        "CjMgMCBvYmoKPDwKL0NvbnRlbnRzIDggMCBSIC9NZWRpYUJveCBbIDAgMCA0MjAgNTk1IF0gL1BhcmVudCA3IDAg"
        "UiAvUmVzb3VyY2VzIDw8Ci9Gb250IDEgMCBSIC9Qcm9jU2V0IFsgL1BERiAvVGV4dCAvSW1hZ2VCIC9JbWFnZUMg"
        "L0ltYWdlSSBdCj4+IC9Sb3RhdGUgMCAvVHJhbnMgPDwKCj4+IAogIC9UeXBlIC9QYWdlCj4+CmVuZG9iago0IDAg"
        "b2JqCjw8Ci9Db250ZW50cyA5IDAgUiAvTWVkaWFCb3ggWyAwIDAgNDIwIDU5NSBdIC9QYXJlbnQgNyAwIFIgL1Jl"
        "c291cmNlcyA8PAovRm9udCAxIDAgUiAvUHJvY1NldCBbIC9QREYgL1RleHQgL0ltYWdlQiAvSW1hZ2VDIC9JbWFn"
        "ZUkgXQo+PiAvUm90YXRlIDAgL1RyYW5zIDw8Cgo+PiAKICAvVHlwZSAvUGFnZQo+PgplbmRvYmoKNSAwIG9iago8"
        "PAovUGFnZU1vZGUgL1VzZU5vbmUgL1BhZ2VzIDcgMCBSIC9UeXBlIC9DYXRhbG9nCj4+CmVuZG9iago2IDAgb2Jq"
        "Cjw8Ci9BdXRob3IgKGFub255bW91cykgL0NyZWF0aW9uRGF0ZSAoRDoyMDAwMDEwMTAwMDAwMCswMCcwMCcpIC9D"
        "cmVhdG9yIChhbm9ueW1vdXMpIC9LZXl3b3JkcyAoKSAvTW9kRGF0ZSAoRDoyMDAwMDEwMTAwMDAwMCswMCcwMCcp"
        "IC9Qcm9kdWNlciAoUmVwb3J0TGFiIFBERiBMaWJyYXJ5IC0gXChvcGVuc291cmNlXCkpIAogIC9TdWJqZWN0ICh1"
        "bnNwZWNpZmllZCkgL1RpdGxlICh1bnRpdGxlZCkgL1RyYXBwZWQgL0ZhbHNlCj4+CmVuZG9iago3IDAgb2JqCjw8"
        "Ci9Db3VudCAyIC9LaWRzIFsgMyAwIFIgNCAwIFIgXSAvVHlwZSAvUGFnZXMKPj4KZW5kb2JqCjggMCBvYmoKPDwK"
        "L0ZpbHRlciBbIC9BU0NJSTg1RGVjb2RlIC9GbGF0ZURlY29kZSBdIC9MZW5ndGggMTUyCj4+CnN0cmVhbQpHYXBA"
        "RTVuOEsjJjRRPmBpXC0hU20wSHQ0NnMlQTtGK0gqUlRkam1dWmdtYWAwZ0JyQVFeOkxQcXJnT1FKMj81MS0tdXI8"
        "NWI3L1QyZEVsSDRvTmpcVUAwW1lfLXFSOVNcN00vakw7JkwhYVQqYzhtVzYtYCMhUGZdWVFgVUxDYmNDKFdKRyYp"
        "WjBSb2hkXVYtQDtAKzt+PmVuZHN0cmVhbQplbmRvYmoKOSAwIG9iago8PAovRmlsdGVyIFsgL0FTQ0lJODVEZWNv"
        "ZGUgL0ZsYXRlRGVjb2RlIF0gL0xlbmd0aCAxNTAKPj4Kc3RyZWFtCkdhcEBFMGIyJlMmLVZtQ0BTMzpJNSJJa2Y2"
        "bjFVQklEQmtDMFFRMDc1ZiluRCNkI3ImQlhFQENQOTlgJyNBKVdzWjlydTcyRG9nP1cpYEJZOlxVMFBaMDkhZyZw"
        "dGBeVlY/SFNoQmFMbSU+SnE5YUJfP3RSRG4+TG45REpYcWlYJ08qbUdWOlVSVV4oZUhiRHIsLTF+PmVuZHN0cmVh"
        "bQplbmRvYmoKeHJlZgowIDEwCjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDA2MSAwMDAwMCBuIAowMDAwMDAw"
        "MDkyIDAwMDAwIG4gCjAwMDAwMDAxOTkgMDAwMDAgbiAKMDAwMDAwMDM5MiAwMDAwMCBuIAowMDAwMDAwNTg1IDAw"
        "MDAwIG4gCjAwMDAwMDA2NTMgMDAwMDAgbiAKMDAwMDAwMDkxNCAwMDAwMCBuIAowMDAwMDAwOTc5IDAwMDAwIG4g"
        "CjAwMDAwMDEyMjEgMDAwMDAgbiAKdHJhaWxlcgo8PAovSUQgCls8MWMxNzgxOThmYmRmYTUxYjI1OTk1ZDg5ZDQx"
        "MDIwNDM+PDFjMTc4MTk4ZmJkZmE1MWIyNTk5NWQ4OWQ0MTAyMDQzPl0KJSBSZXBvcnRMYWIgZ2VuZXJhdGVkIFBE"
        "RiBkb2N1bWVudCAtLSBkaWdlc3QgKG9wZW5zb3VyY2UpCgovSW5mbyA2IDAgUgovUm9vdCA1IDAgUgovU2l6ZSAx"
        "MAo+PgpzdGFydHhyZWYKMTQ2MQolJUVPRgo=",
        validate=True,
    )


def denied(callback):
    try:
        callback()
    except ClientError as error:
        require(
            error.response["ResponseMetadata"]["HTTPStatusCode"] == 403,
            "DESKTOP_STORAGE_DENIAL_STATUS_CHANGED",
        )
        return
    raise spec.ConfigurationError("DESKTOP_STORAGE_ACCESS_NOT_DENIED")


def storage_checks(profile):
    environment = profile["environment"]
    client = probe.store_client(environment)
    bucket, marker = environment["OBJECT_STORE_BUCKET"], profile["probe"]["key"]
    wrong = dict(environment, OBJECT_STORE_SECRET_KEY="synthetic-wrong-secret-" + uuid4().hex)
    denied(lambda: probe.store_client(wrong).get_object(Bucket=bucket, Key=marker))
    import boto3

    anonymous = boto3.client(
        "s3",
        endpoint_url=environment["OBJECT_STORE_ENDPOINT"],
        region_name=environment["OBJECT_STORE_REGION"],
        config=Config(signature_version=UNSIGNED, retries={"max_attempts": 0}),
    )
    denied(lambda: anonymous.get_object(Bucket=bucket, Key=marker))
    other = "synthetic-other-" + uuid4().hex
    denied(lambda: client.put_object(Bucket=other, Key="denied", Body=b"synthetic"))
    key = environment["OBJECT_STORE_PREFIX"] + "desktop-check/" + uuid4().hex
    raw = b"Synthetic exact store bytes"
    try:
        client.put_object(Bucket=bucket, Key=key, Body=raw)
        with client.get_object(Bucket=bucket, Key=key)["Body"] as stream:
            require(stream.read() == raw, "DESKTOP_STORAGE_BYTES_CHANGED")
    finally:
        client.delete_object(Bucket=bucket, Key=key)
    return {
        "anonymous_denied": True,
        "wrong_key_denied": True,
        "other_bucket_write_denied": True,
        "exact_put_get": True,
    }


def run():
    profile = spec.load_private_json(Path("/runtime/profile.json"))
    ready = probe.check(profile)
    login = private_login(profile)
    origin = login["origin"]
    authority = urlsplit(origin).netloc

    def local_url(url):
        parsed = urlsplit(url)
        require(
            parsed.scheme == "http"
            and parsed.netloc == authority
            and parsed.path.startswith("/api/v1/object-transfers/"),
            "DESKTOP_SAME_ORIGIN_TICKET_REQUIRED",
        )
        return parsed.path + ("?" + parsed.query if parsed.query else "")

    def connect():
        return httpx.Client(
            base_url="http://web:8080",
            headers={"Host": authority},
            trust_env=False,
            timeout=15,
            follow_redirects=False,
        )

    dsn = profile["environment"]["API_DATABASE_DSN"]
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        require(
            connection.execute("SELECT count(*) AS n FROM design.bids").fetchall()[0]["n"] == 0,
            "DESKTOP_VALIDATION_NEW_DATABASE_REQUIRED",
        )
    stores = storage_checks(profile)
    with connect() as client, connect() as anonymous, connect() as another:
        response = client.post(
            "/api/v1/auth/login",
            json={"login_name": login["login_name"], "password": login["password"]},
            headers={"Origin": origin},
        )
        require(response.status_code == 200, "DESKTOP_HTTP_LOGIN_FAILED")
        csrf = response.json()["csrf_token"]

        def headers(etag=None):
            return {"Origin": origin, "X-CSRF-Token": csrf, "Idempotency-Key": str(uuid4())} | (
                {"If-Match": etag} if etag else {}
            )

        def post(path, body, status, etag=None):
            response = client.post(path, json=body, headers=headers(etag))
            require(response.status_code == status, "DESKTOP_HTTP_COMMAND_FAILED")
            return response

        bid = post("/api/v1/bids", {"title": "合成完整Compose文件链验收"}, 201).json()["id"]
        root = f"/api/v1/bids/{bid}"
        batch = post(root + "/upload-batches", {}, 201).json()["id"]
        raw = pdf()
        digest = hashlib.sha256(raw).hexdigest()
        grant = post(
            root + "/uploads",
            {
                "batch_id": batch,
                "filename": "synthetic-desktop.pdf",
                "role": "supplement",
                "declared_sha256": digest,
                "byte_size": len(raw),
                "target": {"kind": "new_file"},
            },
            200,
        )
        upload = grant.json()["upload_id"]
        stale = local_url(grant.json()["upload_url"])
        refreshed = post(
            f"/api/v1/uploads/{upload}/authorizations",
            {"part_numbers": [1]},
            200,
            grant.headers["etag"],
        )
        url = local_url(refreshed.json()["upload_url"])
        require(
            client.put(stale, content=raw, headers=headers()).status_code == 403,
            "DESKTOP_STALE_TRANSFER_ACCEPTED",
        )
        require(
            anonymous.put(url, content=raw, headers=headers()).status_code == 401,
            "DESKTOP_ANONYMOUS_UPLOAD_ACCEPTED",
        )
        second = another.post(
            "/api/v1/auth/login",
            json={"login_name": login["login_name"], "password": login["password"]},
            headers={"Origin": origin},
        )
        require(second.status_code == 200, "DESKTOP_SECOND_LOGIN_FAILED")
        require(
            another.put(
                url, content=raw, headers=headers() | {"X-CSRF-Token": second.json()["csrf_token"]}
            ).status_code
            == 403,
            "DESKTOP_CROSS_SESSION_TRANSFER_ACCEPTED",
        )
        require(
            client.put(
                url, content=raw, headers=headers() | {"Content-Type": "application/pdf"}
            ).status_code
            == 200,
            "DESKTOP_UPLOAD_FAILED",
        )
        post(f"/api/v1/uploads/{upload}/complete", {"parts": [], "declared_sha256": digest}, 202)

        def eventually(path, finished):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                response = client.get(path)
                require(response.status_code == 200, "DESKTOP_HTTP_OBSERVATION_FAILED")
                value = response.json()
                state = value.get("state", value.get("operation", {}).get("state"))
                require(state not in {"failed", "cancelled"}, "DESKTOP_TRANSPORT_TASK_FAILED")
                if state == finished:
                    return value
                time.sleep(0.25)
            raise spec.ConfigurationError("DESKTOP_TRANSPORT_WAIT_EXPIRED")

        verified = eventually(f"/api/v1/uploads/{upload}", "verified")
        current = client.get(root)
        committed = post(
            root + f"/upload-batches/{batch}/commit",
            {
                "accepted_upload_ids": [upload],
                "accepted_binding_ids": [verified["binding_id"]],
                "explicit_partial_acceptance": False,
            },
            202,
            current.headers["etag"],
        )
        eventually("/api/v1/operations/" + committed.json()["operation"]["id"], "succeeded")
        with psycopg.connect(dsn, row_factory=dict_row) as connection:
            document = str(
                connection.execute(
                    "SELECT verified_version_id FROM design.uploads WHERE id=%s", (upload,)
                ).fetchall()[0]["verified_version_id"]
            )
        context_url = root + f"/documents/{document}"
        context = client.get(context_url + "/parse-context")
        require(context.status_code == 200, "DESKTOP_PARSE_CONTEXT_FAILED")
        requested = post(
            context_url + "/parse-requests",
            {
                "context_digest": context.json()["context_digest"],
                "reason": "合成Compose实际文件链验收",
            },
            202,
        ).json()["request"]["id"]
        parsed = eventually(context_url + "/parse-requests/" + requested, "succeeded")
        require(
            parsed["result"]["coverage"] == "partial" and not parsed["current_body_changed"],
            "DESKTOP_PARSE_QUALIFICATION_CHANGED",
        )
        signed = client.get(f"/api/v1/artifacts/{document}/download")
        require(signed.status_code == 200, "DESKTOP_DOWNLOAD_TICKET_FAILED")
        download = local_url(signed.json()["download_url"])
        require(client.get(download).content == raw, "DESKTOP_DOWNLOAD_BYTES_CHANGED")
        require(anonymous.get(download).status_code == 401, "DESKTOP_ANONYMOUS_DOWNLOAD_ACCEPTED")
        require(
            client.post("/api/v1/auth/logout", headers=headers()).status_code == 204,
            "DESKTOP_LOGOUT_FAILED",
        )
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        for table in ("model_calls", "assistant_jobs", "task_definitions"):
            # Fixed identifiers only, never supplied by a caller.
            from psycopg import sql

            require(
                connection.execute(
                    sql.SQL("SELECT count(*) AS n FROM design.{}").format(sql.Identifier(table))
                ).fetchall()[0]["n"]
                == 0,
                "DESKTOP_PAID_OR_AUTOMATIC_WORK_FOUND",
            )
        state = connection.execute(
            "SELECT current_body_id,current_review_id FROM design.bid_profiles WHERE bid_id=%s",
            (bid,),
        ).fetchone()
        require(
            state == {"current_body_id": None, "current_review_id": None},
            "DESKTOP_CURRENT_BUSINESS_POINTER_CHANGED",
        )
        require(
            connection.execute(
                "SELECT count(*) AS n FROM design.document_parse_results WHERE request_id=%s",
                (requested,),
            ).fetchall()[0]["n"]
            == 1,
            "DESKTOP_PARSE_RESULT_NOT_UNIQUE",
        )
    return {
        "ok": True,
        "runtime_uid": 65532,
        "nonowner": ready["database"]["nonowner"],
        "plan_digest": profile["plan_digest"],
        "storage": stores,
        "same_origin_http": True,
        "session_and_ticket_isolation": True,
        "kafka_worker_parse": True,
        "download_sha256": digest,
        "physical_pages": 2,
        "coverage": "partial",
        "current_body_changed": False,
        "synthetic_bid_id": bid,
        "provider_calls": 0,
    }


def main():
    try:
        result = run()
    except Exception as error:
        code = (
            str(error)
            if isinstance(error, spec.ConfigurationError) and re.fullmatch(r"[A-Z_]+", str(error))
            else "DESKTOP_TRANSPORT_VERIFICATION_FAILED"
        )
        print(json.dumps({"ok": False, "code": code, "failure_type": type(error).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
