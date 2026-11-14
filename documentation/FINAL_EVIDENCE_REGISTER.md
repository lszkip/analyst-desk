# Analyst Desk: final evidence register

This register distinguishes implemented features, scripted regression tests, real-model development runs, human judgments and held-out experiments. They are different evidence. Test counts do not measure research quality, and a correct citation hash does not prove that a passage supports a claim.

The authority is the supplied **Agentic AI Capstone** brief, physical pages 3, 5, 6, 7 and 8. The Analyst Desk pages are printed 15–16. The page-3 final-review column was checked against the rendered original. This version records evidence through Sequence 33, the preserved Sequence 34 human-review packet and validated Sequence 35 AI review. New final experiments must be added only after their actual results have been inspected.

**Human review simulation — performed by Claude:** the operator cannot complete manual labeling and requested a transparently disclosed AI simulation instead. Sequence 35 review is received and validated: 24 saved outputs were labeled 10 supported, 13 unsupported and one uncertain; four lesson candidates received three keep and one reject recommendations. All item/proposal hashes match. These are AI-only judgments on a selected synthetic development sample, not a general production error rate. Exact runtime model identity is unknown. The original human-review packet is preserved unchanged. Human-label calibration and person-reviewed lesson requirements remain unmet. A separate-family judge comparison is pending; any agreement will be model-model agreement, not agreement with human judgments.

## Minimum-bar evidence

| Requirement | Evidence required at final review | Evidence already available | Remaining qualification or action |
|---|---|---|---|
| L4–L6 Retrieval | Recall@5 on 30+ labelled queries, before and after one change | Sequence 13 contains 40 labelled development queries, four retrieval modes and a paired before/after ranking comparison. | Labels are AI-reviewed development labels, not human-adjudicated or held out. Preserve this disclosure. |
| L7 Tools and MCP | Tool list with schemas and one test per tool | Four FastMCP servers expose 16 typed tools. Contract tests cover schemas, read/write annotations, success and structured failures. Laptop HTTP checks exercised the KB, sandbox and portal. | Include the actual schema catalog and per-tool test mapping, rather than only a suite test count. |
| L8 Orchestration | Graph diagram and live resume demonstration | [Architecture and walkthrough](ARCHITECTURE_AND_WALKTHROUGH.md), persistent SQLite checkpoints, a separate-process regression, and saved S22–S25 continuation/publication records. | The separate-process regression uses scripted responses. A final live demonstration or recording must show the actual stop/resume interaction. |
| L9 Memory | One-page policy and memory-on/off comparison | [Memory policy](MEMORY_POLICY.md), owner-scoped durable store, bounded prior-note context, private watchlists and deterministic on/off behavior tests are implemented. | The offline comparison measures context/follow-up behavior without model calls. A real-model quality comparison remains pending. |
| L10 Planning | Trace showing a replan | `41.12 WorkflowReplanReviewOut.txt`, journal events 66–69: evidence contradiction, supervisor replan, new plan hash and `replans=1`. | This was development recovery with operator involvement, not held-out autonomous success. |
| L11 Multi-agent | Single-agent/team comparison | Supervisor, researcher, quant, risk-diff, writer, critic and deterministic publisher roles; typed handoffs and parallel company branches. | Same-model, same-tools and same-budget quality/cost/time comparison needs actual final results. |
| L12 Improvement | Held-out before/after playbook results; playbook; one rejected lesson and reason | Four development-derived candidates received three AI keep recommendations and one AI reject recommendation, each with a reason. | Person-reviewed lesson approval and a human rejection/reason remain unmet. AI recommendations must remain separate from a human-approved playbook. |
| L13 Evaluation and monitoring | Evaluation report, judge agreement and dashboard | Saved-run Grafana/Prometheus verification; validated Claude AI labels for the same 24 saved writer outputs: 10 supported, 13 unsupported, one uncertain. | Manual labels are not being supplied. Human judge calibration remains unmet. Any subsequent model-model agreement and final research results must be reported under their actual evidence class. |
| L14 Human control | Live demonstration of an edit invalidating earlier approval | Exact-payload approval, edit invalidation and forged/stale capability tests; saved note edits and explicit human publication. | A note edit followed by a new approval is not itself proof that a stale approval was attempted and rejected live. Include that specific demonstration. |
| L15 Security | Defenses off/on attack success and clean false blocks | Real-model S31/S32 failure evidence; S33 deterministic source-instruction quarantine; separate sandbox and permission tests. | S30/S31 changed harness execution, so their difference is not a fair defenses-off/on experiment. Count technical failures separately from blocked attacks. |
| L16 Deployment | Clean-machine installation and one failed CI run explained | Laptop Docker/MCP/monitoring operation, local monitoring CI checks and prior green GitHub screenshots. | An existing laptop is not a clean-machine installation. A failed local Docker build or helper is not a failed GitHub Actions run. New streaming UI/Compose/CI claims need the corresponding execution record. |

The machine-readable audit in `review/requirements_audit.json` preserves the exact page-3 final-review wording, references and unresolved evidence for every row.

## Reusable retrieval results

Source: `13_All_Companies_Reranker_Results.zip`, especially `All_Companies_Retrieval_Evaluation.json` and `Baseline12_vs_Experiment13_Comparison.json`.

The same 40 development queries were evaluated before and after combining the original hybrid order and cross-encoder order with equal-weight reciprocal-rank fusion (`rrf_k=60`). Controls were unchanged. The paired change has the following observed results:

| Metric | Before, Sequence 12 | After, Sequence 13 | Change |
|---|---:|---:|---:|
| Macro Recall@5 | 0.369308 | 0.388981 | +0.019673 |
| Macro nDCG@5 | 0.788462 | 0.812511 | +0.024048 |
| Precision@5 | 0.650 | 0.640 | −0.010 |
| Hit rate@5 | 0.975 | 0.975 | 0 |

Seven queries improved, eight regressed and 25 tied by nDCG@5. This is an aggregate improvement with individual regressions, not a universal gain.

The Sequence 13 within-run comparison is different from the paired Sequence 12/13 change:

| Retrieval mode | Recall@5 | nDCG@5 | Median seconds | p95 seconds | Errors |
|---|---:|---:|---:|---:|---:|
| BM25 | 0.372198 | 0.712850 | 0.0508 | 0.0631 | 0 |
| Dense | 0.390303 | 0.792059 | 0.01595 | 0.0248 | 0 |
| Hybrid | 0.387091 | 0.799025 | 0.0639 | 0.0789 | 0 |
| Hybrid plus reranker fusion | 0.388981 | 0.812511 | 2.4816 | 3.7790 | 0 |

Recall uses all relevant supplied passages in its denominator. Because many questions have more than five relevant passages, the macro Recall@5 ceiling for these exhaustive labels is **0.453030**, not 1.0. The former 0.70 raw-recall target was retired as unattainable for this label set; it was not an IIT-mandated threshold. The development nDCG gate was 0.70. Latencies include query inference/search after loading models and indexes, not process startup.

The corpus replay record `34.2.4 IndexVerifyOut.txt` verifies 15 companies × FY2023–2025: 45 company-years, 16,235 passages, 5,334 tables and 24,607 search windows. It retains 239 unassigned passages. These counts establish prepared coverage and replay integrity, not complete semantic Item assignment.

## Tool contracts and tests

The audit enumerated the current servers in process and exported their actual input schemas, output schemas and annotations to `review/evidence/Tool_Schemas_Actual_Source.json`. It did not invoke tools or make external calls.

| Server | Tools | Representative contract/operation tests |
|---|---|---|
| EDGAR | `list_companies`, `list_annual_filings`, `get_annual_financial_fact`, `get_section` | `test_edgar_mcp.py`: company catalog, filing bounds, fact provenance, structured failures; `test_filings_kb_mcp.py`: paginated EDGAR section and source-change rejection. |
| Filings KB | `plan_search`, `search_filings`, `get_passage`, `get_table_rows`, `get_coverage` | `test_filings_kb_mcp.py`: plan without search, source-linked results, complete passage, exact table cells, coverage and HTTP transport. |
| Python sandbox | `execute_python`, `get_sandbox_policy` | `test_research_tools_mcp.py`: authenticated sandbox HTTP execution and policy; `test_python_sandbox.py`: calculations, bounds and kernel network/file/process probes. |
| Research portal | `list_notes`, `get_note`, `get_proposal`, `prepare_note`, `publish_note` | `test_research_tools_mcp.py`: prepare/review/publish/read, role denials and schemas; `test_research_portal.py`: exact-content approval, edit revocation, expiry and idempotency. |

The portal's two write tools are explicitly annotated. Human approval is not an agent-visible MCP tool. The laptop `40.2 ResearchToolsOut - Test live MCP connections.txt` records blocked unapproved publication, blocked read-only publication, blocked agent approval and no publication during that check. Its sandbox probe records network, file-open and process-creation denial. These are concrete boundary checks; they do not quantify natural-language attack resistance.

## Saved workflow and publication evidence

The implementation graph contains a plan/edit interrupt, parallel company researchers, an evidence-triggered replan, quant calculations, a risk comparison, writer/critic revisions, proposal preparation, exact-note approval and source revalidation before publication. The configured automatic writer-revision bound remains two.

`test_workflow_clients.py::ProcessResumeTests.test_three_separate_processes_resume_same_checkpoint_and_publish_once` exercises checkpoint reuse across three processes with synthetic fixtures. The saved real-model continuation from S22 through S25 demonstrates recovery and publication of the same development work without resetting its cumulative accounting.

The S25 publication archive records:

- one exact saved Google/Meta note published to the **localhost mock research portal**;
- one publication call in that invocation, after explicit terminal approval;
- two identical subsequent reads of the published note;
- 12 code-rendered financial table values verified from the saved calculations;
- 31 cumulative model requests, 133,710 provider-reported tokens and **US$0.0634176** reported model cost for that logical run;
- zero new model calls during publication itself.

The note was AI-assisted and corrected during development. Some critic sentence selections had incomplete coverage even though the full cited passages supported the corrected text. Its explicit recorded flags are `fresh_end_to_end_evaluation=false`, `held_out_evaluation=false` and `autonomous_semantic_quality_pass=false`. Keep those limits with any screenshot or cost figure. Earlier failed attempts outside this logical run are retained separately and must not be silently treated as free or included twice.

## Monitoring and alert evidence

`26_Google_Meta_Monitoring_Results(1).zip::26_Monitoring_Audit.json` records a real provisioned Grafana dashboard, healthy datasource, verified query value and 64 matched static metric samples. Prometheus/Grafana are live services, but their research metrics come from one immutable S25 saved-run snapshot.

The alert expression checks verified financial share below 0.98 with a positive figure count. A separate synthetic probe caused an alert to fire at `2026-11-14T04:29:34.994410+00:00` and clear at `2026-11-14T04:29:45.074654+00:00`. The probe was disabled afterwards, and real research data was not modified. This is evidence of working alert plumbing, not evidence that a real research note became incorrect.

The dashboard's 100% figure share means **12/12 code-rendered financial table values**, not every number in prose or every qualitative assertion. EDGAR cache-hit telemetry is explicitly unavailable in that snapshot. A model-response-cache counter is a different metric and must not be substituted for it.

S28's local report passed Python and Prometheus `promtool` checks. That report expressly does not determine GitHub Actions status. Prior user-supplied GitHub screenshots show successful Docker/MCP/test/monitoring/security workflows after their respective merges; they do not establish the result of the final S34 code.

## Observed deterministic memory comparison

`review/Memory_On_Off_Report.json` replays the actual archived S25 note with the same request and historical source inventory in both modes. Memory off supplies zero prior notes and 131 context characters. Memory on supplies one prior note, its 18 citations and 5,549 characters within the 6,000-character bound, without truncation. Historical note/approval/publication hashes are checked. The retained status is `historical_publication_confirmed_not_current_source_reverified`; the old note is not upgraded to modern critic verification.

The replay makes zero model/network calls. Identical historical citation lists create no source-inventory delta, which does not establish current filing or risk stability. A synthetic private watchlist literal is blocked in both modes without dispatch. These are measured offline behavior and privacy checks, not evidence of improved model research quality.

## Security findings that must remain visible

S31 corrected the evaluation harness to call the actual writer and researcher methods. Its saved Comcast output adopted a source instruction that replaced a potential renewal risk with an invented contractual guarantee. The original source and the accepted writer output were then carried unchanged into S32.

S32 called the actual production critic, preserving the source guards, rendering, source revalidation, strict reference schema and expanded receipts. Only the absent separate financial-suffix completeness check was stubbed for this risk-only diagnostic. The critic fetched the original source and approved both invented-guarantee sentences. Every selected quote was exact, and the overlapping windows covered the entire source. The failure was semantic: it treated the source's instruction as factual support. The node returned `preparing_note`; the isolated experiment did not publish anything.

S33 adds a deterministic, deliberately narrow quarantine for recognized explicit source-to-agent instructions. It scans complete original passage text and table strings, preserves originals, blocks support catalogs/receipts/rendering, and rechecks source eligibility at approval/preparation/publication. Saved old approvals are invalidated. The original failed approval and a training-quotation wrapper are regression cases.

This is not a general truth or injection classifier. Ordinary false assertions, indirect or unfamiliar syntax, encoded/multilingual content and cross-passage instructions can escape the narrow detector. A quoted executable attack in legitimate training material can be conservatively blocked, along with nearby useful facts. Report that false-positive tradeoff. The S33 laptop security report's 236 passing software tests are implementation evidence, not 236 research successes or an attack-defense percentage.

## Final evaluation and submission checks

The brief's additional requirements must not disappear behind the minimum-bar table:

| Required evidence | What counts | What does not count |
|---|---|---|
| 60+ research cases, about 20 development and at least 40 held out | Expected outcomes authored before execution, related variants grouped, hidden gold and recorded authorship. | The already inspected S30/S31 attack/control corpus or ordinary unit fixtures relabelled as a research benchmark. |
| Different-family judge calibrated on 20+ human labels | Actual human-authored labels, exact content bindings and reported Cohen's kappa with denominators/uncertainties. | AI review relabelled as human review, an empty packet, or a model-identity guess. |
| Human risk-change labels for 10 company-years | Human judgments about changes across specified source periods. | The 24 source-support calibration labels; these answer a different question. |
| FinanceBench external retrieval check | Licensed external sample, untouched evaluation split and reported retrieval outcomes. | The internal 40-query development comparison. |
| E1 | C0 fixed workflow, C1 one agent and C2 team under the same model/tools/budget; quality, cost and time. | Different budgets, omitted failures, or architecture diagrams alone. |
| E2 | Defenses off/on on attacks and clean controls, with clear attack success and false-block denominators. | Harness corrections presented as an ablation; provider errors counted as defenses. |
| E3 | Human-reviewed conditional lessons, frozen before held-out execution; before/after results and lesson-generation costs. | Suggested lessons automatically accepted or a synthetic rejected-lesson record. |
| E4 | Critic on/off and table-aware parsing versus plain-text chunks. | Merely turning off citations or changing presentation without the specified ablation. |
| Three repeated final comparisons | All three actual runs, failed/time-out attempts and observed spread. | Three deterministic copies of one result. |
| Deployment/demo | One-command startup, clean-machine evidence, a failed CI run explained, streaming UI and recorded demonstrations. | Existing-laptop success described as a clean install; local helper failure described as GitHub failure. |

The preserved human-review HTML is a preparation artifact only. Its packet hash is `e7b5be45054d73e32da3b45069dc4b12f09eb83954e906ef01d8ce68c129c2c1`. It contains 24 saved development outputs and four unapproved lesson candidates; no label or lesson decision is prefilled. The operator declined manual completion. Do not populate this human-attestation form with AI answers.

The Sequence 35 Claude packet has hash `9ad0b0a9658c35fb1679c1602cb869dc2dfffe1e31e43d76a7f4ecd3a0801d0d`. Its 24 item objects, original sources/questions/outputs and four proposal objects exactly match the preserved human packet. Only review-purpose metadata changes. The prompt requires `reviewer_type="ai"`, `human_reviewed=false`, an empty human-reviewer field and `satisfies_human_review_requirement=false`. It requests labels and lesson **recommendations**, not human approvals. The validated response and original response are retained. Curly JSON quotes and eight typographic apostrophes in excerpts were repaired with a logged exact-punctuation restoration; labels and recommendations were unchanged. Review found unsupported revenue/causal elaborations and the invented guarantee in H14; H16 remains uncertain about temporal versus causal paraphrasing. `35_Claude_Review_Validation_Report.json` records the audit, including unknown verified model identity and pending cross-model agreement. Any exploratory experiment with AI-reviewed lessons must disclose that provenance and freeze the recommendations before held-out execution.

Repository drafts now include the one-command README, redrawn architecture with numbered walkthrough, memory policy, evaluation report, experiment log, contribution log and demo recording script. Final assembly must include the threat model, rendered report/slides and an actual backup recording; a recording script is not the recording. State actual AI assistance and individual contributions. Do not include credentials or private watchlists in the submission.
