CURRENT CHECKPOINT - 12 November 2026

Docker log 41.25 confirms a successful image build and 505 tests passed in
40.653 seconds after the workflow_support.py build-context fix.
Live log 41.23 confirms one exact-content, human-approved publication for
margin_demo_v2, with no pending nodes. This was a human-corrected,
budget-extended development run; it is not held-out evaluation evidence.
GitHub CI and the broader capstone evaluations remain pending.
This checkpoint supersedes earlier pending-status entries below.

# Analyst Desk: requirements and evidence register

Authority: `1. capstone_project.pdf`, 16 October 2026. Physical pages 3, 5–6 and
7–8 (Analyst Desk printed pages 15–16). Cohort dates/team approvals remain separate.
This register must be updated with actual evidence after each milestone.

## Completion rule

An implementation, an automated fixture test, a real-data test and a final-review
demonstration are different evidence. None substitutes for another. A row is
complete only when its specified evidence exists and was reviewed. No estimate of
"100% complete" is derived from test counts. Known gaps stay visible.

Exceed explicit minimum counts. Respect upper bounds and exact constraints:
15 companies remains within the prescribed 10–15; the critic still has a maximum
of two rounds; the report remains 4–6 pages. Do not increase these past the limit.

## Targets above the minimum

| Requirement | Brief | Project target | Current evidence |
|---|---|---|---|
| Labelled retrieval queries | 30+ | **40 reviewed queries** | 40 unlabelled development questions supplied; gold labels and measured Recall@5 pending |
| Golden research cases | 60+, approximately 20 dev and at least 40 held out | **80: 24 development, 56 held out** | Pending; separate Claude drafting workflow selected; validation and frozen datasets pending |
| Human judge calibration | 20+ human labels | **30 human-labelled outputs**, judge from another family, Cohen's kappa | Pending |
| Red-team suite | 30+ attacks | **40 attacks**, plus clean-case false-block measurement | Pending; separate Claude drafting workflow selected; attack validation and frozen suite pending |
| MCP servers | At least 3; Analyst Desk names 4 | **4 domain servers** | All four server implementations supplied; EDGAR/KB laptop evidence exists; sandbox/portal laptop Docker and live HTTP checks passed; retry fix confirmed on the laptop |
| Specialist agents | At least 3 | **6 roles:** researcher, quant, risk-diff, compliance critic, writer, publisher; plus supervisor | First LangGraph backend supplied; publisher is a deterministic action role. Live model/corpus validation pending |
| Tool tests | One per tool | **Multiple success, validation and failure tests per tool** | EDGAR/KB plus sandbox/portal success, failure and authorization tests supplied; 414-test laptop Docker suite and live checks passed |
| Risk-change labels | 10 company-years | **12 company-years labelled by hand** | Pending |

These are targets, not completed counts. The 40 retrieval questions are separate
from the 80 research cases. Synthetic unit fixtures do not count toward either.

## Full requirements ledger

| ID | Required behaviour / evidence | Status and remaining work |
|---|---|---|
| D1 | 10–15 companies, two sectors, three fiscal years each | Prepared and replay-verified on laptop: 15 × FY2023–25 = 45; two sectors |
| D2 | Original 10-K HTML split by Item; tables kept structured; source/date/company/year/Item metadata | Implemented; original byte fingerprints, stable passage/table/cell IDs, IBM Exhibit 13 included |
| D3 | Accurate limits and source coverage | Targeted IBM warnings resolved. 239 unassigned passages retained. Source/Item/table quality review and retrieval-driven checks remain; replay is not proof of semantic completeness |
| R1 | Dense embeddings and persistent vector database | Full laptop build and replay/vector verification passed: 24,607 search windows from 16,235 passages across all 45 company-years; pinned local BGE model and persistent Qdrant storage |
| R2 | BM25 + vectors combined by Reciprocal Rank Fusion | Implemented; separate BM25/dense/hybrid experiment modes and tests |
| R3 | Cross-encoder reranker and query decomposition | Implemented; laptop MSFT risk and IBM table searches passed; comparison plan produced the expected 12 company/year/facet branches. Labelled quality comparison remains pending |
| R4 | Passage-ID citations in final answers | Search returns passage/cell IDs and sources; final writer/answer integration pending |
| R5 | Recall@5 on 30+ labelled queries, before/after one change | Harness supplied with 40-query requirement; four modes; gold labels excluded from requests; reviewed labels and final results pending |
| R6 | FinanceBench open sample as external retrieval check | Pending; keep independent of development tuning and respect its licence |
| T1 | FastMCP with typed input/output, structured errors, read/write classification, per-agent least privilege | Four typed server contracts implemented. KB uses read-only Qdrant; sandbox and portal have separate scoped credentials. Graph role allowlists and denial tests supplied; live graph evidence pending |
| T2 | EDGAR list filings, get section, XBRL facts with cache | Implemented, including paginated complete section passages; existing tool tests and new section tests pass. Docker service starts healthy; full-corpus HTTP get_section check remains pending |
| T3 | filings-kb hybrid search/rerank/get passage | All five KB tools passed through the Docker gateway against the laptop corpus. Read-only Qdrant write attempt rejected; coverage reports 45 company-years |
| T4 | python-sandbox: calculations in code, no network, time limit | Implemented with isolated subprocess, seccomp kernel policy, CPU/wall/memory/output limits and real kernel probes. Laptop Docker tests and live kernel probes passed |
| T5 | research-portal: past notes, publish_note after approval | Persistent SQLite mock portal with authenticated read/draft/publish separation; exact-content human approval, edit invalidation and idempotent publication tested. Laptop synthetic-note human approval/publication demonstrated; repeated command returns the existing note without republishing, confirmed on laptop |
| O1 | LangGraph typed state, conditional edges, self-correction, persistent checkpointer; stop/resume demonstration | Backend supplied and tested with real LangGraph/SQLite, including separate-process resume. Live laptop model demonstration pending |
| O2 | Visible editable plan and outline; replan on contradictory tool results; ReAct within specialists | CLI plan/edit interrupt, bounded role tool loops and a replan gate supplied; scripted control-flow tests pass. Real-model replan trace and UI pending |
| O3 | Parallel company research; critic loop at most two rounds; token budget enforced in hooks | Parallel Send branches, at most two automatic revisions, persistent conservative token reservations and tool limits supplied. Tested in software fixtures; live quality/cost validation pending |
| O4 | Supervisor + specialists with typed handoffs | Pydantic plan, handoff, draft and critique contracts supplied for the first margin/risk graph; broader research-task coverage pending |
| O5 | Same-model/tools/budget C0 fixed workflow, C1 single agent and C2 team | Pending; comparison must include quality, cost and time |
| N1 | Every number linked to XBRL fact or table cell and checked by code | XBRL lookup/replay guardrails exist. Sandbox Decimal/pandas arithmetic implemented; first graph renders SEC-fact-derived financial tables and checks Decimal margins; table-cell-derived claims and broader numeric tasks remain pending |
| N2 | Align fiscal periods/units; never let the LLM perform authoritative arithmetic | Fiscal-period/unit lookup safeguards exist; first graph checks same-company numerator/denominator periods and units and prints actual periods in peer comparisons; live model validation and wider metric support pending |
| M1 | Trim/summarise short context; persistent long-term store | Pending |
| M2 | Preferences, coverage list, past notes; private watchlist stays within system | Pending |
| M3 | One-page memory policy: what/when/access/correction/deletion; memory-on/off comparison | Pending |
| I1 | Agent/evaluator/reflection loop; code first, judge where necessary | Pending |
| I2 | Human reviews every conditional lesson; retrieve relevant lessons; freeze before held-out run | Pending |
| I3 | Before/after held-out results, playbook, rejected lesson/reason; account for lesson-generation cost and harmful lessons | Pending |
| H1 | interrupt() approve/edit/reject for every external write; approval bound to exact payload; edits invalidate approval | Portal gate and human CLI supplied/tested; LangGraph plan/note interrupts and separate-process resume are supplied and software-tested; live laptop approval flow and UI remain pending |
| H2 | Pre-tool permission and budget checks; post-tool trace/scrub | Existing request/output checks are partial; graph role/budget hooks and hashed event records supplied; LangSmith/Langfuse export, complete observability and live evidence pending |
| S1 | One-page threat model; layered input/output guardrails; least-privilege tools | Existing financial guardrails and local container restrictions are partial; complete system threat model pending |
| S2 | Data-path injection, stock recommendation injection, private-watchlist exfiltration, sandbox network attempts | Pending final 40-attack suite; parser untrusted-source flags alone are not a defence |
| E1 | 80 research cases with expected outcomes written before running; related variants grouped; held-out data inaccessible to agents | Pending. Separate Claude sessions will draft held-out cases and attacks under the selected solo workflow; validate expected outcomes, record authorship and freeze before evaluation. AI-assisted drafting is not claimed as independent human authorship |
| E2 | Code checks outcomes/trajectories/tool use; judge from different family calibrated on 30 human labels, Cohen's kappa | Pending |
| E3 | E1 baseline/team, E2 defences off/on, E3 playbook before/after, E4 signature ablations; configuration-driven harness | Retrieval modes and table representation ablation supplied; agent-level experiments pending |
| E4 | Signature ablation: critic on/off AND table-aware vs plain-text chunks | Search-representation ablation supplied as a starting point. Full plain-text parsing/chunking variant, end-to-end results and critic graph remain pending |
| E5 | Counts, violations, attack success, false blocks, cost/case, median/p95 latency, errors including timeouts | Retrieval harness records latency/errors only; complete agent report pending |
| E6 | Final held-out comparison repeated three times, spread reported; any post-view tuning reclassifies those cases as development | Pending |
| P1 | Every agent/critic/judge/reflection prompt uses role/context/task/constraints/format/examples | Graph role prompts use six slots in workflow_config.json. Evaluation-judge and reflection prompts pending |
| P2 | Exact model IDs and prompts in versioned config; same models/budgets for fair experiments | Retrieval config pinned by revisions and file hashes; first graph pins the existing API model openai/gpt-4.1-mini and versioned role prompts; approved model-list documentation, evaluator family and fair-comparison configs remain pending |
| V1 | FastAPI streaming and research UI with plan view/draft | Existing FastAPI numeric endpoint only; streaming research flow/UI pending |
| V2 | docker compose up starts app, all MCP servers, vector store, Prometheus/Grafana; clean-machine reproduction | Qdrant, gateway, EDGAR and KB started healthy on the laptop; live KB checks passed. Sandbox/portal/gateway started healthy on the laptop; live authenticated checks passed; monitoring and clean-machine evidence pending |
| V3 | Traces for every LLM/tool run; dashboard shows verified-number share, critic rejections, tokens/cost per note, EDGAR cache hits/latency | Pending |
| V4 | Alert when verified-number share drops below 98%; working alert demonstrated | Pending |
| V5 | Evaluation in CI fails below threshold; show one failed CI run with explanation | Offline software tests in CI. Retrieval evaluation CLI exists; final labelled quality gate and evidence pending |
| F1 | Own architecture diagram and numbered request walkthrough | Pending final architecture |
| F2 | One-command README; threat model; memory policy; 4–6-page eval report; experiment log; contribution log; slides; backup demo | Pending final deliverables; target six-page evaluation report |
| F3 | Demo margin comparison with fact IDs; planted-number correction; injected buy recommendation blocked; memory follow-up | Pending |
| F4 | Explain model/code/human decisions, failures/missing evidence, limits and pilot requirements; redraw/justify architecture in viva | Pending preparation |

## Explicitly deferred extensions

AWS, Terraform, S3/RDS and Lambda are extensions, not substitutes for the required
local Compose system. No cloud deployment or storage is claimed complete.

## Reviewed evidence: 11 November 2026

1. DockerOut(1).txt: 361 tests passed in 14.813 seconds. Both real models were
   downloaded and fingerprint-verified. The corrected gateway then passed the
   laptop health, indexing and live MCP checks below.
2. IndexOut.txt and IndexVerifyOut.txt: status ok; all 24,607 search windows built
   and verified, covering 45 company-years / 15 companies / FY2023-2025. Source
   totals: 10,901 text passages, 5,334 tables, 239 retained unassigned passages,
   and no reported coverage warnings.
3. SearchOut.txt: MSFT FY2025 Item 1A cybersecurity passages returned.
   TableSearchOut.txt: IBM FY2025 consolidated income statement ranked first.
   SearchPlanOut.txt: 12 branches for two companies, three years and two facets.
4. MCPStartOut.txt and MCPRetrievalOut.txt: service startup succeeded; all five
   KB tools passed and a read-only database write was rejected.
5. ReviewExportOut.txt and RetrievalReview.zip: 40 unlabelled development
   questions and 5,109 unique source passages, with matching scope metadata.
   No relevance labels, Recall@5 result or final retrieval quality pass exists.

See RETRIEVAL_VALIDATION.md for the collection and source fingerprints. These
checks support an implementation checkpoint; they do not complete retrieval
evaluation or the whole capstone. Retrieval PR #10 is merged: supplied merge log
confirms local main at 7207082. Specific CI results are not inferred from that log.

## Next evidence and work

1. Research-tools PR #11 is merged; supplied laptop log confirms main at 56ebbce.
   The first LangGraph backend is supplied next. Install it, run offline/Docker
   checks, then validate the actual OpenRouter and prepared-corpus workflow.
   Specific GitHub CI results are not inferred from the merge log.
2. Review the development question scope and establish relevance labels before
   freezing and running the four-mode evaluation. The brief requires 30+ labelled
   queries, not 5,109 manual decisions; the larger review workload came from our
   current broad scopes. Keep all relevant passages for each chosen scope and do
   not trim gold sets to inflate Recall@5. Target remains 40 reviewed queries.
3. Run FinanceBench and the remaining retrieval/agent experiments.
4. Prepare separate Claude authoring instructions for held-out research cases
   and attacks. Record AI assistance, verify expected outcomes against sources,
   freeze the datasets, and keep them unavailable to the research agents. The
   brief's separation of human authorship remains distinct from this solo method.

## Research tools laptop evidence: 12 November 2026

Installation completed without dependency conflicts. The supplied Docker log
reports 409 tests passing in 24.549 seconds. Startup and live HTTP logs confirm
healthy sandbox/portal/gateway containers, successful calculation and denied
network/file/process operations plus denied unauthorized publication/approval.
The first human review published the synthetic test note. Repeat runs revealed
a CLI status-handling bug, now covered by five additional regression tests.
The follow-up PortalRetryFixOut log confirms all 414 Docker tests passed in
25.853 seconds, the rebuilt portal was healthy, and the repeated review returned
already_published with the existing note ID and unchanged content hash. No new
approval or publication was performed. PR #11 is merged and local main is confirmed at 56ebbce; specific CI results were not supplied.
This evidence does not complete LangGraph interrupt/resume or financial claim
verification.

## First workflow backend: 12 November 2026

The new backend supports operating-margin comparisons, with optional qualitative
risk discussion, for up to four covered companies and FY2023–2025. It does not
silently treat other financial questions as margin questions. Fiscal years and
companies are explicit request fields and are shown at the plan gate.

Researcher, quant, risk-diff, writer and critic use bounded model decisions; the
publisher is a deterministic action role. LLM tool catalogues never include
human approval or publication. The CLI uses a separate human credential after
explicit typed confirmation. Approval capabilities remain in memory, outside
LangGraph state, resume data, journal and model prompts. Losing that capability
requires fresh human review; a completed publication is recovered by note ID.

Software validation includes the real four HTTP MCP services with synthetic
SEC/retrieval fixtures and a scripted model, plus three separate processes using
the same durable checkpoint. These are implementation checks, not real model
quality scores or independent financial/retrieval evaluations. See
WORKFLOW_VALIDATION.md for the exact results and gaps.

Still required: live laptop workflow evidence, wider numeric/table-cell claims,
complete risk-change evidence, C0/C1/C2 experiments, long-term preferences and
private-watchlist policy, Reflexion, independent-family judge calibration, all
held-out/attack/FinanceBench evaluations, FastAPI research UI, LangSmith/Langfuse,
Prometheus/Grafana and alerts, clean-machine reproduction and final submission
materials. No full-capstone completion is claimed.

## Workflow laptop checkpoint and preflight correction

The supplied WorkflowInstallOut log confirms dependency installation without
conflicts and all 34 new tests passing in 9.230 seconds. DockerOut confirms the
initial workflow image built and all 448 tests passed in 37.312 seconds.

Before the laptop preflight run, review caught a checker-only field mismatch:
get_coverage returns company_years, while the first checker expected
verified_company_years. The corrected checker validates the exact company/year
pairs, rejects coverage warnings and requires all sandbox kernel probes. The
actual preflight is now exercised through all four HTTP MCP services with
synthetic fixtures, in addition to the existing graph integration test.
This correction does not change the agents, model configuration or graph state.
The later laptop preflight passed; the live workflow remains incomplete as
recorded in the following checkpoint.

## Live workflow checkpoint and scope correction

The supplied 41.2 log verifies all 45 company/year pairs and successful EDGAR
section/fact HTTP calls, sandbox kernel restrictions and portal reading. The
41.3 log records the first real planner response. Logs 41.4 and 41.5 demonstrate
human plan editing, a new exact-content hash, a fresh saved review gate and
explicit approval. Research then stopped when the GOOGL branch requested MSFT.
No publication occurred. The 41.7 diagnostic records incomplete provider cost
telemetry rather than silently treating the interrupted request as free.

The v3 patch preserves the company/year guard and adds branch-specific argument
schemas plus bounded feedback for rejected model inputs. Source provenance,
role and policy failures remain fatal. Eight new software regression tests
include resuming a failed parallel run while retaining its plan approval and
budget, and rejecting repeated cross-company requests without dispatching them.
See WORKFLOW_VALIDATION.md and PATCH_MANIFEST.json for current test results.

Still pending: successful real multi-agent research, final note review and
publication, the revised laptop Docker/CI checkpoint and all broader gaps
already listed above. These live development steps are not held-out evaluations.

## Live researcher progress correction

The v3 laptop workflow/client suite passed 44 tests. Its live continuation then
stopped at agent_step_limit. Diagnostic 41.9 shows repeated MSFT FY2023 Item 7
page-zero requests, while Alphabet completed with explicit source limitations.
The v4 patch supplies compact source previews with pagination, successful-call
history, saved source samples and remaining steps. It blocks exact successful
duplicate requests within an invocation and reserves the final decision for a
handoff; it does not increase bounds or declare source coverage complete.

Eight new progress regressions include resuming only the stalled company branch
with its original approved plan and budget. The focused 16-test progress/scope
suite passed. See WORKFLOW_VALIDATION.md and PATCH_MANIFEST.json for full-suite
results. The real live continuation, review/publication and final Docker/CI
remain pending, along with the wider requirements already listed.

## Live writer/coverage failure and corrected development run

Logs 41.12 confirm a human-edited replacement plan and explicit approval, seven
additional section reads, twelve annual fact lookups and six sandbox calculations.
The run then used its original draft and both allowed revisions and ended
blocked without preparing or publishing a note. Diagnostic 41.13 shows repeated
numeric year/Item labels, unsupported absence/causality claims in the draft,
and missing FY2025 risk-section samples for both companies. The deterministic
guard rejected the drafts before the model critic was called.

The v5 correction adds required-section lookup checks, direct source/financial
evidence in the writer context, precise field/token correction feedback, and
visible blocked reasons. It preserves the output guards and the two-revision
bound. Nine focused regressions passed; the complete regression result is in
PATCH_MANIFEST.json. The terminal margin_demo and its usage remain recorded;
margin_demo_v2 is a separate development attempt requiring a new human approval.
Live completion, final Docker/CI, quality/attack evaluations and the other open
requirements are still pending. Software test counts do not establish financial
research quality or complete risk-change coverage.


## Live critic false positive and v6 correction

Logs 41.16/41.17 reached the note gate with all six margin calculations and
18/18 table figures verified, but the critic approved false data-absence,
unsupported causal and overly broad risk-comparison prose. The proposal remains
unpublished. This is a development failure; 100% table verification does not
establish qualitative accuracy or end-to-end task success.

V6 adds evidence-aware checks, sentence-level critic receipts scoped to each
paragraph, source-quote validation and version/draft-bound review before
publication. A human-edited development draft is supplied for the existing run;
its correction must not be labelled autonomous success or independent gold.
485 software tests passed, including 71 workflow/client tests. The real-model
recheck, final Docker/CI, evaluation sets, independent labels and all previously
open requirements remain pending. See WORKFLOW_VALIDATION.md for limits.

## Live critic response failure and v7 development recovery

Logs 41.18/41.19 confirm the human draft edit was accepted but the critic
repeated overlong quotes and exhausted its reservation allowance before
completing. The note is unpublished at edited_note / next critic. Current
usage is 897,486 reserved units, 83 tools, 173,125 reported tokens and complete
reported model cost USD 0.0820072. The earlier table check is not a completed
new qualitative review.

V7 adds precise sanitised schema/receipt feedback, at most two format repairs,
and an optional exact-confirmation development budget extension. Source checks,
the two-writer-revision bound and final human publication gate remain intact.
All used allowance and failed attempts stay recorded. Any extended run is
marked operator_extended_development and excluded from fixed-budget comparisons;
the default evaluation budget is unchanged. This human-corrected debugging run
does not count as autonomous success, independent gold or a held-out case.
496 software tests passed (82 workflow/client tests). Live completion, final
Docker/CI and all previously open capstone requirements remain pending.

## V8 critic reference contract and current development state

The explicit development extension succeeded, but log 41.21 shows the critic
still returning invalid financial receipt fields. It stopped at the configured
three-response bound with 938,310/1,100,000 reservations and 101/240 tools used.
The note remains unpublished; neither the table checks nor software tests
establish a completed live research task.

V8 replaces model-transcribed receipts with selections from a paragraph-bound
catalog of financial records/reporting dates and exact source excerpts. Code
resolves the selected IDs and retains provenance checks; the model must still
judge semantic support for every sentence. The effective six-slot critic prompt
and receipt version now describe that contract consistently. Failures include
their own sanitised diagnostics. Budget, tool, source-replay, revision and human
approval limits remain enforced. No further budget extension is made.

505 software tests passed, including 91 workflow/client tests. Offline checks
of the uploaded draft/evidence fit within context limits without passage
truncation. Live completion, final Docker/CI and all previously open capstone
requirements remain pending. This human-corrected, budget-extended development
run remains excluded from fixed-budget comparisons and autonomous success claims.
