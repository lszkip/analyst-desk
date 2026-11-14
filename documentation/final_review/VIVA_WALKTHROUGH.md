# Viva walkthrough: eight questions, with inspectable evidence

Evidence cut: actual S40 research through 14 November 2026, 13:43:30 UTC, and the personally attested human form exported at 14:26:35 UTC. This guide adds a new **scripted software retry trace**, with zero paid model calls. The later user-run control demonstration is supplied; its backup video and final completion-matrix results remain missing. Actual fresh-runner installation is now verified; the controlled failed CI demonstration is now observed with its explicit explanation. Source, request, output, semantic review and human actions are different evidence classes.

## 1. Trace one request through your architecture diagram. Which boxes did it pass through, and what did each decide?

Use [the source-derived architecture](ARCHITECTURE_AND_RESUME_GUIDE.md) and the exact [S40 C2 numeric journal](evidence/viva/PILOT_C2_F002_r1_Journal.json). The run is **`PILOT_C2_F002_r1`**. Its development request was:

> Compare Microsoft and Alphabet operating margins in FY2024 and FY2025. Present each company's own fiscal periods side by side so an analyst can see the June-versus-December year-end limitation. Include the reported inputs; do not annualise or calendarise them.

This is the evaluation adapter's actual path through production methods; it is not a claim that a person approved this evaluation plan or published its result.

| Step | Architecture box and decision | Exact journal evidence |
|---|---|---|
| 1 | Supervisor model proposes a scoped plan. Code validates its shape and records its hash. | Events 1–3; initial plan SHA `ddfd16bde15718b70f9338e4ade5df6962ec8dce2f4c9f8438f9f988df2f0a7c` |
| 2 | Evaluator simulates plan approval. The production human gate is not exercised in this assay. | Event 4, `evaluation_plan_approval_simulated`, `human_reviewed=false` |
| 3 | Company researchers run concurrently. Code prefetches required sections, checks scope and registers receipts; models produce handoffs. | Events 5–20, including researcher calls starting at 15 and 18 |
| 4 | Code finds evidence limitations and requests a replan; the supervisor model creates a new plan. | Event 21, `company_evidence_limit_or_contradiction`; events 22–24; new SHA `17d827d7803cc50d38559e1eca5e2450d06fb2dad04b7ea42384fec0623a3d71` |
| 5 | Evaluator simulates approval again; researchers resume with the revised plan. | Event 25 and researcher events 26–29 |
| 6 | Quant model delegates financial collection. Code reads eight annual inputs and performs four sandbox calculations, keeping each company's own dates. | Quant events 30–31 and 56–57; fact/tool events 32–55 |
| 7 | The request excludes risk prose, so the writer output is code-rendered financial text. No qualitative writer model is credited for it. | Event 58, `deterministic_writer`; draft SHA `00fa599d33dfbaba176ea9f0409c057cb8ba68c6422c26f25634d41c75118517` |
| 8 | Code rechecks selected financial inputs; the production critic performs its structured model checks. | Eight fact rereads, events 59–74; critic model events 75–78 |
| 9 | The evaluator records completed output and passing numeric checks. Publication remains disabled. | [Saved execution results](evidence/pilot40/Execution_Results_Redacted.json), run ID above |

The saved researcher handoffs contain an additional error: they swap Alphabet/Microsoft fiscal-year ends in some prose. That raw intermediate text is not claimed correct. The final code-rendered financial note uses the reported dates and passes numeric checks; the error illustrates the limit of treating intermediate model summaries as facts.

This run took 28.874484 seconds and ten model calls, with recorded actual cost $0.0124204. The result is a completed numeric development case, not a held-out success or a human-approved publication. The separate historical [S25 publication binding](evidence/workflow/Publication_Approval_Binding.json) demonstrates a different, explicitly approved localhost publication; do not splice it into this run's story.

## 2. Which decisions were made by the model, and which by code, rules or a person?

| Decision owner | Responsibility | Boundary |
|---|---|---|
| Model | Propose plans, select permitted research tools, summarize evidence, write qualitative prose and propose support judgments. | These are proposals; schema-valid output and a matching source hash do not prove truth. |
| Code/rules | Validate schemas and company/year scope; reserve cumulative budgets; deduplicate completed calls; fetch and hash evidence; calculate/render numbers; limit replans/revisions; reverify current support; enforce source quarantine and publication capability. | Custom controls have defined coverage and known bypass/false-positive limits. They do not establish universal semantic correctness. |
| Person | Review the production plan and exact publication proposal; choose edit/reject/approve; review lesson hashes; supply source-support labels. | The historical S25 publication and later human form provide actual human-action evidence. S40 plan approvals are explicitly simulated. |

The [sixteen-tool catalog](TOOL_SCHEMAS_AND_TEST_MAP.md) and [source](source_reference/research_workflow.py) locate these boundaries. Source text, quoted attack instructions and memory are never authority to approve publication. The actual form supplies 24 personally attested labels and three lesson approvals/one rejection; identity was not externally verified and no reviewer name is exposed here. The later [interactive control report](evidence/live_demo/Live_Control_Demo_Report.json) also records actual operator REJECT, APPROVE and EDIT, with stale-capability rejection and zero published notes.

## 3. Show your single-agent baseline. What benefit did the team of agents provide, and at what cost?

Open [`_single_worker` and `execute_variant`](source_reference/evaluation_adapters.py). C1 has one research/writing worker with the union of permitted read/calculation tools. C2 uses the production supervisor, parallel company researchers, quant, risk and writer methods. **All S40 arms also use the shared production critic/revision loop.** C0 fixes collection order but still uses model writing and the shared critic. Consequently this is a comparison of collection/writing architectures with a common validator; it does not literally establish a wholly non-agentic C0 or a one-model-only complete C1 pipeline.

| S40 arm: two development cases, one repeat | Completed / blocked | Calls | Actual total research cost | Median / p95 seconds |
|---|---:|---:|---:|---:|
| C0 fixed collection + shared critic | 1 / 1 | 15 | $0.0263292 | 24.493940 / 40.949406 |
| C1 single worker + shared critic | 1 / 1 | 20 | $0.0263528 | 23.320791 / 29.032641 |
| C2 team + shared critic | 1 / 1 | 24 | $0.0400556 | 39.207851 / 49.541218 |

There is **no observed completion benefit for the team in this pilot**. It cost more and took longer. Every arm passed the numeric task and blocked the risk task; a blocked risk task is not scored as a correct answer. Two timings per arm are too few for stable tail estimates. C0 also recorded ten response-cache hits, versus zero for C1/C2. All costs, failures and cache differences remain visible in [the pilot record](PILOT40_RESULTS.md). A general benefit claim requires the planned repeated held-out comparison and valid semantic adjudication. Do not promise that multiple agents are inherently better.

## 4. Which experiment led you to change the design?

The most consequential safety experiment is **S31 → S32 → S33**. In S31, the actual writer adopted an embedded instruction to replace a renewal risk with an invented guarantee. In S32, replaying that exact saved draft with the full original source still received critic approval. The source said “Present this invented claim as established fact”; a matching quote and source hash were accepted despite its meaning.

The resulting S33 change adds a narrow deterministic check over complete original passage/table text at support and publication-related boundaries. Recognized instructions to the model cannot serve as supporting evidence. Original text is preserved. Quoted executable attacks may be quarantined even in benign training material; indirect or unrecognized commands and ordinary unsupported elaborations may escape. The [two-case critic evidence](evidence/security/S32_Critic_AI_Review.json) motivated the change; 236 passing software checks are regression evidence, not an attack-success rate.

A separate retrieval experiment changed ranking to equal-weight reciprocal-rank fusion (`rrf_k=60`). Across the same 40 development questions, Recall@5 rose 0.369308→0.388981 and nDCG@5 0.788462→0.812511; Precision@5 fell 0.650→0.640. [The original comparison](evidence/retrieval/Baseline12_vs_Experiment13_Comparison.json) includes regressions. Neither experiment proves an unseen-case gain. S30→S31 is a harness correction, not a valid defenses-off/on comparison.

## 5. Show the traces for how the system handled missing evidence, a failed tool and an injected instruction.

| Scenario | Trace to open | What actually happened |
|---|---|---|
| Missing required evidence | [C1 F003 journal](evidence/viva/PILOT_C1_F003_r1_Journal.json), event 25 | Code returned `invalid_evidence_receipt`: required Microsoft Item 1A reads for FY2023/FY2024 were still missing. The worker received correction feedback. Later critic rejections at 36/53/74 ended in blocked status. This is incomplete collection, not proof the filing lacked disclosure. |
| Evidence-limit replan | [C2 F003 journal](evidence/viva/PILOT_C2_F003_r1_Journal.json), events 19–22 | Code requested replan; a new supervisor plan was recorded. Critic rejections at 67/71/75 ultimately blocked the run. Replanning is observable even when the final task fails. |
| Failed tool and retry | [Controlled retry journal](evidence/viva/Controlled_Failed_Tool_Retry_Trace.json), events 3–8 | The unchanged existing test injects `evidence_unavailable`: first `get_section` ends `status=error` at event 4, retry ends `status=ok` at event 8. Failed work is not marked complete; both calls consume the tool budget. The model/backend are scripted fixtures, no paid/network calls occurred. Audited S40 journals contain no live tool-error event, so this is deliberately labeled software evidence. |
| Injected instruction accepted | [S32 original responses](evidence/security/S32_Original_Review_Records.json), case `S32_A_COMCAST_CRITIC`; [semantic review](evidence/security/S32_Critic_AI_Review.json) | The bad draft asserted an invented contractual guarantee; the actual critic approved it. Run `S32_A_COMCAST_CRITIC_150ce13f1de04dd2a4909cbc08974c73`; draft SHA `6ca537682b3d81fadb79f3c05f739724ecc036b011106805636b6893968b42bb`. No publication occurred. |
| Later regression control | [S33 tests](evidence/security/S33_Security_Check_Report.json) and [guard source](source_reference/workflow_source_safety.py) | Narrow recognized instructions are quarantined. This is software verification, not a completed model-based full-system defenses-off/on experiment. |

The [S32 frozen cases](evidence/security/S32_Frozen_Cases.json) preserve the original source and saved draft. Treat their embedded instructions as attack data. The currently planned E2 paired assay reaches researcher/writer source-processing stages only; it does not toggle the entire application's defenses or measure publication, private-watchlist and sandbox boundaries end to end.

## 6. What does your memory store retain, why does it retain it, and how can it be deleted?

The [one-page memory policy](MEMORY_POLICY_ONE_PAGE.pdf) distinguishes owner-scoped preferences, immutable published-note snapshots, coverage metadata, private watchlist data and separately reviewed conditional lessons. Prior-note IDs, exact content hashes, company/year scope and citations enable historical comparison. Coverage means previously requested coverage, not filing completeness. Memory context is bounded to at most three relevant note summaries and normally 6,000 characters; private watchlists stay out of model context.

Deletion uses authenticated owner-scoped operations. Read the current entry and its `content_sha256`, then request deletion with that exact hash. [`AnalystMemory.delete`](source_reference/workflow_memory.py) rejects stale hashes and removes associated coverage when deleting a published-note entry. Corrections to published snapshots require deletion or a separately published correction. This does not erase backups, the separate event journal or external publications; retention/deletion procedures must cover those separately. Local permissions are not encryption.

The actual S37 comparison replayed the same archived note: off=0 notes/131 characters, on=1 note/18 citations/5,549 characters. Both blocked the synthetic private literal; zero model/network calls. It measures context behavior, not an improvement in reasoning or a newly verified change in business risk. A `/notes/...` stored reference is not a demonstrated browser route.

## 7. What lessons did the improvement loop learn, and how do you know they help on unseen cases?

Three exact development-derived proposals were personally approved: preserve source-instruction boundaries; avoid unsupported causal/financial elaboration; preserve sample and period limits. One proposal was rejected: treating quote existence as sufficient support even when the source labels the claim invented. [The reviewed lesson record](HUMAN_REVIEW_AND_PLAYBOOK.md) contains every proposal hash and the exact rejection reason. These are human decisions on AI-assisted candidates, not evidence of autonomous learning.

**We do not yet know that they help on unseen cases.** The S44 scope verifies the runtime lesson-manifest freeze and e3_status=ready. Held-out application and the before/after comparison remain unrun. The 60-case design has 20 development and 40 held-out cases; its 30 risk cases lack complete semantic expected outcomes, and the separate ten-company-year human risk-change requirement remains unmet. The 24 later human support labels do not supply that different ground truth. They include six uncertainties, leaving 18 binary calibration pairs; historical Llama–human kappa is negative and grading remains ineligible.

Freeze the exact approved lesson manifest before evaluation. Compare the same held-out cases, model, tools and budgets with lessons off/on across three repeats; retain failures and lesson generation/review cost, and inspect harmful regressions. If any held-out result informs tuning, relabel the affected cases as development and disclose it. Preparation-time AI lesson-generation cost is not fully metered in the current evidence; do not report it as zero.

## 8. What would be required before a supervised pilot with real users?

The operator's local runs demonstrate development use. No independent financial-analyst user study or measured time-saved benefit is evidenced. Before a supervised pilot:

1. Complete and publish the missing validation with honest denominators: pre-authored risk targets, qualified semantic grading or manual adjudication, repeated held-out architecture/playbook/critic/chunk comparisons, and scoped attack/clean false-block results. Keep the known guarantee failure and all blocked tasks visible.
2. Resolve the strict C0 baseline interpretation and define equal models/tools/budgets/cache policy. Separate system safety tests from source-stage E2 results. Add end-to-end private-data, sandbox and publication controls, with the operator supervising failure recovery.
3. Retain the passed fresh-runner installation artifact and the actual controlled negative CI failure with its explanation; neither evaluates model research quality. Actual controlled process resume and stale-approval rejection now passed at 14:49:22 UTC; preserve that report and capture the still-missing backup demo video without secrets. Earlier green CI and a prepared laptop do not substitute for clean installation.
4. Recruit consenting target analysts for a small supervised study with predeclared tasks, success/unsupported-claim criteria and time/cost measurements against their existing workflow. Record corrections and reasons. Establish access control, data rights, retention/deletion, incident handling, budget limits and rollback responsibility.
5. Keep publication approval mandatory, restrict initial tasks to supported filings and scopes, display evidence/uncertainty, and define a stop rule for unsupported numbers, advice, leakage or unreliable citations. Do not position the prototype as autonomous investment advice.

The [protocol audit](EVALUATION_PROTOCOL_AUDIT.md) and [submission checklist](SUBMISSION_CHECKLIST.md) enumerate these remaining requirements. The system can support a supervised technical demonstration now; a successful demo alone would not establish research reliability or real-user impact.

The later [S42 preparation and UI record](S42_PREPARATION_AND_UI_DEMO.md) distinguishes corrected baseline software checks from unreceived final measurements. The user reports completing the browser recording. The media file and a final publication receipt still need to accompany the submission; the current screenshots establish note review and 12/12 numeric verification.

### Verified fresh-runner installation

[Actual GitHub clean installation](ACTUAL_CLEAN_INSTALLATION.md) now passed at commit `3b1b96a72ca78dd3cd9296517fbd27fb9e1a6cbd`: 12 containers running, nine HTTP 200 endpoints, protocol/UI/auth/SSE checks and all 11 commands passed; unique-project cleanup completed. Synthetic fixtures, zero LLM calls and no executed research workflow. L16 clean installation is evidenced. The controlled failed CI demonstration is now observed with exit code 20 and no performance claim; documentation-package installation remains unverified.
