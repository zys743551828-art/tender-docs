# Explicit private OSS new-object writes

Read-only review found storage is documented as a private-object port, and storage.put directly constructs OssClient via storage.client/from_env. No other layer supplied a private object ACL. Existing native evidence manually supplied private headers before the adapter roundtrip, so it did not prove the normal app put policy.

Only packages/domain/providers/oss.py and existing tests/test_providers.py changed. New-object SDK puts now explicitly carry x-oss-object-acl=private along with unchanged content type, overwrite prohibition and SHA metadata. No bucket operation, stored-object ACL rewrite or storage-class change was introduced. Existing 409 behavior reads and compares actual bytes and never retries an overwrite; existing object ACLs remain untouched.

The new offline regression traverses storage.put/get -> storage.client -> OssClient.from_env, replacing only the native SDK Bucket at its transport boundary. It checks actual V4/session construction, namespace preservation, all three put attempts private and immutable, exact same-byte dedup, differing-byte conflict, no fallback write, original bytes retained, and exact call sequence. Existing signing/config/prefix/transport/error-scrubbing checks also run.

Red: new test failed with missing private header before the one-line provider fix. Green: 10 OSS unit cases passed, 17 unrelated cases deselected, zero skips/failures; one existing SDK utcnow deprecation warning. Ruff, format, provider mypy and Bandit exited0. Executed on the user's Windows current C Python environment with explicit no-profile environment and bytecode/plugin autoload disabled. No network, external OSS, paid/model call, live instance or Git write occurred.

summary.json freezes exact LF source hashes. These mock results prove the request boundary and conflict behavior, not live OSS object privacy; root owns the later unique-synthetic-object live from_env roundtrip and exact deletion. No credentials, configuration values, signed URLs or object bytes are stored in this evidence.
