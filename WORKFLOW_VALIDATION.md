CURRENT CHECKPOINT - 12 November 2026

Docker log 41.25 confirms a successful image build and 505 tests passed in
40.653 seconds after the workflow_support.py build-context fix.
Live log 41.23 confirms one exact-content, human-approved publication for
margin_demo_v2, with no pending nodes. This was a human-corrected,
budget-extended development run; it is not held-out evaluation evidence.
GitHub CI and the broader capstone evaluations remain pending.
This checkpoint supersedes earlier pending-status entries below.

# First LangGraph workflow: validation and limits

Current update: v8 catalog-selected critic references. After the explicit v7
budget extension, the critic still produced invalid financial receipts and
stopped at the bounded response limit. The draft is unpublished. V8 simplifies
the response contract while preserving source checks and the existing allowance.

Base checkpoint: `56ebbce`, merge of research-tools PR #11. The supplied laptop
merge log confirms local `main` and `origin/main` at that commit, with only
`.vscode/` untracked. It does not independently establish individual CI results.

## Implemented

- Typed LangGraph state, Pydantic plan/handoff/draft/critic contracts and conditional
  edges. Researchers fan out with `Send`, one branch per requested company.
- Supervisor, researcher, quant, risk-diff, writer and critic model roles, with a
  deterministic publisher action role. Role tool catalogues and lower-level MCP
  hooks both deny unauthorised operations. No model has a human-review tool.
- Editable plan and note `interrupt()` gates with exact hashes. Source limitations
  trigger at most one replan followed by another human plan review.
- At most two automatic writer revisions. Numeric/markup/policy guard failures
  become critic feedback; exhaustion blocks preparation/publication.
- SQLite checkpoints, an execution lock, durable evidence receipts, model response
  replay cache, conservative persistent token reservations and per-run tool bounds.
- Financial values come from cached EDGAR facts. Margin arithmetic runs in the
  sandbox and is checked with Decimal code. Numerator/denominator identity, unit
  and period must match; zero/nonpositive revenue is not divided. Peer rows show
  actual reporting periods rather than implying calendar alignment.
- The financial table is rendered in code. Model prose cannot add numeric literals,
  common written quantities, arbitrary links or fabricated citation syntax.
  The critic judges qualitative support separately against cited source excerpts.
- Approval capabilities never enter model prompts, resume values or graph state.
  The human CLI obtains a capability through the existing separate review route
  after TTY confirmation. Publication uses only the checked exact note.
- Fact/passage replay and receipt integrity checks run before note preparation and
  publication. Changed evidence blocks publication. A completed portal write is
  recovered by original note ID after a disconnect.
- Versioned six-slot prompts and the existing `openai/gpt-4.1-mini` model ID. No
  automatic provider/model substitution. Provider-reported token/cost metadata
  and hashed call events are recorded; unknown costs remain unknown.

## Authoring validation

Validation is performed with Python 3.12 and the pinned dependencies in
`requirements-workflow.txt`. The new tests use synthetic source fixtures and
scripted/mocked model output. They do not use paid model calls or provide
retrieval, numeric-task, judge, or held-out quality scores.

The targeted suite exercises:

1. Parallel company execution and a completed join before quant work.
2. Replanning, edits, rejection, source changes and the two-revision bound.
3. Real local HTTP MCP calls through all four existing server implementations,
   including kernel-restricted sandbox arithmetic and the human portal route.
4. Three separate OS processes resuming the same workflow: plan gate, note gate,
   then one publication. A separate test simulates a disconnect after the portal
   committed and recovers the original note without another publish call.
5. Wrong-role tools, scoped company/year access, forged resume approval, stale
   hashes, lost capabilities, changed receipts and source replay rejection.
6. Atomic budget reservations under concurrency and persistence across reopen.
7. Fixed provider/model requests, required usage metadata, incomplete-response
   failure, sanitised provider errors and refusal of piped human approval.

The preflight-correction regression run passed **450 tests in 56.716 seconds**,
comprising the existing 414-test baseline and 36 workflow/client tests. Dependency
validation reported no broken requirements. Python/JSON parsing, Docker source
allowlisting, workflow YAML/shell syntax and whitespace checks also passed.
The latest correction and laptop milestones are recorded below. No Docker
daemon or user model credential is available in the authoring environment.

## Boundaries and remaining work

This first CLI graph supports operating-margin comparisons with optional
qualitative risk discussion, up to four companies per run, over the prepared
FY2023–2025 corpus. Other research tasks are rejected instead of silently being
answered with margin figures. Expanding numeric metrics, table-cell claims and
complete risk-change extraction remains necessary for the capstone case set.

The critic receives explicitly marked source excerpts and may request source
passages; bounded model observations explicitly mark any truncation. The graph does not certify that top-k retrieval establishes all risk
additions/removals. Complete-section comparison and hand-labelled risk-change
evaluation remain required. A model judge can still be wrong; its calibration
and broader attack evaluation have not been completed.

The token limiter reserves input UTF-8 bytes plus output limit and framing
allowance for the configured byte-level model tokenizer. This is deliberately
conservative; reservations are not billed-token totals and are not refunded on
failure. Changing models/tokenizers requires revalidating this bound. It is not
a monetary spending guarantee. The OpenRouter account's own spend limit remains
the independent spending control. Provider failures with unknown usage/cost are
shown as incomplete telemetry.

Draft preparation writes an internal pending proposal; publication is the
external action behind the human gate. Editing closes the old proposal and
requires a new approval. This local CLI is an operator interface; authenticated
web sessions and an authenticated research API have not yet been added.

Long-term preferences, private-watchlist memory, past-note retrieval in the graph,
Reflexion, C0/C1/C2 comparisons, independent-family evaluation judge, held-out and
attack suites, labelled retrieval/FinanceBench results, research UI, tracing
export, Prometheus/Grafana, alerts, clean-machine reproduction and final submission
materials remain on the requirements register. The current cost/event journal
does not substitute for LangSmith/Langfuse or the required dashboard.

## API references used

- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [OpenRouter API reference](https://openrouter.ai/docs/api/reference/overview)

Installed LangGraph/checkpointer signatures and actual execution tests were used
to verify these integration points for the pinned versions.

## Laptop checkpoint and preflight correction

The initial workflow version passed all 34 new tests on the laptop in 9.230
seconds with no dependency conflicts. Its Docker image built successfully and
passed all 448 tests in 37.312 seconds. Negative-input/authentication diagnostics
in that log belong to tests which finished successfully.

Before the next live check, review identified an integration error in the
preflight helper: the KB summary uses company_years, not verified_company_years.
The helper now checks the actual field, exact unique company/year pairs, empty
coverage warnings and every required sandbox kernel probe. It uses explicit
validation errors instead of Python assertions. Its integration test calls the
real helper through all four HTTP servers and checks that it creates no proposal
or publication. New negative tests reject incomplete/duplicate/wrong-year
coverage and empty kernel-probe reports.

The focused nine-test client suite passed in 15.606 seconds. The final updated
regression result is recorded in the patch manifest. The laptop Docker result
above applies to the initial 448-test version; the corrected helper and added
tests still need the normal final Docker/CI checkpoint. Existing agent, provider,
calculation and publication code is unchanged by this correction.

## Live scope failure and bounded correction

The subsequent laptop preflight passed: all 45 exact company/year pairs,
EDGAR section/fact HTTP calls, all three sandbox kernel probes and portal read
access. It made no model call and created no note. A real model call generated
the first plan (725 reported tokens; USD 0.000632). Human editing produced a
new plan hash, returned to a saved approval gate and made no additional model
call. The edited plan was then explicitly approved.

Research stopped with scope_denied. The supplied scope diagnostic records
two researcher branches, one completed EDGAR section call, five model responses
from six started requests, 6,725 reported tokens and incomplete cost telemetry.
No note was prepared or published. Reconstructing the initial prompt hashes
from the exact configuration, request and edited plan maps the branch beginning
with 5f716e99d9bb to GOOGL and fa41989de3b5 to MSFT. The GOOGL branch's next
response requested MSFT FY2023. The scope guard correctly rejected it; the
agent loop had treated that model-input mistake as a fatal workflow error.

The correction supplies exact argument schemas (including required offset and
branch ticker/year enums), gives each company branch a scoped request view,
and feeds rejected model-input scope back into the bounded ReAct loop. It does
not coerce or substitute company/year arguments. Denials and correction steps
are recorded without raw arguments. Wrong-company source results still raise
fatal scope_denied; permission and policy failures also remain fatal. Handoffs
cannot use another company's registered evidence without correction.

The full corrected suite passed **458 tests in 68.134 seconds** (414 baseline
plus 44 workflow/client tests). Eight added regression tests also passed in
0.525 seconds in the focused run. They cover correcting a
wrong-company request, required-field feedback, repeated-error exhaustion,
strict year types, cross-company passage/table access, fatal upstream provenance
errors, handoff correction, permission/policy rejection and reopening a failed
parallel graph with its original plan approval and budget. The resume test
reproduces the prior fatal input check with synthetic fixtures, then uses the
corrected loop and reaches the final note gate without publishing.

The source patch keeps workflow_config.json, graph state schemas, saved approval
hashes and database formats unchanged. Its stricter model context generates new
prompt hashes; older cached responses are reused only for matching prompts.
Existing reservations are retained. This is software regression evidence;
completion of the real laptop run, revised Docker/CI and capstone quality/attack
evaluations remain pending.

## Repeated-page failure and progress correction

The v3 laptop workflow/client suite passed 44 tests in 10.548 seconds. Its live
continuation completed one researcher and stopped the remaining branch with
agent_step_limit. Diagnostic 41.9 shows repeated MSFT FY2023 Item 7 offset 0
requests, occasional other pages, and two identical-prompt cache hits. The
Alphabet handoff contained two known source IDs and four limitations. No note
was prepared or published. The journal retains 402,073 reserved tokens and 13
tool calls; 76,295 provider-reported tokens are a different measure. Cost
telemetry remains incomplete after an earlier interrupted provider request.

Raw tool results previously lost pagination/source text when a large metadata
prefix consumed the 9,000-character preview. The loop also lacked a successful
request history or a remaining-step counter. The v4 correction:

- Projects source records into bounded previews with source IDs, text, hashes,
  truncation labels and page offsets. Full original evidence stays immutable in
  the journal and is still replayed before preparation and publication.
- Supplies a scoped durable index of saved source samples and missing
  company/year/Item slots. Samples are explicitly not complete-section coverage.
- Lists successful requests and blocks exact duplicates within an invocation.
  Failed results can be retried; another page is a different request. Duplicate
  blocking does not affect the separate publication-time source checks.
- Reports remaining steps and reserves the last of the existing ten decisions
  for a typed handoff with unresolved limitations. A model that keeps requesting
  tools still fails at the bound; no fallback handoff is invented.
- Adds company scope to model events and records duplicate/step-limit events
  without raw arguments, source text or credentials.

Sixteen focused progress/scope tests passed in 0.791 seconds, including eight
new regressions. They cover metadata-heavy source responses, duplicate-page
blocking and advancing offsets, failed-result retry, evidence surviving context
trimming and reopening, explicit limited handoffs, persistent-loop exhaustion,
table-preview navigation, and graph restart after one company stalls. The last
test keeps the other company's completed branch, approved plan and spent budget,
then reaches the note gate without publishing. The final full suite passed **466 tests in 45.339 seconds**
(414 baseline plus 52 workflow/client tests). Dependency, syntax and whitespace
checks also passed. PATCH_MANIFEST.json records this result.

Configuration bytes, graph state schemas, approval hashes and database formats
remain unchanged. Successful-call history is local to each invocation; saved
evidence survives restart. New prompt hashes can cause fresh model calls, but
reservations are not reset. The real laptop continuation and final Docker/CI
remain pending; these synthetic regressions are not capstone quality scores.

## Live writer rejection and required-section correction

The v4 laptop suite passed 52 tests in 10.442 seconds. Its continuation reached
a revised-plan gate, but the proposed plan confused unsearched evidence with
unavailable reports. The user edited the plan and explicitly approved its new
hash e75857d84e0ddaeced4192e950ad94c7953b9334b35f9034d1c4ea60c723b68e.
Research then made seven section reads; quant obtained twelve annual facts and
completed six sandbox calculations. The writer produced an original draft and
two revisions. All three failed the deterministic output check before the model
critic ran; no note was prepared or published. margin_demo is terminal blocked.
Its journal retains 705,209 reserved tokens and 38 tool calls, with 138,173
provider-reported tokens. Earlier interrupted usage leaves total cost unknown.

Diagnostic 41.13 identifies unverified_number: the same title repeatedly
included FY2023-FY2025, while paragraphs included fiscal years, Item 1A and
number words. The draft also claimed absent financial data despite six saved
margin receipts and gave causal explanations backed only by calculation IDs.
Neither company's FY2025 Item 1A had been retrieved. These are real development
failures, not evidence of complete source coverage or a passed quality task.

The v5 correction preserves the numerical/markup/policy guards and revision
bound. It identifies every affected title/paragraph and offending numeric token
in one critic response, with examples of compliant labels. The writer receives
the saved margin receipts (including values, periods and input IDs) and bounded
original source excerpts. Raw quant/researcher summaries no longer stand in for
that evidence. The risk handoff remains explicitly untrusted; financial
calculations cannot establish causal explanations. The model critic and source
replay still check the final claims; the correction does not certify them.

Researcher and risk-diff nodes now require actual lookup attempts for requested
company/year/Item slots without saved samples before accepting a finish. A
premature finish gets bounded correction feedback; persistent refusal still
stops at the existing step limit. Actual empty/error results receive explicit
retrieval-gap limitations, never a code-generated assertion that disclosures
are absent. Saved samples still do not mean complete-section comparison.

Nine new focused regressions passed in 0.607 seconds. They reproduce skipped
latest-year risk evidence, premature-finish exhaustion, empty lookup handling,
risk-role coverage, complete six-pair financial context, bounded untrusted
excerpts, the supplied title/Item-label failures, correction within one revision,
and blocked-state diagnostics with no reset on continue. Existing source-change,
permission, exact-note approval, three-process resume and two-revision tests
remain part of the full suite. The full suite passed **475 tests in 47.697
seconds** (414 baseline plus 61 workflow/client tests). Dependency, syntax and
whitespace checks passed; PATCH_MANIFEST.json records the result.

State/database schemas and workflow_config.json remain unchanged. The original
blocked run is retained; the corrected development run uses margin_demo_v2 and
requires its own plan approval. New-run accounting does not erase the earlier
run's usage or failure. The new real-model run, Docker/CI and broader capstone
evaluations remain pending. No paid model or real SEC-corpus run was performed
in the authoring environment.


## V6: false-positive critic, source receipts and human draft correction

Logs 41.14–41.17 confirm 61 laptop software tests passed, plan edits/approvals,
required section lookups and a completed path through quant, risk, writer,
critic and proposal preparation. margin_demo_v2 is awaiting_note, unpublished.
The table reports 18 verified figures and no missing company/year calculations.
Its ledger holds 744,896 reserved tokens, 65 tool calls, 144,872 reported tokens
and USD 0.0655424 reported model cost (complete for this run).

The critic nevertheless returned supported=true for all five paragraphs. The
note contradicted the complete margin ledger, supplied causal business reasons
supported only by calculations, used Alphabet-only citations for a both-company
claim and declared no clear risk changes without complete-section coverage.
The supplied Microsoft excerpts do not support the acquisition-integration
claim. The Alphabet latest-period excerpt explicitly discusses AI reshaping
advertising; the earlier supplied excerpts differ. These samples support a
limited wording comparison, not a claim about when a risk first appeared.
This is a recorded critic false positive, not a successful quality evaluation.

The v6 changes:
- Add conservative deterministic checks for the observed absence, citation-scope,
  calculation-only causal and filing-wide risk-absence failure classes.
- Review each paragraph in isolation, all supplied sentences in order. Every
  supported sentence must name that paragraph's citations and provide a short
  source excerpt, or an explicit fact/calculation reference. Code rejects
  missing/duplicated sentence reviews, uncited sources and fabricated quotes.
- Bind a versioned review receipt to the exact draft hash. Old proposals cannot
  obtain publication approval through the CLI or publish under the new checks
  until the corrected draft is reviewed again. Model calls remain cached by
  their complete prompts; new checks do not reuse old broad approvals.
- Give the planner labelled company issues and the saved source-coverage index;
  clarify that quant collection is a later step and prepared years are not
  future periods. The writer receives explicit per-paragraph scope instructions.
- Render the financial table as contiguous Markdown rows, sorted by ticker/year,
  with coverage stated from the calculation ledger.

The human-edit replacement LSOutput/CorrectedNoteV2.json was checked against
all supplied source text and calculation values. It removes unsupported causes
and absence claims, limits risk statements to the sampled wording, and uses
company/year-appropriate citations. It is a development correction and must not
be treated as independent gold data or an autonomous agent success. The edit
must still pass live re-verification and the new critic, then obtain fresh
exact-note human approval. The old proposal will be rejected by the normal
edit route; its history and accounting remain intact.

Ten focused regressions passed in 0.822 seconds. The complete suite passed
485 tests in 48.675 seconds (414 baseline plus 71 workflow/client tests).
An initial HTTP fixture selected a table as prose evidence; its quote could
not pass the new checks. The scripted fixture now selects risk text passages.
The real four-service HTTP test, separate-process resume, exact approval,
source-change rejection and two-revision bounds passed in the final suite.
Dependency checks passed. No paid model or laptop Docker/CI run was performed
in the authoring environment; live continuation remains pending.

No state/database schema, dependency or workflow_config.json byte changes were
made. Existing reservations and limits are preserved. Source-quote matching
proves quotation provenance, not semantic entailment. The deterministic rules
cover named failure classes and are not a complete natural-language verifier.
The model's semantic decisions remain fallible; independent evaluation, held-out
cases, risk-change gold and the broader capstone requirements remain open.

## V7: critic response repairs and explicit development recovery

Logs 41.18/41.19 confirm 71 laptop tests passed and the corrected draft edit
was accepted. The previous proposal was closed by the edit route. The graph
is paused at critic with status edited_note and no pending review/publication.
The earlier 18/18 financial-table verification is not a completed new review.
The ledger records 897,486/900,000 reservations, 83/240 tool calls, 44 completed
model calls, 173,125 reported tokens and USD 0.0820072 complete reported cost.

Eight Alphabet critic responses violated the quote length limit: most repeated
a 393-character excerpt against a 300-character maximum, and some also used a
308-character excerpt. The last shortened excerpt still had 312 characters.
Generic schema feedback failed to identify these fields. The earlier financial
critic response also supplied nonempty calculation quotes and a supported
sentence with no references; those receipts cannot be accepted either.

V7 supplies bounded, sanitised field/constraint/observed-length feedback and
stops after three invalid schema/receipt responses per agent invocation (at
most two repair attempts, still inside the existing ReAct step bound). It
never truncates a model's quote, loosens its source checks or manufactures a
verdict. Receipt defects return to the critic; a valid unsupported verdict
still uses the original writer revision route. Correctable receipt failures
do not consume a writer revision. Diagnostics record only trusted paths,
constraints and generated instructions, without rejected content or secrets.

The optional extend-dev-budget command is an explicit human/operator action,
not a model tool. It requires a live terminal and exact confirmation of the
run and old/new limits. It permits only a resumable execution without a pending
human gate, never a terminal run. It changes only the token ceiling, atomically
journals the change, preserves used reservations/tools/configuration and marks
the run operator_extended_development. It makes no model/MCP calls, resumes
nothing and grants no publication approval. The original default remains
900,000; the command's absolute ceiling is 2,000,000. Tool/revision limits are
unchanged. Reopening still requires the original configuration hash.

For margin_demo_v2 the proposed development limit is 1,100,000, adding 200,000
reservation units, with 202,514 left after the recorded usage. The user must
explicitly confirm it, then separately continue. No extension has been applied
to the laptop by the authoring environment. This manually corrected, extended
development run is excluded from fixed-budget capstone comparisons; it cannot
be labelled autonomous success. Baseline/evaluation runs must start separately
under their predeclared identical budgets and retain failed attempts.

Eleven new regression tests cover precise length feedback, redacted error
fields, verbatim quote repair, financial receipt repair, unchanged unsupported
verdicts, bounded retries with cached restart, graph-level receipt correction,
atomic extension accounting, refused piped/wrong confirmations, terminal/gate
refusal and checkpoint recovery to a fresh unpublished human note gate.
The complete suite passed 496 tests in 53.361 seconds (414 baseline plus 82
workflow/client tests). Tests use scripted models and synthetic sources; live
critic completion and final Docker/CI remain pending. No dependency,
configuration or checkpoint schema changed.

## V8: select checked references instead of transcribing receipts

Logs 41.20/41.21 confirm 82 laptop tests passed and the operator explicitly
extended margin_demo_v2 to 1,100,000. The subsequent critic invocation used one
cached response and two new model calls, then stopped after three invalid
receipts. Current usage is 938,310 reserved units and 101 tool calls; 161,690
reservation units remain. No further budget extension is proposed or applied.

The financial critic repeatedly returned numeric descriptions in quote fields
that require an empty string, omitted references for the fiscal-period claim,
then selected underlying fact IDs outside the paragraph's permitted citations.
The limit worked, but precise feedback did not resolve the format mismatch.
The system prompt also retained its obsolete paragraph-level wording while
the detailed task required sentence-level receipts.

V8 builds a deterministic reference catalog from each paragraph's permitted
sources after normal source replay. Financial entries include values, input
values and actual reporting dates. Passage entries contain overlapping exact
source windows of at most 280 characters (inside the existing 300-character
bound); the visible source is limited to 3,500 characters and any truncation
is explicit. Short page furniture cannot produce a supporting quote.

The model must still judge every sentence and every clause, but returns only
selected reference IDs and a verdict/reason. Code resolves those selections
into the original receipt format. It does not choose references for the model,
truncate the model's prose, fabricate a quotation, repair an unsupported verdict
into approval or borrow a different paragraph's citations. Unknown, duplicate,
missing and cross-paragraph selections are rejected. Catalog IDs bind the
paragraph and source; altered catalogs/sources cannot resolve an old selection.
Original exact-quote checks and source replay still apply. The three-invalid-
response limit and two automatic writer revisions are unchanged.

The effective critic prompt retains the six required slots but replaces the
obsolete paragraph instruction with the sentence/catalog contract. It is code-
versioned as sentence-evidence-v2-catalog and appears in the full hashed prompt
used for response caching. Previous approvals cannot authorise publication
under this version. workflow_config.json bytes remain unchanged to preserve
the existing run's config binding, budget/history and checkpoint compatibility.
This development code change is not a frozen evaluation run.

Workflow failures now print state, budget/usage and the current attempt's
sanitised correction events before the final error. They make no extra model
calls and do not expose raw rejected content or credentials.

Nine new catalog regressions passed in 0.064 seconds; the full suite passed
505 tests in 49.543 seconds (414 baseline plus 91 workflow/client tests).
Existing tests now exercise the new contract, unsupported-claim revision,
source-change rejection, exact-note human approval, HTTP MCP services and
separate-process resume. The development-resume test also checks automatic
failure diagnostics and preserved usage. Dependency and source checks passed.

An additional offline check reconstructed the corrected draft's reference
inputs from the uploaded note table and source export: catalogs contain
6 / 26 / 29 references and 2,932 / 11,774 / 13,240 UTF-8 bytes respectively;
none of these supplied passages is truncated. Both observed malformed financial
responses are rejected by the new schema. This is an input/contract check, not
live source replay or a paid model run. Actual critic completion and final
Docker/CI remain pending. Source provenance does not prove semantic entailment;
independent quality evaluation and all broader open capstone work remain due.
