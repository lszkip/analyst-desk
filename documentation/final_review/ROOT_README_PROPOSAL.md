# Analyst Desk

Analyst Desk creates source-linked financial research notes from scoped company filings. It combines filing retrieval, verified financial facts, sandboxed calculations, a persistent research workflow and explicit review before publication.

From the repository root of the prepared project:

```bash
python start_analyst_desk.py
```

The startup launcher brings up the local application and reports service health. Follow `documentation/README_FINAL_REVIEW.md` for configuration and prerequisites, including the supported Python environment, Docker, local filing data and indexes, and provider/SEC access configuration. Never commit credentials. Prepared-laptop startup has been observed; a fresh-machine installation must be verified separately.

## Request and review flow

Submit a company/year question, inspect and approve or edit its proposed plan, and follow the scoped research/calculation/checking stages. Review the exact note before approval. Editing a note invalidates its prior approval. Resuming preserves the same run, evidence and cumulative budget; a new process cannot silently reconstruct a human publication capability.

## Review materials

The final-review guide at `documentation/README_FINAL_REVIEW.md` identifies the architecture, tool schemas, memory/threat policies, evaluation report, experiment log and contribution log. The final evidence bundle includes a numbered actual-request walkthrough, eight viva answers, a requirement-by-requirement audit and source-bound reports. Preserve its evidence-status qualifiers when installing those documents: development runs, software tests, human actions and held-out outcomes are distinct.

Current inspected evidence includes the S40 two-case architecture pilot, an actual human support-review form, historical monitoring/memory records, an interactive controlled resume/stale-approval demonstration, and a real Research UI development run to note review. Sequence42 corrected strict E1 baselines have passed software checks. Full repeated held-out experiments, eligible automated semantic grading and a backup video are not all established in the current evidence packet. The report records the exact cut and remaining gaps.

## Boundaries

Filing text is untrusted evidence, never authority to run commands or approve publication. Calculations and financial prose are checked by code; source identity alone does not prove semantic support. Custom source-instruction checks have documented false positives and bypasses. Memory is owner-scoped historical context, not current source verification. Outputs require analyst review and are not autonomous investment recommendations.

This project was developed with disclosed AI assistance. The contribution and experiment logs identify actual operator actions, model-assisted work, failed experiments and limitations.

### Actual S42 preview update

The [S42 user result](S42_PREPARATION_AND_UI_DEMO.md) verifies installation after 540 offline checks, 162 source pins, 30 compatible JSON inputs with zero mismatches and two exact fiscal-year HTML files. Human review was ingested; E3 preparation is ready. The run ended `preview_only`, with no new paid calls or final held-out results. It preserved the 83-call ledger (USD 0.09438074 reported / 0.69963306 reserved). The new scope still awaited approval. This archive is separate from the missing UI demo export/video and hosted CI evidence.

### Verified fresh-runner installation

[Actual GitHub clean installation](ACTUAL_CLEAN_INSTALLATION.md) now passed at commit `3b1b96a72ca78dd3cd9296517fbd27fb9e1a6cbd`: 12 containers running, nine HTTP 200 endpoints, protocol/UI/auth/SSE checks and all 11 commands passed; unique-project cleanup completed. Synthetic fixtures, zero LLM calls and no executed research workflow. L16 clean installation is evidenced. The controlled failed CI demonstration is now observed with exit code 20 and no performance claim; documentation-package installation remains unverified.
