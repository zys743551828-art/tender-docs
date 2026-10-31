# Limited safe diagnostics regression

52 passed / 0 failed / 0 errors / 0 skipped: desktop_bootstrap_input 37 and fusion_desktop_private 15. Only these two test files were run; no previous 138/158/527 suite rerun.

fusion_desktop_private.py Ruff, format, mypy --platform linux and Bandit -ll all exit0. Actual commands, exits, logs, JUnit and source SHA are in summary.json / runtime.json / targets.xml. The tests validate the safe diagnostic whitelist and rejection of credential-shaped unknown codes; they do not prove the unknown real S3 ClientError is fixed.

Fixed runtime620f, Python3.12.16, UID65532, network none, read-only root and source, 1GiB memory, tmpfs512MiB, no Docker socket or business environment. No real object store, credentials, model calls or product/Git modifications. resources.json records exact owned container removed and closed=true. Task runner snapshots are evidence, not product entry points.
