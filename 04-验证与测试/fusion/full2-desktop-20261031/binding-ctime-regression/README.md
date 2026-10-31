# Binding-file path/handle timestamp regression

The real Windows second executor rejected an unchanged valid binding before creating its report directory. Path and handle APIs returned different ctime representations, while each remained stable and all common file-identity fields agreed. The original stderr was discarded by the launcher; the stable error was reproduced by calling the original loader on the same saved public file, not reconstructed from process output.

The loader now compares common path/handle identity and retains complete separate before/after signatures for each API, including its own ctime. Reparse/symlink, bounded-size, inode, metadata and duplicate-key checks remain. Both original real Windows bindings pass without modifying their bytes; simulated handle-only and path-only timestamp changes remain rejected.

The final host module passed **102 tests, zero skipped**, plus Linux Ruff, format, mypy and Bandit. Windows passed 92 with 10 existing POSIX skips. This targeted run supersedes the host source of the earlier 99-test run; the 527-test source record remains historical and unchanged. The single check container was removed. Full installation/lifecycle must still be demonstrated independently.
