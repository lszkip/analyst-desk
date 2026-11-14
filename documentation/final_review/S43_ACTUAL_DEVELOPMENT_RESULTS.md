# Actual Sequence 43 development results

The user approved the preserved cumulative scope at 17:00:44 UTC on 14 November 2026. S43 then recorded **24 new development attempts across all 20 development cases**. It stopped with `blocked_development_coverage` before held-out execution. No final E1/E3/E4 comparison, three-repeat held-out result or security ablation is established by this archive.

| Phase | Attempts | Completed | Blocked | Errors | Median / p95 seconds | Reported USD |
|---|---:|---:|---:|---:|---:|---:|
| Strict C0/C1/C2 sanity on F002/F003, one repeat | 6 | 3 | 2 | 1 | 20.690 / 56.816 | 0.0600756 |
| C2 coverage on the other 18 development cases | 18 | 8 | 9 | 1 | 51.018 / 92.251 | 0.4463396 |
| Total new attempts | 24 | 11 | 11 | 2 | See phase scopes | 0.5064152 |

All blocked and failed outcomes remain in the counts. Latency is wall-clock per attempt under the shared evaluator load. Every attempted row has known model cost; unrun held-out jobs must not be included as successful or zero-cost runs. These phases each have one repeat; a three-repeat spread cannot be inferred.

## Corrected baseline sanity check

Unlike the historical S40 shared-critic pilot, S43 uses the corrected strict C0/C1 implementation. All three arms completed numeric F002. On risk F003, C0 ended `agent_step_limit`, while C1 and C2 blocked after bounded work. This is two development cases, not a sufficient architecture ranking or complete final E1.

| Strict arm | Numeric F002 | Risk F003 | Median / p95 seconds | Reported USD |
|---|---|---|---:|---:|
| C0 fixed workflow | Completed | agent_step_limit | 8.551 / 12.915 | 0.0018708 |
| C1 single worker | Completed | Blocked | 20.690 / 24.507 | 0.0187132 |
| C2 team | Completed | Blocked | 49.637 / 56.816 | 0.0394916 |

Across C2's 20 unique development cases, seven of ten numeric-only tasks completed and passed numeric/task checks; three blocked. Of ten risk tasks, two completed, seven blocked and one hit its workflow budget. All ten risk task-success labels remain unknown because semantic grading is not eligible; completion is not semantic correctness.

## Why execution stopped

C2 F020 hit its **local 650,000-token conservative reservation ceiling**, with 642,190 tokens already reserved before the next call. Its 24 received responses used 135,081 provider-reported tokens and USD 0.0603600. Reservations and billed tokens are different measures. The cumulative financial ledger was neither exhausted nor poisoned. The readiness classifier treated this local budget outcome as an unreviewed runtime/infrastructure failure and stopped the remaining queue.

The [S44 correction](S44_LOCAL_BUDGET_CORRECTION.md) is implemented and tested. It preserves the exact failure, original limits, original clock, frozen source data/answers and previous records. No retry, higher per-case ceiling or successful held-out result is claimed here. The later [actual S44 continuation](S44_ACTUAL_CALIBRATION_STOP.md) passed this transition but stopped during judge calibration before held-out research.

## Accounting and provenance

New work used 285 model calls and USD **0.50641520**. Together with the historical 83-call pilot/judge ledger, cumulative totals are **368 calls, USD 0.60079594 reported and USD 3.63497986 reserved**. All calls are settled, every actual cost is known, no request is in flight and the ledger has no stop code. UI demonstration spending remains separate.

- [Independent actual-run audit](evidence/sequence43_actual/Actual43_Development_Stop_Audit.json)
- [Sanity phase metrics and exact denominators](evidence/sequence43_actual/S43_Development_Sanity_Performance.json)
- [Coverage phase metrics and exact denominators](evidence/sequence43_actual/S43_Development_Coverage_Performance.json)
- [Original completion status](evidence/sequence43_actual/Completion_Status.json)
- [Original cumulative budget report](evidence/sequence43_actual/Completion_Budget_Report.json)

Archive SHA-256: `0bb92afee402ee19bad8c769f9a97f87e7183cad73b2bf28636dc0e31e0a0b36`. The original S40 and S43 records are preserved separately.
