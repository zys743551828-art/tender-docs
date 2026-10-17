"""Actual release HTTP read/retrieval; no generation/review or bucket setting changes."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from jsonschema import Draft202012Validator, FormatChecker


def main():
    out = Path(os.environ["QA_RAG_OUTPUT"])
    out.mkdir(parents=True, exist_ok=False)
    members_path = Path(".local/external-test-members.json")
    assert members_path.stat().st_mode & 0o077 == 0
    member = json.loads(members_path.read_text())[0]
    bid = "79a7fdd5-71ab-4d20-9af3-2bd1853de6e7"
    base, origin = "http://127.0.0.1:8802", "http://127.0.0.1:5175"
    result = dict(
        passed=False,
        checks=[],
        paid_generation_calls=0,
        scope="Actual TCP8802 retrieval on own synthetic real OSS PDF project; no formal scoring or labeled retrieval-quality claim",
    )

    def check(name, value):
        result["checks"].append(dict(name=name, passed=bool(value)))

    with httpx.Client(base_url=base, trust_env=False, timeout=90) as client:
        actual = client.get("/openapi.json")
        assert actual.status_code == 200
        (out / "live.openapi.json").write_bytes(actual.content)
        api = actual.json()
        check(
            "live_73paths_80operations",
            len(api["paths"]) == 73
            and sum(
                method in {"get", "post", "patch", "delete", "put", "head", "options"}
                for path in api["paths"].values()
                for method in path
            )
            == 80,
        )
        check(
            "live_contract_matches_export",
            api == json.loads(Path("/tmp/a1-implemented.openapi.json").read_text()),
        )
        body = dict(
            bid_id=bid,
            context="body_evidence",
            query="municipal construction evidence first physical page",
            limit=4,
        )
        anonymous = client.post(
            "/api/v1/retrievals",
            json=body,
            headers={"Origin": origin, "Idempotency-Key": uuid4().hex},
        )
        check("anonymous_retrieval_401", anonymous.status_code == 401)
        login = client.post(
            "/api/v1/auth/login",
            json={"login_name": member["login"], "password": member["password"]},
            headers={"Origin": origin},
        )
        assert login.status_code == 200
        headers = {
            "Origin": origin,
            "X-CSRF-Token": login.json()["csrf_token"],
            "Idempotency-Key": uuid4().hex,
        }
        no_csrf = client.post(
            "/api/v1/retrievals",
            json=body,
            headers={"Origin": origin, "Idempotency-Key": uuid4().hex},
        )
        check("missing_csrf_403", no_csrf.status_code == 403)
        response = client.post("/api/v1/retrievals", json=body, headers=headers)
        assert response.status_code == 200
        retrieved = response.json()
        schema = {"$ref": "#/components/schemas/RetrievalResult", "components": api["components"]}
        check(
            "live_retrieval_response_schema",
            not list(
                Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(retrieved)
            ),
        )
        check(
            "actual_pdf_hits_partial_and_bound_to_page",
            retrieved["coverage"] == "partial"
            and bool(retrieved["gaps"])
            and bool(retrieved["hits"])
            and all(
                hit["physical_page"] == 1 and "municipal" in hit["excerpt"].lower()
                for hit in retrieved["hits"]
            ),
        )
        replay = client.post("/api/v1/retrievals", json=body, headers=headers)
        check(
            "same_key_revalidated_exact_replay",
            replay.status_code == 200
            and replay.headers.get("Idempotent-Replayed") == "true"
            and replay.json() == retrieved,
        )
        versions = sorted({hit["source_version_id"] for hit in retrieved["hits"]})
        for version in versions:
            parsed = client.get(f"/api/v1/documents/{version}/evidence")
            assert parsed.status_code == 200
            evidence = parsed.json()
            selected = [hit for hit in retrieved["hits"] if hit["source_version_id"] == version]
            check(
                "citation_uses_actual_parse_and_verbatim_physical_page_" + version,
                all(
                    hit["parse_run_id"] == evidence["parse_run_id"]
                    and any(
                        item["physical_page"] == hit["physical_page"]
                        and hit["excerpt"] in item["quote"]
                        for item in evidence["items"]
                    )
                    for hit in selected
                ),
            )
            (out / f"parse-{version}.json").write_text(
                json.dumps(parsed.json(), ensure_ascii=False, indent=2) + "\n"
            )
        (out / "retrieval-response.json").write_text(
            json.dumps(retrieved, ensure_ascii=False, indent=2) + "\n"
        )
        result.update(
            live_openapi_sha256=hashlib.sha256(actual.content).hexdigest(),
            canonical_openapi_sha256=hashlib.sha256(
                json.dumps(api, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            project_id=bid,
        )
    result.update(
        passed=all(c["passed"] for c in result["checks"]),
        probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
