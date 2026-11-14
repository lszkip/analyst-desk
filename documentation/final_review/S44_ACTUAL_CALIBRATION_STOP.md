# Actual Sequence 44: calibration and accounting stop

The actual S44 continuation passed the local-budget classification transition and preserved all 24 S43 development outcomes, all 368 prior call records, the original approved scope, cumulative limits, original start time and deadline. F020 remains a failed local-budget attempt with its original cost.

The revised support judge then returned **19 responses**. The twentieth request, H20, failed with `model_http_error` at 17:41:17 UTC on 14 November 2026. Its actual cost was not returned. The accounting guard marked the shared ledger stopped/poisoned and rejected later workflow starts before any model call. The provider HTTP status and response body were not retained, so the archive cannot distinguish a rate limit, outage or account problem.

The top-level status `evaluations_recorded` means result documents were saved. It does **not** mean the experiments completed.

| Planned work | Recorded outcome | Research model calls | Completed workflows |
|---|---|---:|---:|
| Main held-out matrix: 840 jobs | 839 unrun; 1 immediate accounting error | 0 | 0 |
| Memory comparison: 12 jobs | 11 unrun; 1 immediate accounting error | 0 | 0 |
| Paired source-stage security: 120 jobs | All 120 unrun | 0 | 0 |
| Held-out semantic judgments: 420 maximum | All 420 unrun; judge ineligible | 0 | 0 |

The two immediate pre-model rejections took roughly eight milliseconds. They are accounting checks, not research latency. No held-out output, architecture effect, lesson effect, memory effect, defense rate or three-repeat research spread can be estimated from these records. Unrun jobs are retained, not treated as zero-cost successful runs.

## Partial revised judge agreement

The human set still contains all 24 original personally attested labels. Nineteen usable revised-judge responses yield three-class κ = **0.303665**. Fifteen definite binary pairs yield κ = **0.470588**. H20 failed; H21–H24 were not run. This incomplete revision is ineligible for automated semantic grading. The human-label count remains 24; the brief's 20+ hand-label count is unchanged.

The complete historical S40 judge comparison remains a separate result: three-class κ = −0.014925 over 24 pairs and binary κ = −0.064516 over 18 pairs. Do not merge responses from different judge revisions or imply the partial revised set represents all 24 outputs.

## Accounting and uncertainty

S44 reserved 20 new requests: 19 successful judge responses and one error. New **known** actual cost is USD 0.00167162, plus the failed request's unknown cost. The failed request's reservation ceiling was USD 0.00499201; that reservation is not a verified charge.

Cumulative ledger: **388 requests, USD 0.60246756 known actual cost, USD 3.73199355 reserved, one request with unknown actual cost, no in-flight requests, poisoned = true**. The known actual amount is a lower bound on billed cost, not a settled total. UI demonstration spending remains separate.

A billing reconciliation requires actual provider evidence and a separately reviewed recovery process. The saved evidence does not justify resetting the ledger, setting the failed cost to zero or retrying previous executions.

[Independent actual S44 audit](evidence/sequence44_actual/Actual44_Calibration_Stop_Audit.json) preserves source archive SHA-256 `279067191fe6322da98f6b9634ecfbcdd24887c87b8c480b8312bb91584901d2` and the exact counts. Original archives and prior development failures remain unchanged. Later hosted installation and controlled negative CI evidence are recorded separately; actual documentation installation remains unverified.
