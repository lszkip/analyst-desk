# Analyst Desk — final review

Analyst Desk builds cited comparisons of annual-filing operating margins and qualitative risks. A persistent LangGraph workflow gathers evidence through scoped MCP tools, computes financial values in code, reviews a cited draft and pauses for an operator to approve the exact note before local publication.

This submission includes working backend development evidence and newly implemented UI, memory and evaluation components. The final UI/Compose deployment and new experimental comparisons remain **pending actual target-machine results**. [The evaluation report](FINAL_EVALUATION_REPORT.md) separates those states.

## Start the prepared project

From the existing project folder, with the project's Python 3.12 environment active:

```bash
python start_analyst_desk.py
```

Prerequisites are a running Docker installation with Compose, the existing local `.env`, prepared SEC filing/passages/index data, local retrieval models and the repository's dependencies. The launcher checks these prerequisites; it does not download a corpus or create an empty substitute. Keep the model credential local. This is a one-command start of the **prepared project**, not evidence of a clean-machine installation.

The launcher starts the UI, API, four MCP services, retrieval storage and monitoring services. It preserves existing data and named volumes and writes a uniquely named startup results ZIP under `LSOutput`. It may stop the older named monitoring containers to hand over their ports. Starting services makes no model calls and starts no research.

Open **http://127.0.0.1:8080**. Retrieve the separate UI login token locally:

```bash
python start_analyst_desk.py --show-token
```

Paste the token into the interface; do not include it in screenshots or uploads. The model API key stays on the server. Grafana is at **http://127.0.0.1:3000**.

## First research run

1. Enter a question, one to four configured companies and fiscal years from 2023–2025. Review the displayed budget and explicitly accept model usage.
2. Review the generated plan. Enter your actual reviewer name and plain `APPROVE`, `EDIT` or `REJECT` for the chosen action.
3. Follow the execution trail. Pausing and continuing reuse saved state and the original reservation ledger; continuing does not approve a gate.
4. Read the complete note, citations and verification record. Editing requires revalidation. Approving the note publishes that exact content to the local research portal.

The saved dashboard shows one historical research snapshot, not the correctness of the new run. Read [deployment details](DEPLOYMENT.md) for checks, ports and troubleshooting. The current UI tests and loopback HTTP smoke test used fixtures; they did not execute a new paid research run or prove complete target deployment.

## Submission map

| Document | Purpose |
|---|---|
| [Architecture and walkthrough](ARCHITECTURE_AND_WALKTHROUGH.md) | Redrawn implementation graph, request flow and trust boundaries |
| [Final evaluation report](FINAL_EVALUATION_REPORT.md) | Observed metrics, failure analysis and explicitly pending comparisons |
| [Evidence register](FINAL_EVIDENCE_REGISTER.md) | Exact artifact references and requirement qualifications |
| [Memory policy](MEMORY_POLICY.md) | Storage, context, private watchlists and lesson governance |
| [Experiment log](EXPERIMENT_LOG.md) | Changes, results and limits by sequence |
| [Contribution log](CONTRIBUTION_LOG.md) | Operator work and disclosed AI assistance |
| [Demo recording script](DEMO_RECORDING_SCRIPT.md) | Reproducible recording plan and expected observations |

The review track titled **“Human review simulation — performed by Claude”** is AI-only. Its validated results cover 24 outputs (10 supported, 13 unsupported, one uncertain) and four lesson recommendations (three keep, one reject). It does not satisfy the brief's human-label or person-reviewed-lesson requirements. No human identity, approval or agreement statistic is inferred from an AI response.
