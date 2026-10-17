"""Independent exact-key native OSS multipart resources, synthetic bytes, no buckets scan."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from botocore.exceptions import ClientError
from packages.domain import storage
from packages.domain.runtime import DomainError, assert_role, connection
from packages.uploads.native import NativeMultipart
from packages.uploads.protocol import PART_SIZE


def main():
    root = Path(os.environ["QA_MULTIPART_ROOT"])
    out = root / "execution"
    out.mkdir(exist_ok=False)
    assert Path(__import__("packages.uploads.native", fromlist=["x"]).__file__).is_relative_to(root / "execution-inputs")
    manifest = root / "input-manifest.json"
    declared = json.loads(manifest.read_text())

    def verify():
        assert all(hashlib.sha256((root / "execution-inputs" / row["path"]).read_bytes()).hexdigest() == row["sha256"] for row in declared)

    verify()
    assert_role()
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "external-release.env"
    own_prefix = "workbench-checks/qa-multipart-" + uuid4().hex + "/"
    os.environ["OBJECT_STORE_PREFIX"] = own_prefix
    native = NativeMultipart()
    complete_key, abort_key = "complete-synthetic.pdf", "abort-synthetic.pdf"
    open_uploads = []
    completed = []
    result = dict(passed=False, checks=[], paid_generation_calls=0, real_kafka_consumers_started=0,
                  scope="Native OSS SDK adapter only, own synthetic UUID prefix; no application HTTP, DB workflow, parser or browser acceptance",
                  prefix=own_prefix, no_bucket_scan=True, no_bucket_configuration_changes=True)

    def check(name, value, observation=None):
        result["checks"].append(dict(name=name, passed=bool(value), observation=observation))

    def denied(fn, code):
        try:
            fn()
        except DomainError as exc:
            return exc.code == code
        return False

    try:
        # Only transport bytes: intentionally no claim that this padded fixture is
        # a valid application PDF document or parser accuracy sample.
        data = b"%PDF-1.7\n% independent native multipart synthetic\n" + b"A" * PART_SIZE + b"Z" * 321
        digest = hashlib.sha256(data).hexdigest()
        provider_id = native.initialize(complete_key)
        open_uploads.append((complete_key, provider_id))
        check("new_exact_upload_empty", native.parts(complete_key, provider_id) == [])
        last = native.upload(complete_key, provider_id, 2, data[PART_SIZE:])
        first = native.upload(complete_key, provider_id, 1, data[:PART_SIZE])
        parts = native.parts(complete_key, provider_id)
        check("out_of_order_real_parts_report_exact_sizes_and_etags", parts == [dict(part_number=1, byte_size=PART_SIZE, etag=first), dict(part_number=2, byte_size=len(data)-PART_SIZE, etag=last)])
        native.complete(complete_key, provider_id, parts)
        open_uploads.remove((complete_key, provider_id))
        meta = native.port.head_object(Bucket=storage.bucket(), Key=complete_key)
        completed.append((complete_key, meta.get("VersionId")))
        row = dict(staging_key=complete_key, declared_size=len(data), declared_digest=digest)
        native.verify_complete(row)
        check("completed_real_object_full_readback_sha", meta["ContentLength"] == len(data), dict(byte_size=len(data), sha256=digest))
        check("same_size_wrong_declared_digest_rejected", denied(lambda: native.verify_complete(dict(row, declared_digest="0"*64)), "UPLOAD_DIGEST_MISMATCH"))
        check("wrong_declared_size_rejected", denied(lambda: native.verify_complete(dict(row, declared_size=len(data)-1)), "UPLOAD_SIZE_MISMATCH"))
        # A second exact upload may exist, but complete must never replace the
        # already created original under this same key.
        second = native.initialize(complete_key)
        open_uploads.append((complete_key, second))
        alternative = b"%PDF-1.7\nDifferent synthetic original\n"
        etag = native.upload(complete_key, second, 1, alternative)
        check("native_complete_forbids_existing_object_overwrite", denied(lambda: native.complete(complete_key, second, [dict(part_number=1, etag=etag, byte_size=len(alternative))]), "MULTIPART_PROVIDER_UNAVAILABLE"))
        native.verify_complete(row)
        check("forbidden_overwrite_leaves_original_sha_and_size", True)
        native.abort(complete_key, second)
        open_uploads.remove((complete_key, second))
        aborted = native.initialize(abort_key)
        open_uploads.append((abort_key, aborted))
        native.upload(abort_key, aborted, 1, b"%PDF-1.7\nOnly a temporary part\n")
        native.abort(abort_key, aborted)
        open_uploads.remove((abort_key, aborted))
        native.abort(abort_key, aborted)
        check("exact_abort_replay_is_idempotent", True)
        check("aborted_exact_upload_no_longer_lists_parts", denied(lambda: native.parts(abort_key, aborted), "MULTIPART_PROVIDER_UNAVAILABLE"))
        verify()
        result["passed"] = all(c["passed"] for c in result["checks"])
    except Exception as error:
        result["fatal"] = dict(type=type(error).__name__, code=error.code if isinstance(error, DomainError) else "NATIVE_MULTIPART_PROBE_INCOMPLETE", message="External response and private locator intentionally omitted")
    finally:
        cleanup = []
        for key, provider in open_uploads:
            try:
                native.abort(key, provider)
                cleanup.append(dict(kind="exact_incomplete_session", cleaned=True))
            except Exception:
                cleanup.append(dict(kind="exact_incomplete_session", cleaned=False))
        for key, version in completed:
            try:
                # Check both logical and actual prefixed exact keys, without
                # enumerating other objects or removing any registered object.
                with connection() as c:
                    refs = c.execute("SELECT (SELECT count(*) FROM blobs WHERE object_key=ANY(%s))+(SELECT count(*) FROM uploads WHERE staging_key=ANY(%s)) AS n", ([key, own_prefix+key], [key, own_prefix+key])).fetchone()["n"]
                if refs:
                    cleanup.append(dict(kind="exact_own_completed_object", cleaned=False, reason="database_reference_present"))
                    continue
                native.port.native.delete_object(native._key(key), params={"versionId": version} if version else None)
                try:
                    native.port.head_object(Bucket=storage.bucket(), Key=key)
                except ClientError:
                    deleted = True
                else:
                    deleted = False
                cleanup.append(dict(kind="exact_own_completed_object", cleaned=deleted, references=refs, exact_version=bool(version)))
            except Exception:
                cleanup.append(dict(kind="exact_own_completed_object", cleaned=False))
        result["cleanup"] = cleanup
        result["passed"] = result["passed"] and all(c["cleaned"] for c in cleanup)
    result.update(probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), input_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
    (out / "executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(dict(passed=result["passed"], checks=len(result["checks"]), failed=[c["name"] for c in result["checks"] if not c["passed"]], cleanup=result.get("cleanup"), fatal=result.get("fatal")))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
