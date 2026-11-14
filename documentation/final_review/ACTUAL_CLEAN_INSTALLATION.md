# Actual fresh-runner installation

The [GitHub Actions clean-install run](https://github.com/lszkip/analyst-desk/actions/runs/43624655205) **passed** at commit `3b1b96a72ca78dd3cd9296517fbd27fb9e1a6cbd`. It started on a fresh GitHub-hosted runner and built the production stack using invented MSFT/GOOGL source fixtures for four company-years. This is actual installation evidence, with no live research-quality claim.

- All 12 containers were running. Eleven had Docker `healthy` status; Qdrant has no Docker healthcheck but its authenticated collection bootstrap passed.
- Nine HTTP health endpoints returned 200.
- All 11 commands returned exit code zero, including model preparation, index build/verification, stack startup, protocol tests and cleanup.
- EDGAR, filing KB, sandbox and portal checks passed. Unapproved and read-only publication attempts were rejected.
- The UI page, authenticated API and SSE idle/status checks passed; unauthenticated API access was blocked.
- No research workflow executed; recorded workflow model and tool calls were zero. No authorized publication was requested.
- Cleanup removed only the unique CI project and its volumes.

The launcher reported **126.98 seconds**. The separately displayed GitHub job total was 2m55s; these timings measure different scopes.

[Sanitized report and command summaries](evidence/deployment_actual/Clean_Installation_Public_Report.json) retain the run URL, commit, checks and provenance. Raw command logs and runner-local paths are omitted. Original archive SHA-256: `2fc7509534c685ce1429c125c4bf3e6c197303e81cc0a8fe06ddf0aee602670e`; original report SHA-256: `e5aea4721fe24bca47a07a3833946ecc8ad511121cb0d901d0da76282db88969`.

L16's clean-install evidence is now available. The [actual controlled failed CI run](ACTUAL_CONTROLLED_NEGATIVE_CI.md) is now observed with exit code 20 and an explicit no-performance-claim explanation. This green installation does not establish held-out agent performance, semantic safety, paid model reliability or installation of the documentation package on the user's machine.
