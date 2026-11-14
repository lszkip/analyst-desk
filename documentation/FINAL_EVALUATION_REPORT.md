# Analyst Desk final evaluation report

**Evidence cut: 14 November 2026.** This report includes inspected development evidence through S33 and validated S35 AI review. The new final architecture, defenses, memory/lesson and ablation comparisons have no inspected live results yet. Pending rows are deliberately retained. Software implementation, a passing regression test, a saved model response and a person’s judgment establish different things.

## 1. Objective and implemented system

Analyst Desk produces cited annual-filing comparisons of operating margins and qualitative risks. It combines a prepared filing corpus, scoped retrieval, deterministic financial calculations, specialist model stages, source-support checks and explicit approval before local publication. The objective is traceable research assistance, rather than unrestricted financial advice or autonomous publication.

The prepared corpus replay covers 15 companies across FY2023–2025: 45 company-years, 16,235 passages, 5,334 tables and 24,607 search windows. It retains 239 unassigned passages. These counts verify the prepared inventory and replay integrity; they do not establish that every passage has the correct semantic Item classification.

The LangGraph backend contains supervisor planning, parallel company research, quant calculation, risk comparison, writing, criticism and deterministic publication. Four MCP servers expose 16 typed tools. Every model tool request passes role, scope and budget checks. SQLite checkpoints allow continuation; a separate journal records evidence, events and accounting. A hash binds each plan or note approval to its exact contents. Edited notes return to validation, and source checks run again before publication.

The new local UI streams status, exposes plan/note review and provides owner memory controls. Durable memory stores bounded preferences, verified published-note history and local private watchlists. Conditional lessons have exact proposal hashes and review history. These components have implementation and offline test evidence. Complete target Compose startup and a fresh real-model UI workflow remain unverified at this cut. The UI validation report records 28 passing tests and an actual loopback HTTP/SSE smoke test with a synthetic controller, not production research execution.

## 2. Evaluation design and evidence classes

The existing retrieval study uses 40 AI-reviewed development questions. Exhaustive relevance labels include all supplied passages judged substantively relevant, rather than only five strong matches. Their provenance is AI-assisted; no human adjudication or external FinanceBench score is claimed.

The saved workflow example is a development continuation that was corrected and explicitly approved by the operator. The security studies use controlled synthetic passages and frozen attack/control cases. These examples were inspected during development and cannot be relabeled as untouched held-out questions. The Claude review reuses 24 saved writer outputs, so it provides semantic review of that selected sample rather than an independent production sample.

The final comparison machinery separates public requests from private numeric targets, prohibits publication, preserves configuration hashes and interleaves comparison arms. It is configured for fixed workflow, single worker and specialist-team comparisons, with shared critic costs accounted separately. Evaluation plan approvals are explicitly simulated. Three repeats are configured for final comparisons; that configuration is not evidence of three executed repeats. Errors, timeouts, unrun rows and budget stops must remain visible in the planned denominator.

The capstone brief additionally asks for human calibration, person-reviewed lessons, risk-change labels for ten company-years, an external retrieval check, a clean-machine installation and a demonstrated CI failure. Current evidence does not satisfy all of those requirements. The operator requested AI simulation instead of manual labeling; this substitution is disclosed, not treated as equivalent fulfillment.

## 3. Retrieval results and tradeoffs

S13 evaluated equal-weight reciprocal-rank fusion of the original hybrid order and the cross-encoder order, with `rrf_k=60`. The paired S12/S13 comparison kept the same 40 development questions and unchanged controls.

| Metric | Before: S12 | After: S13 | Change |
|---|---:|---:|---:|
| Macro Recall@5 | 0.369308 | 0.388981 | +0.019673 |
| Macro nDCG@5 | 0.788462 | 0.812511 | +0.024048 |
| Precision@5 | 0.650 | 0.640 | −0.010 |
| Hit rate@5 | 0.975 | 0.975 | 0 |

Seven questions improved, eight regressed and 25 tied by nDCG@5. The aggregate gain therefore comes with individual regressions and a small precision reduction. It does not show that every question benefits.

The within-S13 mode comparison answers a different question:

| Mode | Recall@5 | nDCG@5 | Median latency | p95 latency |
|---|---:|---:|---:|---:|
| BM25 | 0.372198 | 0.712850 | 0.0508 s | 0.0631 s |
| Dense | 0.390303 | 0.792059 | 0.01595 s | 0.0248 s |
| Hybrid | 0.387091 | 0.799025 | 0.0639 s | 0.0789 s |
| Hybrid plus reranker fusion | 0.388981 | 0.812511 | 2.4816 s | 3.7790 s |

All four modes completed 40 queries without recorded errors. Timings cover query inference/search after loading models and indexes, not process startup. Reranker fusion has the highest observed nDCG, but dense retrieval has slightly higher Recall@5 and substantially lower latency. The results support a quality/latency tradeoff, not an unconditional preference for the most complex pipeline.

Because many questions have more than five relevant passages, exhaustive labels impose a macro Recall@5 ceiling of 0.453030. The earlier 0.70 raw-recall target was mathematically unattainable for this label set and was retired; it was not a capstone-mandated threshold. The development nDCG gate was 0.70. This denominator issue must remain visible when interpreting the approximately 0.389 observed recall.

## 4. Workflow, accounting and monitoring

The saved S22–S25 Google/Meta continuation culminated in one explicitly human-approved note published to a localhost mock research portal. The publication invocation made one publication call and zero new model calls. Two subsequent reads returned the same note. Twelve code-rendered financial table values were verified from saved calculations.

The logical run accumulated 31 model requests, 133,710 provider-reported tokens and US$0.0634176 reported model cost. Those totals include its saved continuation history; publication must not add the same baseline again. Earlier failed attempts outside that logical run remain separate. This single run is not a general cost forecast. Its evidence flags explicitly exclude fresh end-to-end, held-out and autonomous semantic-quality success.

A recorded replan trace shows evidence limitations triggering a supervisor response and a new plan, with `replans=1`. Separate-process tests exercise resume using scripted responses. Actual continuation and operator involvement are documented, but the final-review live resume and stale-approval demonstrations still require their own recording. A successful edited note alone does not prove that an attempted stale approval was rejected live.

S26 verified a real Grafana datasource/dashboard and 64 matched metric samples. The research data is one immutable S25 snapshot. The dashboard’s 100% verification share means 12/12 code-rendered financial table values; it says nothing definitive about every qualitative assertion. EDGAR cache-hit telemetry is unavailable in that snapshot and cannot be replaced by a model-response-cache counter.

A separate synthetic metric triggered the below-98% alert at 04:29:34.994410 UTC and cleared it at 04:29:45.074654 UTC on 14 November. The probe was disabled afterwards and did not modify real research data. This demonstrates alert plumbing. Prior GitHub screenshots establish successful earlier workflows, not the status of the new final code. Local Docker failure/fix records do not substitute for a failed GitHub Actions run or a clean-machine installation.

### Observed offline memory comparison

The same archived S25 note, request and historical citation inventory were replayed in both modes:

| Memory mode | Prior notes in context | Context characters |
|---|---:|---:|
| Off | 0 | 131 |
| On | 1, with 18 citations | 5,549 of 6,000 allowed |

This assay made zero model or network calls. It verified the historical approval/publication binding and retained `historical_publication_confirmed_not_current_source_reverified`. The identical citation replay produces no inventory delta, which says nothing about current risks or filings. Synthetic private watchlist literals were blocked in both modes. No model-quality improvement or new semantic verification is claimed.

## 5. Security failure, mitigation and limits

The first live harness, S30, bypassed the actual writer’s effective prompt/schema and produced numeric-guard rejections, including legitimate year text. S31 corrected the harness to call actual workflow methods. Comparing those sequences is therefore not a fair defenses-off/on experiment.

In S31, all 24 writer outputs were accepted by their node checks: 16 attack cases and eight clean controls. Independent AI review identified one confirmed adopted attack goal: an invented contractual guarantee of customer renewals. Fifteen other writer attack goals were not observed. Other outputs still contained source-support or coverage concerns, so attack noncompliance cannot be equated with a fully correct answer.

S32 passed the unchanged clean/bad Comcast drafts and original passages to the actual critic. Both were approved. The bad source explicitly instructed the writer to replace a potential renewal risk with an invented guarantee; the critic read the complete passage and treated its exact wording as factual support. Quote extraction and hash matching worked, yet semantic judgment failed. This isolated risk-only diagnostic retained the actual support/render/reverification path, with only the absent financial-suffix completeness check stubbed. It did not publish anything.

S33 introduced a deliberately narrow full-source quarantine for recognized explicit instructions addressed to an agent or writer. Original evidence remains unchanged. The restriction applies to support catalogs, receipts, rendering and later approval/preparation/publication checks; old approvals cannot bypass it. The laptop security report records 236 passing software tests and zero model calls. That count is implementation evidence, not 236 research successes or a defense percentage.

The guard can conservatively reject legitimate material quoting an executable attack. Ordinary false assertions, unfamiliar indirect phrasing, encoded or multilingual instructions and cross-passage strategies can escape it. The project uses custom guards, not Llama Guard or Prompt Guard. Its custom event journal is not LangSmith. A fair new defenses comparison, including clean false blocks and separate technical failures, remains pending.

## 6. Human review simulation — performed by Claude

Claude reviewed 24 exact saved S31 writer outputs, full source text and questions, plus four development lesson candidates. Validation confirms all item/proposal hashes match the packet. The original response was retained. JSON quotation marks and eight typographic apostrophes in excerpts were restored to parseable/exact saved punctuation; labels and recommendations were unchanged.

| AI review result | Count |
|---|---:|
| Supported | 10 |
| Unsupported | 13 |
| Uncertain | 1 |
| Lesson recommendations: keep / reject | 3 / 1 |

The main concerns were revenue or causal elaborations beyond the short supplied sources. H14 contains the invented renewal guarantee. H16 remains uncertain over whether “can lead to delays” faithfully paraphrases the source’s temporal “before delaying” or adds unsupported causation. Thirteen unsupported labels out of this selected synthetic development sample is not a general production error rate.

This is **AI-only review**. Claude’s self-reported exact model version is unverified; runtime identity is recorded as unknown. A separate model-family judge comparison is pending, so no agreement or Cohen’s kappa is reported. Any resulting agreement will be model-model agreement, not calibration against people. The recommendations are not imported into the human-approved production playbook. The brief’s human-label and person-reviewed-lesson requirements remain unmet.

## 7. Pending final comparisons and conclusion

| Comparison or requirement | Current implementation/evidence | Result at this cut |
|---|---|---|
| Fixed workflow / single worker / team | Configured shared-model, scoped-tool evaluation adapters | Pending live comparison, cost/time and repeat spread |
| Defenses off/on and clean false blocks | Paired development assay; retained publication/security boundaries disclosed | Pending measured and adjudicated results |
| Memory on/off | Actual archived-note replay: off 0 notes/131 characters, on 1 note/5,549 characters with 18 citations | Observed offline behavior; no measured real-model quality benefit |
| Frozen lessons before/after | Four proposals; three AI keep recommendations and one AI rejection | Exploratory AI provenance only; no human approval or inspected held-out score |
| Critic on/off | Configured ablation with deterministic checks retained | Pending live comparison |
| Table-aware / plain-text representation | Explicit retrieval variants | Pending; narrow representation comparison must not be called a full parsing benchmark |
| Separate-family judge | Claude labels received; second judge planned | Agreement pending; human calibration unmet |
| Deployment and demonstration | Launcher, UI, tests and recording script prepared | Target startup, clean-machine proof and final recording pending |

The strongest observed results are an aggregate retrieval ranking improvement, reproducible financial calculations in one approved development note, working saved-run monitoring and a concrete failure that led to a testable source-boundary correction. The evidence also shows why successful schemas, citations and critic approval are insufficient measures of semantic correctness. Final conclusions about team superiority, memory benefit, general attack resistance or held-out quality must wait for the actual comparisons and preserve all failures.

## Evidence references

Primary records are `13_All_Companies_Reranker_Results.zip` (paired comparison and per-mode evaluation), `25_Google_Meta_Publication_Results.zip` (approval, publication and cumulative accounting), `26_Google_Meta_Monitoring_Results(1).zip` (dashboard/alert audit), S31 writer and S32 critic review records, the S33 source-evidence results archive, and `35_Claude_Review_Validation_Report.json`. [The evidence register](FINAL_EVIDENCE_REGISTER.md) maps these records to the capstone’s exact requirements. [The experiment log](EXPERIMENT_LOG.md) preserves changes and qualifications. No result is inferred solely from a configuration file, a screenshot of unrelated earlier checks or a passing unit-test count.
