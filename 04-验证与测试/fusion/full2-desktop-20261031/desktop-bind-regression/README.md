# Docker Desktop source-share regression

A real full/2 startup reached healthy application services, then rejected the Word peer before any conversion. Docker Desktop had translated a read-only project source bind to `/run/desktop/mnt/host/c/...`; the existing daemon-directory guard treated it as an unsafe runtime bind.

The fix accepts that standard drive-share prefix only for the six prescribed read-only `/workspace` code/configuration targets. Runtime directories, sockets, named pipes, writable shares, unexpected destinations and mailbox shadow mounts remain rejected. The added positive case and seven rejection cases run alongside the original host tests: **99 passed, zero skipped** on Linux. Ruff, format, mypy and Bandit passed. Source identities and precise resource cleanup are in the attached receipts.

This targeted rerun supersedes only the host file's earlier 91-test source identity; it does not rewrite the separate 527-test execution or claim that a new full installation passed. The optional legacy soffice importer test received the same PDFium5 API spelling update, with its original assertions and opt-in gate retained; only static checks were performed for that test.
