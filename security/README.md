# Analyst Desk security regression checks

This development suite tests deterministic security controls using synthetic
filing text, scripted agent responses, fake credentials and temporary databases.
It makes no external model/API calls and does not publish to the research portal.
Simulated approval in positive-control tests occurs only in a disposable portal.

Run from the repository root using the existing Python 3.12 environment:

```bash
python security/run_checks.py
```

The environment needs the existing `requirements-workflow.txt` dependencies.
The operator runner and helper do not install dependencies automatically.
Linux with the existing sandbox prerequisites is required for real subprocess
and kernel-policy tests; unavailable isolation is a failure, not a skipped pass.

The helper copies an explicit source inventory into a disposable workspace,
uses a scrubbed environment and fake home directory, and prevents accidental
Python network connections before importing application modules. It runs:

- Existing research-workflow and portal regressions, including exact-content
  approval, edited or changed evidence, tool permissions and budget limits.
- Existing real sandbox subprocess tests for network/file/process restrictions,
  resource limits, environment isolation and successful calculations.
- New security tests for injected source followed by scripted forbidden actions,
  argument smuggling, company/year scope, source-response identity, credentials,
  endpoint validation, safe error handling and approval-capability replay.
- Positive controls for allowed retrieval, valid credentials, ordinary business
  prose and exact-content simulated publication in a temporary database.

Results include executed test methods, subtest counts where present, failures,
skips and source hashes. A subtest variant is not a separate independent attack
or a held-out case. Raw exception text, credential values and synthetic database
contents are excluded from the public report. A nonzero exit means a failure or
incomplete check. GitHub Actions runs this same helper on pushes and pull requests.

## Corrections covered by this change

The existing literal advice filter named only a few tickers. Instructions such
as `Buy META shares.` and `Buy NFLX shares.` passed that layer. The corrected
filter also covers explicit stock/share instructions naming companies and
aliases in the prepared registry. It remains a limited literal filter.

Passage replies are now bound to the requested identity/section before they
enter the evidence registry. A plausible company/year alone is insufficient
when a service returns a different passage or filing section.

Transport construction is included in the same sanitised exception boundary as
the MCP request, so setup failures cannot escape as raw diagnostic strings.

## Operator delivery and scope

Sequence 29 uses the stable `Run_Workflow_Checks.py` filename. It verifies known
source hashes, tests corrected copies first, and installs the reviewed changes
only after those checks pass. Every invocation creates a fresh
`29_Analyst_Desk_Security_Results_<timestamp>_<random>.zip`. Existing source files
that change are backed up in that run's private output folder.

The runner does not open original research databases, saved run folders,
monitoring runtime data, or `.env`. It does not restart containers, change Git
state or consume the research model budget. No approval prompt is needed for
these local tests; the application's publication approval gate remains intact.

Passing demonstrates the tested code boundaries. It does not measure whether a
real model follows an injected instruction, a general advice-detection rate,
private-watchlist protection for an unimplemented memory feature, deployed HTTP
security or a held-out attack-success/false-block rate. The cases are development
regressions authored with AI assistance, not the capstone's final held-out
red-team benchmark. See `THREAT_MODEL.md` for the boundaries and remaining work.
