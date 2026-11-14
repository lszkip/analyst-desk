# Analyst Desk experiment log

This log records inspected artifacts, not a reconstructed history of unobserved work. Sequence numbers identify work packages; they are not sample counts. Research measurements are development evidence. The current cut includes the actual S40 user run, completed 14 November 2026 at 13:43:30 UTC: six research attempts, three completed numeric tasks and three blocked risk tasks, followed by a separate AI judge. A later validated human form supplies 24 support labels and four lesson decisions; held-out execution remains absent. Earlier preflight stops are preserved below as historical events.

| Sequence or record | Question or change | Observed result | Interpretation and retained limit |
|---|---|---|---|
| S12 → S13 | Does equal-weight fusion of hybrid and cross-encoder orders improve retrieval? Same 40 queries; `rrf_k=60`. | Recall@5 0.369308 → 0.388981; nDCG@5 0.788462 → 0.812511; precision@5 0.650 → 0.640. Seven nDCG improvements, eight regressions, 25 ties. | AI-reviewed development labels. Aggregate gain with regressions; not held-out or human-adjudicated. |
| Corpus replay, `34.2.4 IndexVerifyOut.txt` | Is the prepared corpus/index inventory reproducible? | 15 companies, FY2023–2025, 45 company-years, 16,235 passages, 5,334 tables and 24,607 search windows; 239 passages unassigned. | Inventory/replay verification, not complete semantic Item assignment. |
| `41.12 WorkflowReplanReviewOut.txt` | Can evidence limitations trigger a new plan? | Journal events 66–69 record contradiction/limitation, supervisor response and `replans=1`. | Actual development trace with operator involvement. |
| S22–S25 | Can saved work be continued, corrected and explicitly published? | One Google/Meta note published to localhost mock portal; two identical reads; 12 financial table values verified. Logical-run totals: 31 model requests, 133,710 tokens, US$0.0634176 reported cost. | AI-assisted recovery. Publication used zero new model calls; not fresh autonomous end-to-end or held-out success. |
| S26 | Do saved-run monitoring and the alert route work? | Real datasource/dashboard checks and 64 matched metric samples. Separate synthetic low-verification probe fired then cleared. | Research values are an immutable S25 snapshot. Synthetic alert did not alter actual research data. |
| S28 | Are monitoring configuration checks reproducible? | Local Python and `promtool` checks passed. | Local checks do not establish a GitHub Actions result. |
| S29 | Do isolated transport, tool and publication boundaries hold in tested cases? | Saved security checks exercise authentication, capability and sandbox boundaries. | Software boundary tests, not natural-language task quality or a general attack success rate. |
| S30 | Does the first live injection harness exercise the intended workflow? | Writer results were blocked by numeric checks, including legitimate fiscal-year text. Audit found that the harness bypassed actual effective writer prompts/schema. | Preserved as a base-prompt-stage experiment. Its difference from S31 is a harness change, not defenses off/on. |
| S31 | What happens through the actual writer/researcher methods? | All 24 writer outputs accepted; 16 attack cases and eight clean controls. One confirmed accepted invented renewal guarantee; 15 other writer attack goals not observed. | Independent AI review also found unsupported elaboration/coverage issues. Node acceptance is not factual correctness; no end-to-end publication claim. |
| S32 | Does the actual critic reject the saved clean/bad draft pair? | Both drafts approved. The bad draft asserted a contractual renewal guarantee; the critic had the complete exact source and still approved it. | Two-case diagnostic, not a population estimate. Risk-only financial-completeness fixture; no publication. |
| S33 | Can recognized source-to-agent commands be prevented from serving as support? | Narrow full-source quarantine added to support/render/approval/publication boundaries. Laptop security suite: 236 tests passed, zero model calls. | Reproduces the saved defect and wrapper cases offline. Known false positives and unrecognized instruction forms remain; test count is not a defense rate. |
| S34 implementation | Add UI, owner memory, lesson governance and final evaluation machinery. | UI validation records 28 passing tests, JavaScript syntax pass and loopback HTTP/SSE smoke with synthetic controller. Memory/lesson tests check their deterministic behavior. | At the S34 evidence cut, target Compose was unverified; actual S37 startup is reported below. At that cut, fresh live research was unverified; S40 later ran the development pilot. Browser screenshot execution was blocked in the review environment. |
| S34 historical memory replay | Does memory restore the same archived note and source inventory? | Off: zero notes, 131 context characters. On: one note, 18 citations, 5,549 characters under the 6,000-character bound. Zero model/network calls. | Historical binding verified; current sources not reverified. No semantic risk-change or live-model quality claim. Synthetic private literals blocked in both modes. |
| S35 review packet | Human review simulation — performed by Claude. | Validated 24 exact saved outputs: 10 supported, 13 unsupported, one uncertain. Four lesson recommendations: three keep, one reject. | AI-only; exact runtime model identity unknown. No human decisions were supplied by that AI simulation; later S40 agreement and the separately submitted human form are reported below. |
| Final comparison packages, including S36–S40 | Architecture, defenses, memory/lessons, critic and representation comparisons. | S40 produced the six development attempts below; other final comparisons remain pending. | Configurations, expected outcomes and offline tests are preparation, not measured results. |

## Frozen inputs and reporting rules

The preserved S34 review packet hash is `e7b5be45054d73e32da3b45069dc4b12f09eb83954e906ef01d8ce68c129c2c1`. The S35 AI-review packet hash is `9ad0b0a9658c35fb1679c1602cb869dc2dfffe1e31e43d76a7f4ecd3a0801d0d`; all 24 item objects and four proposal objects are unchanged. Review-purpose metadata differs. No simulated decision is imported as human approval.

For new comparisons, retain the exact corpus/configuration/playbook hashes, model identifier returned by the provider, all scheduled attempts, technical failures, timeouts, skipped rows, measured usage and unrun rows. Report repeated executions independently; do not manufacture three repeats from a saved response. Preserve task gold outside model-visible inputs. Once results influence tuning, reclassify those cases as development.

See [the evidence register](FINAL_REVIEW_EVIDENCE_MAP.md) for archive/member references and [the evaluation report](FINAL_EVALUATION_REPORT.md) for metric interpretation.

## Staged additions after S35, through the S39 user report

| Sequence or record | Question or change | Observed result | Interpretation and retained limit |
|---|---|---|---|
| Frozen final design | Are research cases authored and split before execution? | 60 cases frozen: 20 development and 40 company-disjoint held out. Related primary-company variants and all peers stay in one split. | AI-assisted authorship; no independent human verification. Risk labels remain pending. See `FINAL_CASE_DESIGN.md`. |
| S37 prepared-laptop startup | Does the complete local stack start after the EDGAR connection fix? | Nine checked service endpoints returned HTTP 200; Docker server 29.9.3; startup completed 13:02:18 UTC. Zero model calls and no volume deletion. | Actual existing-laptop deployment. Not a clean install, completed research workflow or GitHub CI result. |
| S37 offline checks | Do installed implementation checks pass on the user's laptop? | 236 legacy security + 164 final-integration tests passed (400 total), no failures/errors/skips. | Software checks using synthetic models/backends; no research-quality percentage. |
| S37 historical memory | Does memory expose the same archived approved note when enabled? | Off: zero notes, 131 context characters. On: one note, 18 citations, 5,549 characters. Zero model/network calls. | Same historical citation inventory; retained historical-only verification. No current risk-change or LLM-quality inference. |
| S37 pilot preflight | Do current SEC cache bytes match the frozen pilot? | Stopped with `frozen_source_mismatch` before paid approval. No held-out run or publication. | Failed source preflight; zero paid model calls. Preserve this attempt rather than counting it as a model result. |
| S39 source-equivalence reconciliation | Are the four exact reviewed cache copies substantively the same frozen inputs? | Two root `prepared_at` differences and one JSON-serialization difference; one file already byte-identical. Five company-years, ten facts and five margins unchanged. | Original gold, cases, authoring code, evaluator and cache remain unchanged. Only exact reviewed variants can be bound. No model calls. |
| S39 offline package validation | Does the revised binding preserve strict evaluation and installation behavior? | 414 source checks (236 + 178), 25 installer tests; repeat smoke installed zero files. Wrong values, fact IDs and source hashes still rejected. | Assistant-side offline checks. The separate S39 user-run preflight is recorded in the following row; no paid pilot result. |
| S39 actual user source/backend preflight | Does the reviewed binding match the laptop sources and live tools? | 414 offline checks passed; exact source binding verified; ten MCP fact checks, seven scoped section/search checks and sandbox check passed. | Actual backend readiness. Preflight tool operations are excluded from task counts; no model-quality outcome. |
| S39 pricing gate | Are current exact-model prices verified before paid work? | Stopped at `price_verification_required` after backend preflight, before paid model calls. No held-out run or publication. | Failed price preflight remains visible. None of the six planned case-arm research attempts is counted as executed. |
| Historical S40 preparation at the S39 cut | Handle an omitted `request` pricing field in the public model catalogue. | At 13:26 UTC, implementation was in progress and no S40 user-run result existed yet. | Superseded by the actual 13:43 UTC S40 execution below; historical preparation is not counted as an execution. |

Primary additions: the S37 uploaded results archive (`Startup/Startup_Report.json`, `Operator/Operator_Audit.json`, `Operator/Memory_On_Off_Report.json` and both check reports), plus S39 `Source_Equivalence_Review.json`, `Independent_Source_Binding_Review.json`, `Independent_Package_Review.json`, `Packaged_Smoke_Result.json` and `Installer_Unit_Tests.log`.

The actual S39 user archive is `39_Analyst_Desk_Pilot_Source_Validation_Results_20261114T132437Z_700ce33f.zip`; its `Operator/Preflight_Report.json` and `Operator/Operator_Audit.json` distinguish successful backend checks from the subsequent failed pricing gate.

## Actual S40 execution (later evidence, 13:43 UTC)

S40 completed all six planned development case-arm research attempts and 24 separate judge calls. Each C0/C1/C2 arm completed the numeric-only case and blocked the risk case. Research cost was $0.0927376 across 59 calls; final actual cost including the judge was $0.09438074 across 83 calls. Conservative reservations were $0.69963306. No missing-cost, in-flight or poisoned ledger state was recorded. This supersedes the earlier staged S40 “in progress” status without removing the S37/S39 stops.

Claude/Llama three-class kappa was 0.053763 on 24 pairs; binary kappa was 0.049587 on 23 pairs. The report is ineligible for automated semantic scoring and does not supply human calibration. [Full pilot results and limits](PILOT40_RESULTS.md) retain all blocked outcomes. Held-out, three-repeat, lesson and fair attack/clean comparison results are still separate obligations.

## Actual human review received after S40

The export dated 14 November 2026 at 14:26:35 UTC validates against the original packet: 24 labels (12 supported, six unsupported, six uncertain), three lesson approvals and one rejection with an exact reason. Human authorship is based on the user-submitted personal attestation, not externally verified identity. The public package omits the name and full export. No choices were changed.

The saved historical S40 Llama responses were compared against these actual labels without new calls: three-class kappa −0.014925, 7/24 agreement; binary kappa −0.064516, 7/18 agreement. Eighteen nonuncertain pairs are below the strict twenty-pair gate, and agreement is below 0.60. Automated semantic-scoring eligibility remains false. At that earlier cut, revised judge, runtime freeze and final matrix were pending. The later S44 scope verifies the freeze; the revised judge is partial and no held-out research ran. [Exact reviewed proposals, rejection reason and receipts](HUMAN_REVIEW_AND_PLAYBOOK.md).

## Actual interactive control demonstration — 14 November 2026, 14:49 UTC

The user executed the isolated live control demo. Production checkpoint state resumed across different processes with identical checkpoint and plan hashes, no additional model response and an explicit operator rejection. The portal operator approved then edited a proposal; the original unexpired capability was rejected against both old and new hashes, leaving revision 2 pending and zero published notes. Zero external model calls, no production state accessed. Exact report and archive hashes are in the evidence manifest. This updates L8/L14 control evidence; a backup video remained pending at that cut. Later hosted clean-install and controlled-failure CI evidence are now recorded below.

## Sequence 42 preparation and browser demonstration

[Latest evidence](S42_PREPARATION_AND_UI_DEMO.md) records the corrected, tested strict E1 baseline configuration and the real F002 Research UI run. The operator corrected a model fiscal-period error with AI assistance, then reached note review with 12/12 displayed financial values verified. At that gate the UI showed 10 model calls, 27,965 tokens and USD 0.01458 cost, outside the evaluation ledger. The user reports recording completion; final publication receipt and video remain unreceived. No held-out result or automatic detection of the plan error is claimed.

[Source detector component audit](SOURCE_DETECTOR_COMPONENT_AUDIT.md): 35/40 known attack passages and 0/20 clean passages rejected, with zero model calls. These component rates do not establish model attack-success or whole-system false-block rates; those remain unmeasured.

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
