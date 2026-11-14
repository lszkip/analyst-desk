# Analyst Desk local deployment

The final stack runs the research UI, financial API, four MCP services, Qdrant, the monitoring exporter, Prometheus, Grafana and their fixed-route gateways. Starting the stack makes **no model calls**. Research starts only when an authenticated operator submits a question and accepts the displayed model-use budget.

## Start from the existing prepared project

Use the project's Python 3.12 environment and keep the existing `.env` and `data` directory in place. Docker Desktop must be running with WSL integration. The existing prepared SEC corpus, passage catalogue, retrieval manifest, local embedding/reranking models and Qdrant storage are prerequisites.

```bash
python start_analyst_desk.py
```

The command validates prerequisites, adds only missing local service credentials, creates a separate UI token, and runs:

```bash
docker compose -f compose.final.yaml up --detach --build --wait --wait-timeout 240
```

The existing `compose.yaml` stays unchanged. The final Compose project reuses `analyst-desk` backend services. If the older `analyst-desk-monitoring` project is running, the start command stops those named monitoring containers to release their local ports. It does **not** delete volumes. The final stack uses the same explicitly named Prometheus and Grafana volumes, preserving their stored history and configuration.

A first image build may take several minutes. A successful startup prints a uniquely named startup results ZIP containing its sequence, UTC timestamp and random suffix; use the exact path printed by the installed launcher and upload it for verification. A failed or interrupted startup also writes a report. Reports contain service health and scrubbed command output, never `.env`, access tokens, checkpoint databases or model prompts.

## Open and unlock the interface

Open **http://127.0.0.1:8080**. Retrieve the separate local UI token:

```bash
python start_analyst_desk.py --show-token
```

Paste that token into the unlock field. Do not upload it or include it in screenshots. It is distinct from the model API key and portal review credential. The browser retains the UI token only for the current tab session; **Lock session** removes it. API keys remain on the server.

The interface provides:

1. A research question, one to four configured companies, and explicit fiscal years from 2023–2025. The implemented workflow covers operating-margin comparisons and optional qualitative risk discussion.
2. Live status over authenticated server-sent events, including completed stages, scrubbed event metadata, token reservations and provider-reported cost when available.
3. A plan gate with approve, edit and reject actions. Every decision carries the exact current content hash. Editing produces another review gate.
4. An exact-note gate showing the complete cited note, verification record and current hash. **Approve & publish** uses the separate human-review credential and a short-lived capability retained only in the running server process. Neither the browser nor the model receives that capability.
5. Pause and continue controls. Pausing retains checkpoints and non-refundable budget reservations; continuing never approves a pending human gate.
6. Inspectable analyst preferences, local private watchlists, published-note history and coverage. Corrections and deletions require the current stored content hash. Lesson approval or rejection requires an explicit operator decision and reason. AI-simulated review does not activate a production lesson.

For human decisions, enter your reviewer name and type plain `APPROVE`, `EDIT` or `REJECT`. Approval/editing may resume paid model calls. Approving the final note publishes that exact checked content to the local research portal. The service never automatically approves a plan or note.

## Checks and troubleshooting

Read-only prerequisite validation:

```bash
python start_analyst_desk.py --check
```

Service status:

```bash
docker compose -f compose.final.yaml ps
```

Stop the final stack while preserving containers' persistent data and all volumes:

```bash
docker compose -f compose.final.yaml stop
```

The research UI is on port 8080; Grafana is on port 3000 and Prometheus on 9090. All published ports bind to `127.0.0.1`. The existing financial API remains on 8000, EDGAR on 8001, filings retrieval on 8002, the computation sandbox on 8003 and the research portal on 8004. The monitoring dashboard's saved-run panels retain their documented scope; a healthy dashboard is not evidence that every future research answer is correct.

If startup reports missing prepared data, finish the repository's existing SEC/download, passage/index and model preparation steps before retrying. Startup does not fabricate an empty index or silently redownload/rebuild the corpus. If a port is occupied by an unrelated process, stop that process deliberately; startup does not kill arbitrary processes. If an existing credential is invalid, startup stops instead of replacing it.

The browser API accepts only its separate bearer token, rejects cross-origin operator actions and untrusted hostnames, and has no arbitrary command, file-path, endpoint or tool execution route. Source text is rendered as text, never executable HTML. The app shares only the retrieval gateway's network namespace so the production MCP clients keep their existing loopback-only endpoint restrictions; credentials remain scoped to their individual containers.

## Persistence and validation scope

UI runs live under `data/workflow_final`: immutable request/configuration files, per-run lesson manifests, journal/checkpoint databases and owner-scoped memory stores. Existing research folders and earlier result archives are not migrated or overwritten. A reviewed lesson change can invalidate an old frozen playbook; use a fresh run when the reviewed playbook changes rather than silently changing an in-progress run's context.

Offline API and scripted workflow tests verify gate handling, exact-hash rejection, human-review capability separation, SSE output, pause/resume and authorization. They do not constitute a live-model quality result. S37 has now verified complete Compose startup on the existing prepared target laptop. This does not verify a completed fresh live-model research workflow, a clean-machine install or current GitHub CI. Those claims still require their own execution records.

## Observed prepared-laptop startup: 14 November 2026

The S37 uploaded startup report records successful execution of `docker compose ... up --detach --build --wait --wait-timeout 240` and nine healthy local endpoints. Startup ran from 13:01:48 to 13:02:18 UTC with Docker server version 29.9.3. The research UI, financial API, EDGAR, filings KB, sandbox, portal, monitoring exporter, Prometheus and Grafana all returned HTTP 200. No model calls, research submission or volume deletion occurred during startup. The user's installed offline suites also passed 400 checks (236 legacy security and 164 final integration).

This is a successful startup on the user's existing prepared laptop. It does not establish a clean-machine installation, browser-driven end-to-end task success or final-code GitHub Actions status. Earlier green GitHub screenshots belong to their earlier commits.

The separate S37 development-pilot preflight stopped on an exact cache-byte mismatch before paid approval. S39 reconciles only four independently reviewed public SEC cache copies against the unchanged frozen pilot. It preserves original data and strict checks; it does not download replacement filings or silently revise expected answers. The user's S39 report now verifies the exact source binding and live backend preflight: ten financial-fact checks, seven scoped section/search checks and the sandbox check passed. Its 414 offline checks also passed. Paid research did not start because the later current-price gate returned `price_verification_required`. Continue to use the single installed launcher and its printed result path; do not remove earlier output folders or reset the cumulative pilot state to bypass a stop.

## Historical price stop: S39, superseded by the S40 result below

The S39 user report distinguishes ready local tools from permission to start paid model work. Live backend/source checks passed; current exact-model prices could not be verified, so the operator stopped before paid calls. At that S39 cut, the six planned pilot case-arm attempts had no research outcomes, and no held-out execution or publication occurred. S40 subsequently added compatibility handling for an omitted `request` field in the public model catalogue and completed the inspected run below. Do not bypass the price gate or infer model cost from the successful Docker/MCP checks.

## Actual S40 follow-up

The later S40 user archive records successful price compatibility preflight followed by six real development research attempts and the separate AI judge. [Pilot results](PILOT40_RESULTS.md) contain the actual completion/block outcomes and accounting. No publication or held-out evaluation occurred. This advances the earlier S39 price stop but does not establish a clean-machine installation, final live UI demo or new GitHub CI result.

## Prepared clean-install proof, not an executed installation

The completion work adds a fresh GitHub-runner installation workflow and a deliberate negative quality-gate control. [Local validation](evidence/deployment_preparation/Deployment_Validation.json) records ten passing unit tests and valid workflow YAML. An [independent AI review](evidence/deployment_preparation/Independent_Deployment_Audit.json) checks scoped synthetic credentials, disposable volumes and cleanup boundaries. The [local negative control](evidence/deployment_preparation/Local_Negative_Control_Report.json) deliberately exits 20 with `performance_claim=false`. These are preparation and local checks. Docker was unavailable in that preparation environment. The later actual fresh-runner installation and controlled hosted CI failure are now linked below.

### Verified fresh-runner installation

[Actual GitHub clean installation](ACTUAL_CLEAN_INSTALLATION.md) now passed at commit `3b1b96a72ca78dd3cd9296517fbd27fb9e1a6cbd`: 12 containers running, nine HTTP 200 endpoints, protocol/UI/auth/SSE checks and all 11 commands passed; unique-project cleanup completed. Synthetic fixtures, zero LLM calls and no executed research workflow. L16 clean installation is evidenced. The controlled failed CI demonstration is now observed with exit code 20 and no performance claim; documentation-package installation remains unverified.

### Hosted failed-CI evidence received

[Controlled negative CI job](ACTUAL_CONTROLLED_NEGATIVE_CI.md) failed with exit 20, `controlled_negative_failure`, thresholds null and `performance_claim=false`; verifier/availability steps passed, actual numeric gate skipped. This supplies the failed-CI demonstration and explanation, not a measured research regression. Its exact commit is not visible. Both L16 evidence items are now covered in the bounded fresh-install/controlled-failure scopes.
