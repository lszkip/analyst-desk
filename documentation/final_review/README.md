# Analyst Desk final-review evidence package

Start the existing prepared project with:

```bash
python start_analyst_desk.py
```

This package consolidates inspected evidence and draft submission material. It does not replace the frozen S40 source or assert that every requirement is complete. Use the [exact 11-row requirements map](FINAL_REVIEW_EVIDENCE_MAP.md) first: each row links to concrete evidence and states missing proof. The source catalog, code references and original records have [recorded hashes](evidence/Evidence_Manifest.json). The [generated catalog/test binding](evidence/Tool_Catalogue_Generation.json) and [explicit execution redaction](evidence/pilot40/Redaction_Record.json) are tracked separately. Historical publication reviewer fields have [explicit privacy redactions](evidence/workflow/Reviewer_Redaction_Record.json); original source hashes are preserved.

| Material | Purpose |
|---|---|
| [Sequence 42 and Research UI evidence](S42_PREPARATION_AND_UI_DEMO.md) | Corrected E1 preparation and screenshot-supported browser run; final comparisons and publication receipt remain pending |
| [Protocol audit and submission checklist](EVALUATION_PROTOCOL_AUDIT.md) / [checklist](SUBMISSION_CHECKLIST.md) | Full protocol and deliverable obligations, including remaining gaps |
| [Eight-question viva guide](VIVA_WALKTHROUGH.md) | Actual request, decision ownership, failed-tool fixture and injection evidence |
| [Actual control demonstration](evidence/live_demo/Live_Control_Demo_Report.json) | User-run checkpoint resume and stale-approval rejection; backup video still missing |
| [Root README proposal](ROOT_README_PROPOSAL.md) / [safe install plan](ROOT_README_INSTALL_PLAN.md) | Preserve any unknown existing root README |
| [Tool schemas and tests](TOOL_SCHEMAS_AND_TEST_MAP.md) | Actual 16-tool schemas, per-tool test mapping, 14 freshly executed contract tests |
| [Architecture and resume guide](ARCHITECTURE_AND_RESUME_GUIDE.md) | Source-derived graph, numbered flow and live checkpoint demonstration checklist |
| [Replan trace](PLANNING_REPLAN_EVIDENCE.md) | Exact saved real-model replan events with original/excerpt hashes |
| [Memory policy](MEMORY_POLICY_ONE_PAGE.pdf) | One rendered page with retention/privacy rules and actual offline comparison scope |
| [Threat model](THREAT_MODEL_ONE_PAGE.pdf) | One rendered page with tested boundaries, concrete failure and known bypass/false-block limits |
| [Human review and reviewed lessons](HUMAN_REVIEW_AND_PLAYBOOK.md) | 24 personally attested labels, three approvals, exact rejected proposal/reason and failed historical judge eligibility |
| [Actual S40 pilot](PILOT40_RESULTS.md) | Six development executions, all blocked outcomes, costs and poor cross-model agreement |
| [Frozen case design](FINAL_CASE_DESIGN.md) | 60 cases: 20 development / 40 held out; split and authorship limits |
| [Evaluation report, six-page PDF](Analyst_Desk_Evaluation_Report_DRAFT.pdf) / [source](FINAL_EVALUATION_REPORT.md) | Actual S40 pilot included; remaining final proof gaps explicit |
| [Ten-slide review deck](output/Analyst_Desk_Final_Review_DRAFT.pptx) | Editable presentation with evidence notes and current limitations |
| [Experiment log](EXPERIMENT_LOG.md) | Preserved changes, failures and scope distinctions |
| [Contribution log](CONTRIBUTION_LOG.md) | Operator and disclosed AI assistance |
| [Demo script](DEMO_RECORDING_SCRIPT.md) | Preparation for recording; not the recording itself |

S37 proves startup on the user's prepared laptop, not a clean installation. S40 ran real model calls, completed the numeric task in all three arms and blocked the risk task in all three arms. The pilot uses one repeat on two development cases; it is not a held-out benchmark. Claude/Llama agreement is low and cannot authorize automated semantic scoring. The validated human form now supplies 24 support labels and four lesson decisions. Historical Llama–human agreement is ineligible, with only 18 nonuncertain pairs; S44’s revised judge stopped after 19 responses and remains ineligible; no held-out research executed.

Credentials, private watchlists, live databases and hidden numeric gold answers are not included. The exact frozen source baseline is represented by copied code references and hashes; executing synthetic tool tests created only disposable test state. Root-owned completion work and final delivery will add newly inspected evidence without rewriting earlier failures.

### Actual S42 preview update

The [S42 user result](S42_PREPARATION_AND_UI_DEMO.md) verifies installation after 540 offline checks, 162 source pins, 30 compatible JSON inputs with zero mismatches and two exact fiscal-year HTML files. Human review was ingested; E3 preparation is ready. The run ended `preview_only`, with no new paid calls or final held-out results. It preserved the 83-call ledger (USD 0.09438074 reported / 0.69963306 reserved). The new scope still awaited approval. This archive is separate from the missing UI demo export/video and hosted CI evidence.

Latest release preparation: [Sequence 43 scheduling and safe handoff](S43_SCHEDULING_PREPARATION.md). Same 840 held-out jobs; 36 installer tests and two 547-check synthetic runs passed. The later S43 development results are recorded below; held-out results remain pending.

### Actual S43 development update

[Actual S43 results](S43_ACTUAL_DEVELOPMENT_RESULTS.md): 24 new attempts across 20 development cases, 11 completed / 11 blocked / 2 errors. Execution stopped at local workflow budget readiness before held-out work. Incremental 285 calls / USD 0.50641520; cumulative 368 calls / USD 0.60079594 reported / USD 3.63497986 reserved, all settled. Strict C0/C1 sanity is separate from historical S40; full E1 and final three-repeat results remain pending.

Latest software correction: [S44 local-budget continuation](S44_LOCAL_BUDGET_CORRECTION.md). It retains all actual S43 failures/costs and the original scope, caps and clock. Validation: 49 focused, 37 installer, two 555-check packaged runs, independent 18-check transition. These are AI software checks; actual user continuation and held-out results remain pending.

### Actual S44 accounting stop

[Actual S44 evidence](S44_ACTUAL_CALIBRATION_STOP.md): the local-budget fix worked and all 24 development results remained unchanged. After 19 successful revised support judgments, H20 failed with `model_http_error` and unknown cost. The ledger stopped: 388 cumulative requests, USD 0.60246756 **known** cost / 3.73199355 reserved / one unknown cost. No held-out model call occurred; matrix 839 unrun + one pre-model accounting error, memory 11 unrun + one such error, all 120 security jobs unrun. `evaluations_recorded` means reports saved, not experiments completed. Partial κ: 0.303665/19 pairs; binary 0.470588/15 pairs, ineligible. No research latency or three-repeat effect can be inferred.

### Bounded final-status correction

The [actual S44 scope](evidence/sequence44_actual/Playbook_Freeze_And_PR_Status.json) verifies the frozen lesson manifest (`bcd4b0bd…44ce9d`) and `e3_status=ready`; only held-out application/performance remains unrun. The latest user screenshot shows the `feature/final-evaluation-evidence` pull request merged and closed. No final CI check result or documentation-package installation has been verified.

### Verified fresh-runner installation

[Actual GitHub clean installation](ACTUAL_CLEAN_INSTALLATION.md) now passed at commit `3b1b96a72ca78dd3cd9296517fbd27fb9e1a6cbd`: 12 containers running, nine HTTP 200 endpoints, protocol/UI/auth/SSE checks and all 11 commands passed; unique-project cleanup completed. Synthetic fixtures, zero LLM calls and no executed research workflow. L16 clean installation is evidenced. The controlled failed CI demonstration is now observed with exit code 20 and no performance claim; documentation-package installation remains unverified.

### Hosted failed-CI evidence received

[Controlled negative CI job](ACTUAL_CONTROLLED_NEGATIVE_CI.md) failed with exit 20, `controlled_negative_failure`, thresholds null and `performance_claim=false`; verifier/availability steps passed, actual numeric gate skipped. This supplies the failed-CI demonstration and explanation, not a measured research regression. Its exact commit is not visible. Both L16 evidence items are now covered in the bounded fresh-install/controlled-failure scopes.
