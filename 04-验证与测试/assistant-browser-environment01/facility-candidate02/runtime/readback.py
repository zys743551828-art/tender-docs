"""Read-only HTTP contract/original/history checks, no database or provider access."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from safety import MARKER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actual", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    expected_raw = args.actual.read_bytes()
    expected = json.loads(expected_raw)
    source = Path("/workspace/tender-docs/04-验证与测试/assistant-browser-environment01/fixture01")
    original = json.loads((source / "synthetic-source.json").read_text())
    members = json.loads(
        Path("/workspace/tender-workbench/.local/assistant_ui01-test-members.json").read_text()
    )
    if isinstance(members, dict):
        members = members["members"]
    with httpx.Client(base_url="http://127.0.0.1:8816", trust_env=False, timeout=15) as client:
        actual = client.get("/openapi.json")
        if actual.status_code != 200 or actual.headers.get("X-Assistant-Fixture") != MARKER:
            raise AssertionError("Fixture actual contract HTTP binding failed")
        if actual.json() != expected:
            raise AssertionError("HTTP actual differs from frozen actual JSON object")
        login = client.post(
            "/api/v1/auth/login",
            json={"login_name": members[0]["login"], "password": members[0]["password"]},
            headers={"Origin": "http://127.0.0.1:5184"},
        )
        if login.status_code != 200:
            raise AssertionError("Dedicated fixture login failed")
        read = client.get(f"/api/v1/artifacts/{original['document_version_id']}/download")
        if read.status_code != 200:
            raise AssertionError("Local fixture actual authenticated download bridge failed")
        location = urlsplit(read.json()["download_url"])
        if location.scheme != "http" or location.netloc != "127.0.0.1:5184":
            raise AssertionError("Download escaped the explicit fixture Origin")
        blob = client.get(location.path + "?" + location.query)
        if (
            blob.status_code != 200
            or hashlib.sha256(blob.content).hexdigest() != original["original_sha256"]
        ):
            raise AssertionError("Original synthetic bytes changed across cold restart")
        preserved = []
        old = json.loads((source / "http-smoke-result.json").read_text())
        for check in old["checks"]:
            result = client.get("/api/v1/assistant/jobs/" + check["job_id"])
            if result.status_code != 200:
                raise AssertionError("Old assistant job inaccessible after cold restart")
            state = result.json()["operation"]["state"]
            required = (
                "cancelled"
                if "cancel" in check["case"]
                else "failed"
                if "unknown" in check["case"]
                else "succeeded"
            )
            if state != required:
                raise AssertionError("Old terminal job changed after cold restart")
            preserved.append({"case": check["case"], "job_id": check["job_id"], "state": state})
    modules = {}
    candidate_hashes = set()
    events = []
    for mode in ("api", "worker", "publisher"):
        binding = json.loads((args.evidence / (mode + "-loaded-modules.json")).read_text())
        candidate_hashes.add(binding["candidate_sha256"])
        modules[mode] = len(binding["modules"])
        events += [
            json.loads(line)
            for line in (args.evidence / (mode + "-events.jsonl")).read_text().splitlines()
        ]
    if len(candidate_hashes) != 1 or any(event["event"].startswith("blocked_") for event in events):
        raise AssertionError("Mixed candidate or unexpected real HTTP/socket attempt")
    result = {
        "actual_file_sha256": hashlib.sha256(expected_raw).hexdigest(),
        "http_openapi_sha256": hashlib.sha256(actual.content).hexdigest(),
        "exact_json_object_equal": True,
        "file_and_http_whitespace_formats_differ": expected_raw != actual.content,
        "paths": len(expected["paths"]),
        "schemas": len(expected["components"]["schemas"]),
        "original_sha256": original["original_sha256"],
        "download_via_actual_authenticated_proxy": True,
        "original_and_prior_jobs_preserved": preserved,
        "loaded_module_counts": modules,
        "candidate_sha256": candidate_hashes.pop(),
        "real_provider_attempts": 0,
        "unexpected_http_or_nonloopback_attempts": 0,
        "fixture_only": True,
    }
    (args.evidence / "cold-http-readback.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        {
            "readback": "passed",
            "paths": result["paths"],
            "schemas": result["schemas"],
            "prior_jobs": len(preserved),
            "real_provider_attempts": 0,
        }
    )


if __name__ == "__main__":
    main()
