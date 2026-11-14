# Analyst Desk evaluation report

**Draft — S40 research evidence through 14 November 2026 at 13:43:30 UTC, plus the human-review export, interactive control demonstration, the Sequence 42 user preview, actual Sequence 43 development and Sequence 44 accounting stops, and later Research UI screenshots.** This report distinguishes implementation checks, actual saved model behavior, AI judgments and human actions. Later completion work is excluded until its outputs are inspected. It reports development evidence and remaining requirements; it does not claim that the capstone is complete.

## 1. Objective and system

Analyst Desk supports cited comparison of company filings: retrieve scoped passages, obtain financial facts, compute ratios in an isolated sandbox, draft a note, check support and obtain explicit approval before publication. The prepared corpus contains 15 companies across FY2023–2025: 45 company-years, 16,235 passages, 5,334 tables and 24,607 search windows. The inventory also retains 239 unassigned passages; inventory verification is not a claim of complete semantic Item assignment.

A supervisor coordinates company researchers, deterministic financial computation, risk comparison, a writer and a critic. Sixteen MCP tools provide EDGAR facts/sections, filing search/tables, sandbox execution and local research-portal operations. Plans and notes have separate human gates. SQLite checkpoints and an append-only-style event journal preserve evidence, cumulative budget reservations and decision history. Editing a note invalidates its earlier approval; publication rechecks the exact proposal and current supporting records.

The implementation uses custom guards and a local event journal. Llama Guard, Prompt Guard and LangSmith are not deployed. A local UI and owner-scoped memory are implemented. S37 verifies startup on the user's prepared laptop; this is distinct from a clean-machine installation. Later UI screenshots document a real assisted development run to note review; the user reports recording it, but the video and final publication receipt have not been received.

## 2. Evaluation design and provenance

The retrieval study uses 40 AI-reviewed development questions with exhaustive relevant-passage sets. The same questions, corpus and evaluation definitions support the S12/S13 before/after comparison. AI-assisted labels are not independently human-adjudicated. The rank-five metrics count each relevant passage separately, including repeated substantive claims.

A separate final design freezes 60 cases before new agent execution: 20 development and 40 held out, with company-disjoint groups. The development set has five annual-series, five peer-comparison and ten risk cases; held-out has ten, ten and twenty respectively. Thirty risk cases lack complete semantic expected outcomes. The separate requirement for human risk-change annotations on ten company-years remains unmet. Requests are separated from private expected values; this separation is an evaluation design, not a claim of operating-system isolation from every possible attacker.

S40 executed two development cases across three architectures once. It did not execute the forty held-out cases or the configured three-repeat final comparison. Plan approval was explicitly simulated inside the evaluator and publication was disabled. The later validated form supplies twenty-four human support labels and four lesson decisions; judge eligibility and held-out playbook measurements remain unresolved. Claude's labeled simulation is kept under its explicit AI-only title. Saved failures remain evidence and are not replaced by the later fix.

## 3. Retrieval: measured gain and tradeoff

S13 changed ranking by equal-weight reciprocal-rank fusion of the existing hybrid order and cross-encoder order, using `rrf_k=60`. All forty questions remained in the denominator. The paired result is:

| Metric | S12 before | S13 after | Change |
|---|---:|---:|---:|
| Recall@5 | 0.369308 | 0.388981 | +0.019673 |
| nDCG@5 | 0.788462 | 0.812511 | +0.024048 |
| Precision@5 | 0.650 | 0.640 | −0.010 |
| Hit rate@5 | 0.975 | 0.975 | 0 |

Seven queries improved, eight regressed and twenty-five tied by nDCG@5. The aggregate gain therefore coexists with individual regressions and a small precision loss. It does not demonstrate improvement on unseen companies or independently labeled questions.

The separate within-S13 comparison uses the same forty queries for each retrieval mode:

| Mode | Recall@5 | nDCG@5 | Median seconds | p95 seconds |
|---|---:|---:|---:|---:|
| BM25 | 0.372198 | 0.712850 | 0.0508 | 0.0631 |
| Dense | 0.390303 | 0.792059 | 0.01595 | 0.0248 |
| Hybrid | 0.387091 | 0.799025 | 0.0639 | 0.0789 |
| Reranker fusion | 0.388981 | 0.812511 | 2.4816 | 3.7790 |

Every mode recorded zero query errors. Reranker fusion led nDCG while dense retrieval had slightly higher recall and much lower latency. Models and indexes were loaded before these query timings; startup is excluded. The paired change and within-run mode comparison answer different questions and are not interchangeable deltas.

Because many questions have more than five relevant passages, the exhaustive labels impose a macro Recall@5 ceiling of 0.453030. A former raw-recall target of 0.70 was unattainable under this definition and was retired; it was not an IIT-mandated threshold. This is a limitation of the selected metric and label scope, not permission to trim the gold set. The declared development nDCG gate is 0.70. Raw results and the original comparison remain available for review.

## 4. Workflow, tools, memory and monitoring

S22–S25 preserved and corrected one Google/Meta development run. S25 published one note to a localhost mock portal after explicit terminal approval, with two identical readbacks. Twelve financial table values were verified. Cumulative logical-run totals were 31 model requests, 133,710 provider-reported tokens and $0.0634176 reported cost. Publication itself used no new model calls. This was an AI-assisted continuation with operator corrections, not a fresh autonomous or held-out success.

A separate original journal contains exact events 66–69: evidence-limit/contradiction replan, supervisor model start and response, then a new plan with `replans=1`. The submission extracts those original lines and records original/excerpt hashes. That development run eventually blocked; the replan trace is not presented as a successful final answer. S40 also contains real C2 replans under evaluator-simulated plan approval.

The schema catalog is freshly generated from the actual frozen S40 MCP implementations. It contains all sixteen tools with descriptions, annotations, input schemas and output schemas. Fourteen selected existing contract tests passed: eleven operation test methods cover the sixteen tools, with shared portal/sandbox tests, plus three catalog checks. Tests use synthetic data, temporary stores and authenticated loopback HTTP. They establish tested contracts, not sixteen independent research-quality outcomes or live model accuracy.

The actual S37 memory comparison reuses the same archived S25 note. Memory off supplied no prior note and 131 context characters; memory on supplied one note, eighteen citations and 5,549 characters within a 6,000-character bound. Both used zero model/network calls. The historical publication binding was verified but sources were not currently reverified. The old critic limitation is retained. A synthetic private watchlist literal was blocked in both modes. This establishes bounded historical replay and privacy behavior, not semantic risk-change accuracy or a live-model quality gain. A historical `/notes/...` string is a reference convention, not a demonstrated UI route.

S26's Grafana audit matched 64 metric samples to the saved S25 snapshot. A separate synthetic low-verification probe fired and cleared the alert without changing research data. The dashboard's 100% means twelve of twelve code-rendered financial table values; it does not cover every narrative assertion. EDGAR cache-hit telemetry is unavailable in that snapshot. The later user-run controlled demo resumed the same checkpoint/plan in a new process with no additional scripted response. Actual approval then edit caused both old/new-hash stale-capability attempts to return `approval_required`; zero notes were published. Scripted content and zero external calls make this control evidence, not model quality. The user reports finishing a UI recording; the backup media file remains unreceived.

A later browser run of the already-seen F002 development question reached note review with 10 calls, 27,965 tokens and USD 0.01458 displayed cost. The operator saved an AI-assisted correction after the model swapped the companies' fiscal year ends. The displayed four rows match the frozen reference; verification shows 12/12 financial values and no missing company/year pairs. Publication is not confirmed by the received screenshots. UI spending is separate from the evaluation ledger. [Screenshot evidence and limits](S42_PREPARATION_AND_UI_DEMO.md) are retained.

## 5. Security failures and response

S30 exposed a harness problem: it bypassed the actual writer's effective prompt and schema, and its numeric guard rejected legitimate fiscal-year text, including clean controls. The original results are preserved as a base-prompt-stage experiment. Comparing S30 with S31 would conflate a harness correction with a defense change, so it is not a valid defenses-off/on ablation.

S31 called the actual writer/researcher methods. All twenty-four writer outputs passed application checks: sixteen attack cases and eight clean controls. One accepted draft adopted a source instruction to invent a contractual renewal guarantee. Fifteen other writer attack goals were not observed, but independent AI review identified other unsupported elaboration or coverage issues. Application acceptance was therefore not factual correctness. No publication occurred in this node-level assay.

S32 replayed the exact saved clean and contaminated drafts through the actual critic with full original sources. The critic approved both, including the invented guarantee. Only risk-only financial-completeness handling was stubbed for this isolated fixture. Exact quotation and matching source hashes did not establish that the source asserted the quoted proposition as fact. This is a two-case diagnostic, not a population failure rate.

S33 added a narrow deterministic quarantine for recognized source-to-agent instructions at support, rendering, approval, preparation and publication boundaries. Originals remain unchanged, and stale approval cannot bypass the current check. The user's 236 security tests passed with zero model calls. A later [component-only scan](SOURCE_DETECTOR_COMPONENT_AUDIT.md) rejected 35/40 known development attack passages and 0/20 clean passages; disabling that detector rejected zero. These are source-classifier rates, not model attack-success or whole-task false-block rates. Researcher nodes do not apply this quarantine gate. Indirect, encoded, multilingual or unfamiliar commands and ordinary false facts remain limitations. A paired source-stage model assay and full-system E2 proof remain pending.

## 6. Human review, lessons and judge agreement

The earlier **Human review simulation — performed by Claude** labeled twenty-four saved synthetic development outputs: ten supported, thirteen unsupported and one uncertain. Its exact runtime identity was unverified. The saved S40 Llama-family judge labeled five supported and nineteen unsupported. Their three-class kappa was 0.053763 on twenty-four pairs; binary kappa was 0.049587 on twenty-three. That historical AI-only comparison remains unchanged and does not estimate production error rates.

A later user-submitted form now supplies **twenty-four personally attested labels: twelve supported, six unsupported and six uncertain**, plus three exact lesson approvals and one rejection with a reason. Packet, item, source, output and proposal hashes were validated. Human authorship rests on the explicit personal-review attestation; identity was not externally verified. The public package omits the name and full export. Every original choice, including uncertainty, is preserved.

Reusing the unchanged historical Llama responses against these actual human labels required no new model calls. Three-class agreement is 7/24 (0.291667), with kappa **−0.014925**. Binary agreement is 7/18 (0.388889), with kappa **−0.064516**. No pair is missing or failed. The brief's 20-output human-label count is met by 24 labels. The project's stricter operational gate needs 20 definite binary pairs and kappa at least 0.60; it fails on both counts (18 pairs and poor agreement). Automated semantic-scoring eligibility is false. S44 returned 19 revised support-judge responses before H20 failed: three-class κ=0.303665 on 19 pairs; binary κ=0.470588 on 15. This partial revision remains ineligible. H20–H24 have no usable response; historical and revised judgments stay separate.

The approved procedural lessons address source-instruction boundaries, unsupported business elaboration and sample/period scope. The human rejected the proposal that an exact quotation proves support even when the source calls the claim invented. The [reviewed playbook record](HUMAN_REVIEW_AND_PLAYBOOK.md) preserves the matching proposal hash, complete text and exact rejection reason. Receiving these decisions does not establish runtime playbook activation or held-out improvement; the actual S44 scope verifies a frozen runtime lesson manifest and e3_status=ready. Held-out application and before/after results remain unrun. Support calibration also does not validate attack-success or false-block classification.

## 7. Architecture: S40 and S43 development

Six real research jobs used the same configured task model (`openai/gpt-4.1-mini`, temperature zero), task tools and budgets. C0 fixes collection, C1 uses one worker and C2 a team; all retain a shared model critic/revisions and its cost. This S40 comparison is not literally a wholly non-agentic C0 or one-model-only C1 pipeline. Each arm completed the numeric-only case with numeric checks passing and blocked the risk-containing case after bounded revisions. Thus every arm has one completed and one blocked result. The blocked risk outputs remain unadjudicated and are not silently dropped or counted as correct; completion is not 50% semantic accuracy.

| Arm | Completed / blocked | Calls | Actual cost, USD | Median / p95 seconds |
|---|---:|---:|---:|---:|
| C0 fixed | 1 / 1 | 15 | 0.0263292 | 24.493940 / 40.949406 |
| C1 single | 1 / 1 | 20 | 0.0263528 | 23.320791 / 29.032641 |
| C2 team | 1 / 1 | 24 | 0.0400556 | 39.207851 / 49.541218 |

Only two timings contribute to each arm; p95 is descriptive, not a stable tail estimate. C0 recorded ten local response-cache hits, while C1 and C2 recorded none. This execution difference is retained. C2 costs more and takes longer here without a completion advantage, but two development cases and one repeat cannot establish a general architecture ranking. Both C2 jobs replanned; replanning alone is not a quality benefit.

Research consumed 59 requests and 208,996 provider-reported tokens, costing $0.0927376. The separate twenty-four-output judge added $0.00164314, bringing the final ledger to 83 requests and $0.09438074 actual cost. Conservative non-refundable reservations were $0.69963306 and are not actual expenditure. There are no missing actual costs, in-flight requests or poisoned ledger state. These are recorded values for this run, not a future price forecast. Publication and held-out execution remained disabled.

Sequence 42 subsequently corrected E1 before final evaluation: C0 uses deterministic collection and at most one tool-free writer response, C1 uses one researcher conversation, and neither invokes a model critic. C2 retains the team/critic. Shared model, tool backends and budget ceilings remain unchanged. The actual user installation passed 540 offline checks and verified 162 source pins. Its preview checked 30 SEC JSON files with zero mismatches plus two exact HTML files. Human review was ingested and E3 preparation was ready. Status remained preview_only, with no new paid calls or held-out measurements; S40 results above remain historical.

The actual S43 run then used the corrected baselines and recorded 24 new development attempts across 20 cases: **11 completed, 11 blocked and two errors**. Six C0/C1/C2 sanity attempts produced three completions, two blocks and one agent_step_limit; eighteen additional C2 attempts produced eight completions, nine blocks and one budget_exhausted. All three arms completed numeric F002. Risk F003 remained unresolved in every arm. C2 passed seven of ten numeric-only development tasks; all ten risk-task success labels remain unknown.

S43 stopped before held-out execution because C2 F020 hit its local 650,000-token reservation ceiling, which readiness classified as a runtime blocker. The global ledger remained healthy. New work used 285 calls and USD 0.50641520; cumulative totals are 368 calls, USD 0.60079594 reported and USD 3.63497986 reserved, all settled. [Actual phase metrics and failure](S43_ACTUAL_DEVELOPMENT_RESULTS.md) retain every outcome. This is development evidence, not a complete E1 comparison or held-out result.

Actual S44 passed the local-budget transition, preserving all prior outcomes and limits. Nineteen revised judge responses succeeded; H20 then failed with model_http_error and missing cost, stopping the ledger. The 840-job matrix produced 839 unrun records and one immediate accounting error; memory produced 11 unrun and one such error; all 120 security jobs stayed unrun. No held-out model call occurred. Cumulative known cost is USD 0.60246756 over 388 requests, with one unknown cost; USD 3.73199355 is reserved. This is a lower bound, not settled spend. “Evaluations recorded” means reports saved, not completed experiments. [Actual S44 audit](S44_ACTUAL_CALIBRATION_STOP.md) preserves every denominator.

## 8. Deployment and remaining proof

S37 completed Compose startup on the user's existing prepared laptop using Docker server 29.9.3. Nine local endpoints returned HTTP 200: UI, financial API, EDGAR, filings KB, sandbox, portal, monitoring exporter, Prometheus and Grafana. Its 400 offline checks passed. The initial pilot stopped on cache-byte provenance before any paid call. S39 reconciled only four reviewed public SEC cache copies against unchanged frozen data, then passed 414 offline checks, ten MCP fact checks, seven section/search checks and sandbox preflight. It stopped at price verification with zero paid calls. S40 subsequently resolved that compatibility issue and produced the actual pilot above. Earlier stops remain separate records.

The exact eleven-row requirements map links every final-review minimum to evidence and names missing proof. Actual fresh-runner clean installation now passed at commit 3b1b96a: all 12 containers ran, nine endpoints returned HTTP 200, authenticated Qdrant bootstrap and protocol/UI/SSE checks passed. Synthetic four-company-year fixtures were used; no research workflow or LLM call ran. All 11 commands succeeded and cleanup completed in the reported 126.98-second launcher run. [Sanitized CI evidence](ACTUAL_CLEAN_INSTALLATION.md) retains provenance. The PR is merged. A separate hosted controlled negative CI job failed deliberately with exit code 20, thresholds null and performance_claim=false; the actual numeric gate was skipped. [Failed-run evidence](ACTUAL_CONTROLLED_NEGATIVE_CI.md) supplies its explanation; its exact commit is not visible. Documentation installation remains unverified. Actual interactive checkpoint resume and edit invalidation now passed in isolated synthetic state. The backup video, eligible judge calibration, held-out before/after results and scoped attack/clean comparison remain pending. The actual human labels and lesson decisions have now been received. Configured critic/representation ablations, three repeated final comparisons, hand-labeled risk change and external FinanceBench checks have no inspected final measurements here.

The project demonstrates measured development retrieval gains, source-linked tools, saved replanning and publication control, historical memory replay and a small real architecture comparison. It also preserves a semantic security failure and poor cross-model agreement. These observations justify neither automatic semantic grading nor a claim that all capstone requirements are complete.

## 9. Evidence and authorship

The accompanying [requirements map](FINAL_REVIEW_EVIDENCE_MAP.md), [manifest](evidence/Evidence_Manifest.json), [tool map](TOOL_SCHEMAS_AND_TEST_MAP.md), [pilot records](PILOT40_RESULTS.md), [experiment log](EXPERIMENT_LOG.md) and [contribution log](CONTRIBUTION_LOG.md) provide inspectable provenance. Original archives, members, content hashes and exact extracts are retained. Private numeric gold answers, credentials, watchlists and live databases are excluded from this submission summary.

The operator supplied requirements, ran the local scripts, inspected artifacts, performed the recorded publication approval and submitted the personally attested source-support labels and lesson decisions. ChatGPT/Codex and parallel AI reviewers assisted implementation, testing, documentation and semantic review; Claude provided the explicitly simulated review. No teammates, human labels, human reviewer identities or unobserved executions are invented. All new material is staged separately from the frozen S40 source.
