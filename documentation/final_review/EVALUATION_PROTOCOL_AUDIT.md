# Evaluation protocol and submission audit

This audit checks the original capstone brief and the three supplied screenshots (evaluation protocol, submission requirements and eight viva questions), together with the [eleven minimum-bar rows](FINAL_REVIEW_EVIDENCE_MAP.md). It is a claim audit, not approval by the course assessor. Evidence includes S40, the actual human form and the later actual controlled live demonstration. No final completion-matrix result has been inspected here.

## Evaluation protocol

| Protocol requirement | Current evidence | Gap / feasible next action |
|---|---|---|
| At least 60 golden cases; expected outcome written before execution | [Frozen 60-case design](FINAL_CASE_DESIGN.md): 20 development, 40 held out; financial expected values authored before execution | Thirty risk cases lack complete semantic expected outcomes; the separate ten-company-year human risk-change requirement remains unmet. Freeze their qualitative expected outcomes before the corresponding evaluation; the 24 support labels are a different task. Do not claim 60 fully adjudicated gold cases. |
| Related variants stay together; approximately 20 development and ≥40 held out | Company-disjoint grouping and frozen manifest, 15 companies / 45 company-years | Preserve the split and log any exposure or later changes. If tuned after seeing held-out behavior, relabel affected cases as development. |
| Held-out folder inaccessible to agents | Evaluator-private expectations never passed into adapter/model messages; agent has no filesystem/code tool | This is application separation, not an operating-system/container permission boundary. Verify and document runtime access; use stronger isolation before claiming literal folder inaccessibility. |
| Code first: DB, amounts, citations, tools and forbidden actions | Production receipts, numerical validation, role/tool scope, source hashes, local portal approval gates and isolated computations | Citation existence is not support; S32 proves the distinction. Keep actual state/actions separate from qualitative judgments. |
| Judge only qualitative aspects; ≥20 hand-labeled outputs and kappa | 24 actual personally attested support labels; saved historical Llama comparison on all 24 | The brief's hand-label count is met: 24. Historical three-class κ=−0.014925 on all 24; binary κ=−0.064516 on 18 nonuncertain pairs. The project's separate operational gate (20 definite pairs and κ≥0.60) remains unmet, so automated grading is ineligible. Preserve uncertainties; additional independently chosen human examples may extend a future calibration, but existing choices must not be changed to qualify. A new prompt alone cannot increase this fixed set's 18 binary labels. |
| E1 C0 fixed/no-agent-decisions, C1 one agent, C2 team with same model/tools/budget | [Strict S43 sanity](S43_ACTUAL_DEVELOPMENT_RESULTS.md): six actual development runs, three complete / two blocked / one agent_step_limit; all arms completed numeric F002 | Strict C0/C1 use no separate critic; risk F003 remains unresolved in all arms. These two development cases and one repeat do not establish complete E1. [Historical S40](PILOT40_RESULTS.md) used a shared critic and remains a separate, narrower comparison. Final held-out results are pending. |
| E2 system defenses off/on on attacks and clean cases | S31 actual failure, S32 two-case replay, S33 narrow guard and software regressions | Planned 120 paired jobs are a **researcher/writer source-stage assay**, not whole-system defense removal. Only source quarantine is toggled; publication/private-memory/sandbox controls remain. Report stage attack/false-block denominators and qualification. Add separate end-to-end system safety evidence if claiming full protocol coverage. |
| E3 unseen before/after playbook, generation cost, harmful lessons | Three exact human approvals, one rejection with exact reason; conditional lesson code | Runtime manifest/freeze is verified by the S44 scope (e3_status=ready); held-out before/after application and results remain unrun. Development AI lesson-generation cost is not fully metered; do not call unmeasured effort zero. Predeclare harms and retain regressions/costs. |
| E4 signature ablation | Critic-on/off and fixed-BM25 table-aware/plain-text configurations prepared | Neither inspected final repeated result exists. BM25 representation ablation is not the production hybrid-reranker stack; state the controlled scope. |
| Config-driven single harness, not separate branches | Common adapter dispatches architecture/critic/memory/representation configuration | Record exact source/config/lesson/corpus hashes per execution; preserve legacy S40 semantics when comparing with later corrected configurations. |
| Every config: success, violations, attack success, false blocks, cost, median/p95, errors | S40 retains 6 attempts, statuses, costs, timings, cache counts and zero tool errors | Semantic success is unresolved for blocked risk outputs; irrelevant rates need explicit N/A with scope, not fabricated zero. Future stage rates cannot stand in for whole-system rates. |
| Include failures/timeouts and three final repeats with spread | S40 includes all three risk blocks and earlier preflight stops; one-repeat pilot | Planned final matrix has three repeats but no inspected results. Include attempted, completed, failed, timed-out, stopped and unrun counts; do not silently shrink denominators. |
| No tuning after held-out without disclosure | Development-only failures informed S33 and procedural lessons | Maintain immutable run/source histories; after any held-out inspection freeze further tuning or reclassify cases. |

## Analyst Desk-specific obligations

| Obligation | Current bounded evidence | Remaining work |
|---|---|---|
| 60 numeric/XBRL, comparison and risk questions | Frozen mix: 15 annual-series, 15 peer, 30 risk | Risk-change human labels for ten company-years remain absent from this packet. Source-support labels are not interchangeable. |
| External FinanceBench retrieval check | None inspected | Use the permitted open sample, record exact subset/labels/model/corpus scope and report results or explicit inability. No fabricated external score. |
| Signature: numeric cited comparison and critic correction | S40 numeric case succeeds across arms; production numeric controls and saved critic checks | Record a planted wrong-number example showing detection/correction; numeric guard tests or a clean answer alone are not that live demo. |
| Injection kept out of a note | S31/S32 preserve an actual accepted contamination; S33 controls are implemented | Record the corrected controlled example and final assay; never erase the original failure. Source-stage success does not prove safe publication. |
| Memory “what changed since last note?” | S37 same-note replay: off 131 characters, on 5,549 and 18 citations | Semantic risk-change answer and real-model memory comparison are not demonstrated by replay. |
| Monitoring verified share, critic outcomes, cost, EDGAR and alerts | S26 saved snapshot + real synthetic alert firing/clearing | 100% covers 12 table values only. EDGAR cache-hit telemetry is unavailable in that snapshot; do not imply complete live model-quality monitoring. |
| Actual user need and measured outcome | Operator used development workflow and control demo | No independent analyst user study or measured user time benefit. Predeclare supervised tasks, users, success criteria and time/cost before an impact claim. |
| Contributions and independent roles | Operator actions and AI assistance disclosed | No invented multi-person team or independent label/attack authors. Acknowledge shared-development/AI-assisted authorship and use independent adjudication where required. |

## Submission and demonstrations

The [submission checklist](SUBMISSION_CHECKLIST.md) covers the root README, diagram, numbered walkthrough, two one-page policies, six-page report, logs, ten slides and backup recording. The [viva guide](VIVA_WALKTHROUGH.md) answers all eight questions using actual IDs and paths.

The actual user-run [live control report](evidence/live_demo/Live_Control_Demo_Report.json), completed at 14:49:22 UTC, now demonstrates a production LangGraph checkpoint reopened by a new process and an explicit operator rejection, plus actual operator approval/edit in an isolated loopback portal followed by two stale-capability rejections. It uses scripted model content and no external model calls. It is control-flow evidence, not a financial-research success. L8 resume and L14 edit invalidation therefore have executed demonstration evidence. **The backup video remains missing.**

Actual clean installation on a fresh GitHub runner is now verified at the linked exact commit using synthetic fixtures. The separate hosted controlled negative CI run now visibly failed with exit code 20 and a no-performance-claim explanation. Its numeric gate was skipped; its exact commit is not visible. The root README is a staged proposal until installed safely against the actual user's root file. Overall full compliance is not yet established.

The later [S42 preparation and UI record](S42_PREPARATION_AND_UI_DEMO.md) distinguishes corrected baseline software checks from unreceived final measurements. The user reports completing the browser recording. The media file and a final publication receipt still need to accompany the submission; the current screenshots establish note review and 12/12 numeric verification.

[Source detector component audit](SOURCE_DETECTOR_COMPONENT_AUDIT.md): 35/40 known attack passages and 0/20 clean passages rejected, with zero model calls. These component rates do not establish model attack-success or whole-system false-block rates; those remain unmeasured.

### Actual S42 preview update

The [S42 user result](S42_PREPARATION_AND_UI_DEMO.md) verifies installation after 540 offline checks, 162 source pins, 30 compatible JSON inputs with zero mismatches and two exact fiscal-year HTML files. Human review was ingested; E3 preparation is ready. The run ended `preview_only`, with no new paid calls or final held-out results. It preserved the 83-call ledger (USD 0.09438074 reported / 0.69963306 reserved). The new scope still awaited approval. This archive is separate from the missing UI demo export/video and hosted CI evidence.

### Actual S43 and S44 correction update

[Actual S43](S43_ACTUAL_DEVELOPMENT_RESULTS.md) recorded 24 development attempts across 20 cases: 11 completed, 11 blocked and two errors. It stopped before held-out execution at local-budget readiness. Cumulative accounting: 368 settled calls, USD 0.60079594 reported / 3.63497986 reserved; USD 0.50641520 is incremental over the historical pilot.

[S44](S44_LOCAL_BUDGET_CORRECTION.md) now implements and tests the narrow harness correction: retain the F020 failed outcome/cost and every original attempt while allowing unrelated cases after verified local exhaustion. Original approved scope, caps and clock remain unchanged. Validation is AI software review, not a human approval or actual continuation result. Final held-out comparisons remain pending.

### Actual S44 accounting stop

[Actual S44 evidence](S44_ACTUAL_CALIBRATION_STOP.md): the local-budget fix worked and all 24 development results remained unchanged. After 19 successful revised support judgments, H20 failed with `model_http_error` and unknown cost. The ledger stopped: 388 cumulative requests, USD 0.60246756 **known** cost / 3.73199355 reserved / one unknown cost. No held-out model call occurred; matrix 839 unrun + one pre-model accounting error, memory 11 unrun + one such error, all 120 security jobs unrun. `evaluations_recorded` means reports saved, not experiments completed. Partial κ: 0.303665/19 pairs; binary 0.470588/15 pairs, ineligible. No research latency or three-repeat effect can be inferred.

### Bounded final-status correction

The [actual S44 scope](evidence/sequence44_actual/Playbook_Freeze_And_PR_Status.json) verifies the frozen lesson manifest (`bcd4b0bd…44ce9d`) and `e3_status=ready`; only held-out application/performance remains unrun. The latest user screenshot shows the `feature/final-evaluation-evidence` pull request merged and closed. No final CI check result or documentation-package installation has been verified.

### Verified fresh-runner installation

[Actual GitHub clean installation](ACTUAL_CLEAN_INSTALLATION.md) now passed at commit `3b1b96a72ca78dd3cd9296517fbd27fb9e1a6cbd`: 12 containers running, nine HTTP 200 endpoints, protocol/UI/auth/SSE checks and all 11 commands passed; unique-project cleanup completed. Synthetic fixtures, zero LLM calls and no executed research workflow. L16 clean installation is evidenced. The controlled failed CI demonstration is now observed with exit code 20 and no performance claim; documentation-package installation remains unverified.

### Hosted failed-CI evidence received

[Controlled negative CI job](ACTUAL_CONTROLLED_NEGATIVE_CI.md) failed with exit 20, `controlled_negative_failure`, thresholds null and `performance_claim=false`; verifier/availability steps passed, actual numeric gate skipped. This supplies the failed-CI demonstration and explanation, not a measured research regression. Its exact commit is not visible. Both L16 evidence items are now covered in the bounded fresh-install/controlled-failure scopes.
