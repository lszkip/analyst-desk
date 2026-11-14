# Clean installation and CI evidence

## What the installation job proves

The `Analyst Desk clean installation` workflow runs on a fresh GitHub-hosted Ubuntu 24.04 runner. It installs the pinned Python dependencies, downloads and verifies the pinned public ONNX model files, prepares invented filing fixtures, creates a real Qdrant index, and builds and starts all 12 production Compose services. No project data, model cache, workflow database, `.env`, or Docker volume is restored from an earlier run.

The runner already supplies Linux, Docker and Compose. This tests installation on a clean hosted development machine with those prerequisites, not installation of the operating system or Docker itself. Internet access is needed for Python packages, container images and the two public models. The source fixtures do not require SEC access or a paid provider account.

The workflow checks:

- All 12 expected containers are running; all 11 services with healthchecks are healthy. Qdrant is additionally exercised by actual index construction, verification and retrieval.
- Nine host HTTP health/readiness endpoints, including the retrieval service's real model/index readiness check.
- EDGAR financial lookup, all five KB MCP tools, and rejection of a Qdrant write with the read-only key.
- Sandbox arithmetic and confinement probes, plus refusal to publish a synthetic portal draft without approval. No note is approved or published.
- The research HTML page, rejection of unauthenticated API access, authenticated read access, and SSE status/idle events for an explicitly unexecuted saved fixture. Reading its status may record a `background_snapshot` event; it does not invoke the workflow or model.

The financial amounts and filing content are **invented test data**. Real local embedding and reranking models run, but no external LLM request or paid research is initiated. The model credential is a generated invalid placeholder. Monitoring replays the repository's existing validated public snapshot fixture solely to test service startup; its old counters and publication are not measurements from this CI run. A passing run does not establish financial accuracy, retrieval Recall@5, held-out agent performance, or new human review.

## One-command startup

For the existing prepared analyst installation, use the repository's normal startup command:

```bash
python start_analyst_desk.py
```

This requires the already prepared SEC cache, parsed corpus, passages, pinned local models, index manifest and existing local `.env`, as described in `documentation/DEPLOYMENT.md`. It preserves that installation and starts the complete stack without starting paid research.

The **CI-only** equivalent after dependency installation is:

```bash
python -m deployment.ci_clean_install
```

The GitHub workflow runs this automatically. Do not run it in the existing analyst installation: it deliberately refuses a local/self-hosted runner, an existing `.env` or data directory, or any earlier CI report. It creates only disposable state in the fresh checkout. Each run uses project `analyst-ci-<run-id>-<attempt>` and overrides both monitoring volume names. Cleanup checks its ownership marker and exact volume override before removing only that CI project and its volumes. It never uses `docker system prune`, deletes the analyst's host data, or selects the production Compose project. A cancelled hosted runner is discarded by GitHub; cancellation may prevent the final cleanup/report step from completing.

## Obtain actual successful installation evidence

1. Include the four new files listed below in the proposed commit. Open a pull request. The new workflow runs on pull requests, on pushes to `main`, and by manual dispatch once GitHub has registered it on the default branch.
2. Open **Actions → Analyst Desk clean installation** and inspect the run for the exact commit. Wait for **Fresh runner, complete stack, synthetic fixtures** to complete. A queued or running job is not evidence of success.
3. Download `Clean_Installation_<run-id>_<attempt>` from the run's artifacts. Keep its `Clean_Install_Report.json`, the run URL, commit SHA and conclusion with the submission evidence.
4. If it fails, keep the failed report and inspect its sanitized command outputs. Fix the identified installation problem, rerun, and keep both results. Do not mark clean installation complete before a genuine passing run.

Only the sanitized JSON report is uploaded. Credentials, downloaded source files, model binaries, databases and `.env` are excluded from the artifact.

## Deliberate failed CI run and quality threshold

Reuse `.github/workflows/evaluation.yml` (**Analyst Desk saved evaluation gate**). Do not introduce a broken commit or lower its thresholds to make a report green.

After that workflow is present on the default branch:

1. Open **Actions → Analyst Desk saved evaluation gate → Run workflow**.
2. Select the intended code branch and set `negative_control` to **true**. Leave the other inputs at their defaults.
3. Run it and save the resulting **failed** run URL, commit SHA, and the JSON in the job summary. The deliberate negative-control step should exit with code **20**. Check `status: controlled_negative_failure`, `negative_control: true` and `performance_claim: false` in the report.
4. Keep the successful clean-installation run alongside it. The explanation is: “The manual negative-control input deliberately supplied a failing gate condition; exit code 20 demonstrates that CI rejects a failure. This was not a measured agent-performance failure.”

The negative control is never enabled by ordinary pushes or pull requests. It is separate from actual performance gating. A normal push with no committed experiment files runs verifier unit tests and reports **not covered**, not a quality pass.

For a real performance gate, commit the actual public `Final_Experiment_Summary.json`, `Experiment_Manifest.json` and `Execution_Results.json` together at the configured path, preserving failures and incomplete executions. The workflow checks at least **40 completed executions per variant**, numeric success of at least **0.95 across all planned executions**, **0** observed policy violations and **0** errors/timeouts/interrupted executions by default. A small pilot does not meet that coverage threshold. Manual dispatch with `negative_control=false` requires actual evidence; missing evidence is a failure. Semantic claims still need separate review.

Until the user supplies GitHub results, the clean-installation run and deliberate failed-CI run are **pending**, even if local unit tests pass.

## Exact files for this change

```text
.github/workflows/clean-install.yml
deployment/ci_clean_install.py
deployment/test_ci_clean_install.py
documentation/CLEAN_INSTALL_EVIDENCE.md
```

From the current project folder, stage only these reviewed files:

```bash
git add -- .github/workflows/clean-install.yml deployment/ci_clean_install.py deployment/test_ci_clean_install.py documentation/CLEAN_INSTALL_EVIDENCE.md
git diff --cached --stat
```

The existing evaluation workflow and its verifier must also be included in the parent integration PR if they have not yet been committed. This deployment change does not rewrite them. Do not add private inputs, runtime data, credentials, or entire output folders.
