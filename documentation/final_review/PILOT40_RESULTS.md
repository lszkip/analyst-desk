# Actual S40 development pilot and model agreement

The S40 user archive contains six executed research jobs: two development cases × three architectures × one repeat. It is a real-model pilot, not a software fixture or held-out evaluation. The configured task model is `openai/gpt-4.1-mini`, temperature zero, with the same configured task tools/budgets. All arms include a shared model critic/revision loop whose costs are retained. C0 fixes collection order but is not a wholly non-agentic pipeline; C1 has one collection/writing worker plus that shared validator. The strict-baseline distinction is documented in the protocol audit. Plan approvals are explicitly simulated for evaluation and publication is disabled.

The numeric-only case completed with private numeric checks passing in C0, C1 and C2. The risk-containing case was blocked in all three arms after the bounded revision process. Thus each arm has one completed and one blocked result. A blocked, unadjudicated risk output is not counted as correct or silently omitted; these counts do not establish 50% semantic accuracy.

| Arm | Completed / blocked | Model calls | Reported research cost, USD | Median elapsed seconds | p95 seconds | Local model-response cache hits |
|---|---:|---:|---:|---:|---:|---:|
| C0 fixed workflow | 1 / 1 | 15 | 0.0263292 | 24.493940 | 40.949406 | 10 |
| C1 single agent | 1 / 1 | 20 | 0.0263528 | 23.320791 | 29.032641 | 0 |
| C2 team | 1 / 1 | 24 | 0.0400556 | 39.207851 | 49.541218 | 0 |

There are only two timings per arm: median/p95 are descriptive values, not stable tail estimates. C2 costs more and takes longer here without a higher completion count. This does not show that a team is universally worse; the sample is too small, has one repeat and lacks independent semantic labels. The cache-hit difference is retained rather than hidden as an equal execution detail. Both C2 cases record a replan, but that alone does not establish a quality benefit.

Research used 59 model requests with reported actual cost **$0.0927376**. The separate 24-output Llama judge brought the final ledger to **83 requests and $0.09438074 actual cost**. Conservative non-refundable reservations were **$0.69963306**; reservations are not actual expenditure. No request lacks an actual cost, none remains in flight and the ledger is not poisoned. These are this run's reported values, not a future cost forecast. Earlier S37/S39 preflight stops remain separate zero-paid-call attempts.

## Human review simulation — performed by Claude

Claude previously reviewed the same 24 exact saved synthetic development writer outputs: 10 supported, 13 unsupported and one uncertain. S40's separate configured judge, `meta-llama/llama-3.3-70b-instruct`, labeled five supported and 19 unsupported. The response is complete, but agreement is poor:

| Agreement measure | Pairs | Observed agreement | Cohen's kappa |
|---|---:|---:|---:|
| Three-class, including uncertain | 24 | 0.541667 | 0.053763 |
| Binary, excluding one uncertain pair | 23 | 0.565217 | 0.049587 |

No judge pair is failed or unpaired. The three-class judge produced no uncertain labels. The declared provisional agreement threshold of 0.60 was not met. The report explicitly sets `eligible_for_automated_semantic_scoring=false`. These are model-model judgments, not truth or calibration against people. At the S40 run cut, actual human labels were zero and Claude's exact runtime version was unverified. The later validated human form is reported separately below; the original S40 model-model comparison is unchanged.

The same record preserves all four lesson recommendations: three AI keep recommendations and one AI rejection with a reason. Those historical AI recommendations are not human approval. A later form supplies actual decisions, but held-out before/after playbook performance is still not supplied by this pilot.

Evidence: [manifest](evidence/pilot40/Experiment_Manifest.json), [six run records, expected-value fields redacted](evidence/pilot40/Execution_Results_Redacted.json), [summary](evidence/pilot40/Experiment_Summary.json), [final budget](evidence/pilot40/Budget_Report.json) and [complete agreement/confusion matrices](evidence/pilot40/AI_Model_Agreement.json). No hidden gold answers are added by this summary. The original selected development outputs remain inspectable in their own saved records.

The reviewer-facing execution derivative removes eighteen `expected_margin_percent` evaluator fields. [The redaction record](evidence/pilot40/Redaction_Record.json) identifies every removed path and retains the exact original archive/member/content hash. Observed outputs, outcomes and costs remain; the original archive is unchanged. The [independent pilot audit](evidence/pilot40/Pilot40_Audit.json) and [real replan extraction](evidence/planning/Pilot40_Replan_Trace.json) retain their own source bindings.

## Later validated human review

The [human review and lesson record](HUMAN_REVIEW_AND_PLAYBOOK.md) now contains 24 personally attested labels: 12 supported, six unsupported and six uncertain, plus three lesson approvals and one rejection. Reusing the unchanged historical Llama outputs gives three-class kappa −0.014925 on 24 pairs (7/24 agreement) and binary kappa −0.064516 on 18 pairs (7/18). No new model calls were used. The strict 20-pair binary minimum and 0.60 agreement threshold are not met. The later revised S44 judge stopped after 19 responses and remains ineligible; no held-out research executed. Original choices are preserved.
