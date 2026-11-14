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

A first image build may take several minutes. A successful startup prints a uniquely named `34_Analyst_Desk_Startup_Results_<UTC>_<random>.zip`; upload it for verification. A failed or interrupted startup also writes a report. Reports contain service health and scrubbed command output, never `.env`, access tokens, checkpoint databases or model prompts.

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

Offline API and scripted workflow tests verify gate handling, exact-hash rejection, human-review capability separation, SSE output, pause/resume and authorization. They do not constitute a live-model quality result. The complete Compose startup, prepared data availability and end-to-end research behavior must be verified on the target machine. Do not claim clean-machine deployment until that separate run succeeds.
