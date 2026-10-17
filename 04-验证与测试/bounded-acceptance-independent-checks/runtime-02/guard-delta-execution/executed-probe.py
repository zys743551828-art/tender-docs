"""Manifest/self/dependency/import-origin delta on frozen bounded-tool02; offline only."""

import hashlib
import io
import json
import os
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from apps.worker import engine  # noqa: F401 -- load ordinary runtime origins for verification
from deploy import review_acceptance as tool
from packages.domain import model_calls, reviews  # noqa: F401
from packages.domain.runtime import DomainError


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(os.environ["QA_GUARD_ROOT"])
    out = root / "guard-delta-execution"
    out.mkdir(exist_ok=False)
    candidate = root / "runtime-candidate.json"
    expected = "fe338be6eb4aa31c7af5cb949500ab3688c392c67e630694fc8f85327ccbee10"
    assert digest(candidate) == expected
    assert (
        digest(Path(tool.__file__))
        == "97d37e942c04007ee2dfbd218f5cab6a4bbb9e05e9d737608e2bbe08cd448e0f"
    )
    result = dict(
        passed=False,
        checks=[],
        paid_generation_calls=0,
        real_kafka_consumers_started=0,
        execute_entry_invocations=0,
    )

    def check(name, value):
        result["checks"].append(dict(name=name, passed=bool(value)))

    def denied(fn, code):
        try:
            fn()
        except DomainError as error:
            return error.code == code
        return False

    def manifest(name, data):
        p = out / (name + ".json")
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return p, digest(p)

    declared = json.loads(candidate.read_text())
    try:
        verify = tool.frozen_sources(candidate, expected)
        verify()
        check(
            "all127_sources_and_loaded_required_origins_match",
            tool.REQUIRED_SOURCES <= set(r["path"] for r in declared["sources"]),
        )
        check(
            "wrong_approved_manifest_hash_rejected",
            denied(lambda: tool.frozen_sources(candidate, "0" * 64), "ACCEPTANCE_MANIFEST_CHANGED"),
        )
        copied, copied_sha = manifest("manifest-identity", declared)
        verify_copy = tool.frozen_sources(copied, copied_sha)
        copied.write_text(copied.read_text() + " ")
        check(
            "manifest_bytes_mutated_with_same_semantics_rejected_on_verify",
            denied(verify_copy, "ACCEPTANCE_MANIFEST_CHANGED"),
        )
        omitted = {
            **declared,
            "sources": [
                r
                for r in declared["sources"]
                if r["path"] != "packages/domain/providers/deepseek.py"
            ],
        }
        p, p_sha = manifest("missing-provider", omitted)
        check(
            "valid_manifest_hash_cannot_omit_required_provider",
            denied(lambda: tool.frozen_sources(p, p_sha), "ACCEPTANCE_DEPENDENCIES_MISSING"),
        )
        duplicate = {**declared, "sources": declared["sources"] + [declared["sources"][0]]}
        p, p_sha = manifest("duplicate", duplicate)
        try:
            tool.frozen_sources(p, p_sha)
        except ValueError:
            rejected = True
        else:
            rejected = False
        check("duplicate_source_identity_rejected", rejected)
        before = dict(sys.modules)
        wrong = types.ModuleType("packages.domain.providers.deepseek")
        wrong.__file__ = "/tmp/identical-but-foreign/deepseek.py"
        with patch.dict(sys.modules, {"packages.domain.providers.deepseek": wrong}):
            check(
                "foreign_loaded_module_path_rejected_even_with_manifest_hash_valid",
                denied(verify, "ACCEPTANCE_IMPORT_MISMATCH"),
            )
        assert (
            sys.modules["packages.domain.providers.deepseek"]
            is before["packages.domain.providers.deepseek"]
        )
        marker = tool.ROOT / "qa_guard_optional_marker.txt"
        marker.write_text("independent marker stable\n")
        extended = {
            **declared,
            "sources": declared["sources"] + [dict(path=marker.name, sha256=digest(marker))],
        }
        p, p_sha = manifest("optional-source", extended)
        optional_verify = tool.frozen_sources(p, p_sha)
        marker.write_text("independent marker changed\n")
        check(
            "actual_source_mutation_after_preflight_rejected",
            denied(optional_verify, "ACCEPTANCE_SOURCE_CHANGED"),
        )
        marker.unlink()
        bid = uuid4()
        sent = []
        boundary = tool.AcceptanceBoundary(
            bid, lambda *a, **k: sent.append(k["logical_key"]), verify
        )

        def invoke(key, selected=bid):
            return boundary(
                {"bid_id": str(selected)},
                {},
                logical_key=key,
                messages=[{"role": "user", "content": "offline"}],
                json_schema={},
                max_tokens=8192,
            )

        def race(i):
            try:
                invoke("core-judgment" if i % 2 else "extract-rules")
                return True
            except DomainError:
                return False

        with ThreadPoolExecutor(max_workers=16) as pool:
            accepted = list(pool.map(race, range(32)))
        check(
            "full_manifest_verification_under_concurrent_two_key_limit",
            sum(accepted) == 2
            and sorted(sent) == ["core-judgment", "extract-rules"]
            and boundary.started == 2,
        )
        check(
            "foreign_bid_rejected_before_upstream",
            denied(lambda: invoke("extract-rules", uuid4()), "ACCEPTANCE_SCOPE_REJECTED")
            and len(sent) == 2,
        )
        stdout = io.StringIO()
        args = [
            "review-acceptance",
            "--bid-id",
            str(bid),
            "--candidate",
            str(candidate),
            "--candidate-sha256",
            expected,
        ]
        with (
            patch("sys.argv", args),
            patch.object(tool, "execute", side_effect=AssertionError("execute forbidden")),
            redirect_stdout(stdout),
        ):
            tool.main()
        preview = json.loads(stdout.getvalue())
        check(
            "preview_is_offline_and_declares_start_window_semantics",
            preview["mode"] == "preview_only_no_external_calls"
            and "new-call start window" in preview["deadline_semantics"],
        )
        with (
            patch("sys.argv", args[:-2]),
            patch.object(tool, "execute", side_effect=AssertionError("execute forbidden")),
            redirect_stderr(io.StringIO()),
        ):
            try:
                tool.main()
            except SystemExit as error:
                missing = error.code == 2
            else:
                missing = False
        check("cli_requires_explicit_manifest_sha", missing)
        result["passed"] = all(c["passed"] for c in result["checks"])
    except Exception as error:
        result["fatal"] = dict(
            type=type(error).__name__,
            message="Independent bounded02 delta incomplete; no private configuration logged",
        )
    result.update(
        runtime_candidate_sha256=expected,
        tool_sha256=digest(Path(tool.__file__)),
        probe_sha256=digest(Path(__file__)),
    )
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
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
