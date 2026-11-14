# Recorded replan

The original saved `41.12 WorkflowReplanReviewOut.txt` contains the following contiguous event sequence at source lines 937–986. The extraction is exact and does not reconstruct a model response.

| Journal event | Observed content |
|---|---|
| 66 | `replan`, role `supervisor`, reason `company_evidence_limit_or_contradiction` |
| 67 | Supervisor `llm_start` with a new prompt hash and token reservation |
| 68 | Supervisor `llm_end` with response hash and recorded provider usage |
| 69 | `plan_created`, `replans=1`, plan hash `c0e27c429ae70b2e6176b596f383828ca0c68b220ad7fb022db7ba6f64a10202` |

Read the [exact event excerpt](evidence/planning/Replan_Exact_Events_66_69.txt), [original review log](evidence/planning/Replan_Original_Review_Log.txt) and [hash/line extraction record](evidence/planning/Replan_Extraction_Record.json). The complete original log also shows operator involvement and later blocked execution. This demonstrates a real development replan; it does not show autonomous held-out success. The original outcome and failed stages are retained.

## Additional actual S40 replan trace

The [S40 trace extraction](evidence/planning/Pilot40_Replan_Trace.json) records both team executions. In the risk case, journal sequence 3 creates the first plan, sequence 19 records `company_evidence_limit_or_contradiction`, and sequence 22 creates a different plan with `replans=1`. The original archive hash and member names are retained. These are real model replans within the development evaluator, which simulated plan approval; they are not live human approval or held-out success. The risk case later blocked, as the [pilot audit](evidence/pilot40/Pilot40_Audit.json) retains.
