# Frozen final case design and execution status

Evidence cut: 14 November 2026, 13:43 UTC. This is a design and provenance statement, not a report of executed held-out results.

The final manifest was frozen at `2026-11-14T11:29:13.814460+00:00`, before any agent run. It contains 60 cases across 15 companies and 45 company-years. The split is company-disjoint: related primary-company variants and all peer companies remain together, preventing the same company's related cases from crossing development and held-out partitions.

| Task family | Development | Held out | Total |
|---|---:|---:|---:|
| Annual financial series | 5 | 10 | 15 |
| Peer financial comparison | 5 | 10 | 15 |
| Risk comparison | 10 | 20 | 30 |
| Total | 20 | 40 | 60 |

Public case requests are separated from evaluator-private numeric truth and gold. Financial expected outcomes and source bindings are authored before execution. Thirty risk cases lack complete semantic expected outcomes. The separate brief requirement for human risk-change annotations on ten company-years remains unmet; the current worksheet does not invent those judgments. Dataset authorship is AI-assisted and is not represented as independent human verification. Hidden gold remains evaluator-side; the absence of agent file/code tools is an application boundary, not a claim of operating-system/container isolation.

The pilot configuration selects two development cases, one repeat and three architecture arms: fixed workflow, single agent and team. Its planned denominator is six case-arm attempts, with the shared critic accounted separately. This small pilot does not execute all 20 development cases or any of the 40 held-out cases. Evaluation plan approvals are simulated, publication is disabled, and all failures, budget stops and unrun attempts must remain visible. The separate final comparisons configure three repeats; no repeat is counted until it has actually executed.

S37's pilot attempt stopped at source preflight before paid approval. S39's offline verification leaves the original 60 cases, targets, authoring code and evaluator unchanged while binding only exact reviewed cache variants for the two development cases. S39 passed live backend preflight but stopped at price verification. The actual S40 pilot subsequently executed all six planned development case-arm attempts: three completed and three blocked. S40 also completed the separate 24-output model-model comparison, with poor agreement and no eligibility for automated semantic scoring. These results are recorded in `PILOT40_RESULTS.md`. Final held-out execution and three repeated comparisons remain pending. A frozen split alone does not satisfy those empirical requirements.

The later [human review](HUMAN_REVIEW_AND_PLAYBOOK.md) supplies 24 source-support labels and three lesson approvals/one rejection. These selected development-output labels do not label the thirty final risk-change cases. Historical Llama–human agreement is ineligible; the revised S44 judge stopped after 19 responses and remains ineligible. The earlier **Human review simulation — performed by Claude** remains AI-only and separate. Final matrix and held-out playbook execution have not yet been observed.

Sources: `evaluator_private/frozen_v1/freeze_manifest.json`, `cases.dev.jsonl`, `cases.heldout.jsonl`, the frozen pilot configuration, the S37 `Operator/Operator_Audit.json`, S39 `Independent_Package_Review.json`, and the actual S39 user archive's `Operator/Preflight_Report.json`, `Operator/Pilot_Plan.json` and `Operator/Operator_Audit.json`. This document intentionally omits numeric gold answers and held-out request contents.
