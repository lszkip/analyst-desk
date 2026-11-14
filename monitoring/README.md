# Analyst Desk saved-run monitoring

This local stack displays the validated Google/Meta Sequence 25 export. It reads
one cumulative S22–S25 logical run once. It does not start research, call an AI
model, alter source evidence, approve a proposal, or publish a note.

The monitored result is a real-data, AI-assisted development note with human
approval. It is not an autonomous or held-out quality evaluation. The dashboard
scrapes are live; the underlying research evidence is a saved snapshot. Its
timestamp and age remain visible.

## Start and inspect

Sequence 26 installed and validated this local stack, its saved snapshot and
the synthetic alert demonstration. The current Sequence 28
`Run_Workflow_Checks.py` installs and runs the CI checks, then writes a unique
Sequence 28 result ZIP. It does not restart the stack or repeat the alert.

For the environment already configured by Sequence 26, start the existing stack
with Docker Desktop running:

```bash
docker compose --env-file /dev/null -f monitoring/compose.yaml --project-directory monitoring -p analyst-desk-monitoring up -d
```

Starting the historical dashboard requires the local runtime snapshot and
administrator secret prepared by Sequence 26. These are deliberately not
committed. A clean checkout can run CI using the committed sanitized fixture;
it does not need those runtime files or an active dashboard.

- Dashboard: <http://localhost:3000/d/analyst-desk-saved-runs>
- Sanitized event timeline: <http://localhost:8085/>
- Prometheus alerts: <http://localhost:9090/alerts>

Grafana opens as an anonymous **Viewer**. Login, sign-up and management writes
are disabled at the local gateway. Its unused administrator password is
generated locally; no default `admin/admin` credential is used. The password is
not part of the downloadable package or result ZIP.

To stop only this monitoring stack, preserving its named volumes:

```bash
docker compose --env-file /dev/null -f monitoring/compose.yaml --project-directory monitoring -p analyst-desk-monitoring down
```

The existing Analyst Desk services are a different Compose project and remain
untouched. Do not use volume pruning or add `-v` when preserving monitoring data.

## What the measures mean

| Measure | Scope |
| --- | --- |
| Published notes | The saved export records one exact-content publication. The exporter performs no live portal lookup. |
| Model requests, tokens and cost | The saved S22–S25 logical run: 31 requests, 133,710 reported tokens and USD 0.0634176. This is not total project spend; other attempts, such as S21, are outside the imported run. Monitoring adds zero AI calls or AI cost. |
| Reserved tokens | 638,420 conservative reservation units in the original budget. These are separate from actual reported tokens and must not be added to them. |
| Financial table verification | 12 verified values out of 12 code-rendered financial table values. This does not establish qualitative accuracy or verify every number in prose. Some critic sentence receipts remain incomplete. A missing or zero denominator is unavailable, never 100%. |
| Critic rejections | Completed critic-node rejection outcomes. Zero rejections is not a quality pass. |
| Corrections and stops | Historical schema/tool corrections and response-limit stops remain visible even though the final note was published. |
| Tool latency | Recorded successful broker calls only. EDGAR's median/p95 aggregate is computed from its individual successful durations, not from averaged percentiles. Failed-call durations and direct operator reads are not available. This is not SEC network latency. |
| Cache telemetry | Model-response cache events are recorded. An EDGAR cache-hit rate is **not measured** by the existing journal. |

Missing telemetry remains unavailable. Incomplete provider cost is not displayed
as zero. This stack does not provide complete live agent instrumentation,
LangSmith/Langfuse tracing, a red-team evaluation, a cloud deployment, or a
full-capstone completion claim.

## Alert demonstration

Prometheus evaluates the same condition for each separately labelled dataset:

```promql
(analyst_desk_verified_financial_share < 0.98)
and on (dataset, evidence_class, telemetry_kind)
(analyst_desk_financial_figures > 0)
```

The condition must remain true for five seconds. The Sequence 26 runner temporarily writes
a synthetic sample with 97 verified values out of 100, observes the
`AnalystDeskVerifiedFinancialShareLow` alert firing, then changes that synthetic
sample to 100/100 and observes resolution. It disables the probe in cleanup; an
expiry also prevents an interrupted probe from persisting indefinitely.

The test uses `dataset="synthetic_alert_probe"`, `evidence_class="synthetic"` and
`telemetry_kind="synthetic_test"`. Every real-data dashboard query explicitly
selects `dataset="google_meta_sequence25"`. The synthetic test never changes
the real note, its financial verification, or the recorded AI cost. It sends no
email, chat message or other external notification. An alert firing in the
Prometheus UI is the demonstrated delivery surface.

Promtool unit cases cover 97%, 98%, 100%, recovery, zero denominator and missing
evidence. The CI helper runs the actual Prometheus binary from the pinned image;
Python/static checks alone are not claimed as a live alert demonstration.

## Boundaries and storage

Exporter, Prometheus and Grafana run on an internal-only Docker network. A small
fixed-route gateway publishes only `127.0.0.1:8085`, `127.0.0.1:9090` and
`127.0.0.1:3000`. It allows GET/HEAD, plus Grafana's read-only datasource-query
POST endpoint. The gateway has no route configured to the research services.
Backend containers have no direct external network and receive no model,
portal, review or Qdrant credentials.

The exporter sees only sanitized `monitoring/data` files, mounted read-only.
No original checkpoints, source filings, `.env`, raw prompts, response caches,
note text or reviewer identity are mounted. Export fields and metric labels use
explicit allowlists. Prometheus and Grafana each have a separate named data
volume; roots are read-only with bounded temporary storage and log rotation.

`data/` and `secrets/` are ignored by Git and excluded from the image build. The
administrator secret has a private host directory and is bind-mounted as one
read-only file readable by Grafana's container user. Do not include it in
diagnostics or commits. The launcher records only selected image metadata,
never unrestricted container configuration or environment dumps.

## Reproducibility and source documentation

The Python base and existing nginx gateway image are pinned by digest. New
Prometheus and Grafana images use explicit release tags; the runner records
the actual image IDs/repository digests used on the laptop. Tags are not
represented as immutable digests.

- Prometheus LTS 3.13.5: <https://prometheus.io/download/>
- Prometheus alert semantics: <https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/>
- Promtool rule tests: <https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/>
- Grafana OSS 13.3.1: <https://grafana.com/grafana/download?edition=oss&platform=docker>
- Grafana provisioning: <https://grafana.com/docs/grafana/latest/administration/provisioning/>
- Grafana Docker configuration: <https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/>

The release/version documentation was checked on 14 November 2026.

## Validated local checkpoint

The reviewed Sequence 26 laptop result records the four healthy monitoring
containers, successful Prometheus scraping and Grafana provisioning/querying,
and the synthetic warning firing below 98% then resolving. The probe was disabled
afterward. The visible dashboard was also reviewed by the operator. This step
used zero model calls and did not open original research databases, alter the
research application, or publish another note.

This evidence validates monitoring of the saved Google/Meta snapshot and the
isolated alert demonstration. It does not establish live instrumentation of
every workflow, external alert delivery, or the broader capstone evaluations.

## Repeatable source checks and GitHub CI

The committed monitoring tests use
`monitoring/tests/fixtures/google_meta_saved_snapshot.json`, a sanitized
saved snapshot. They do not depend on local `LSOutput`, an uploaded result ZIP,
live services, credentials, or `monitoring/data`. Missing or invalid fixtures
fail the tests. Two source-archive importer tests are optional and explicitly
skipped on a clean checkout; the portable snapshot/HTTP/contract tests still run.

From the repository root, run:

```bash
python monitoring/run_ci_checks.py
```

This runs the standard-library Python tests followed by three Promtool checks:
configuration validation, alert-rule validation and alert-rule unit tests. Each
Promtool check uses a separate ephemeral container with no network, a read-only
root and only the three required configuration files mounted read-only. Its
only added temporary workspace is a 16 MiB `/tmp` tmpfs with
`rw,noexec,nosuid,nodev,mode=1777`; the container's `TMPDIR` is fixed to `/tmp`.
Promtool's rule-test storage needs this writable temporary directory. The
helper verifies and pins a local Docker endpoint before daemon operations. An
image download may be needed by Docker if the pinned release is not cached.
The running monitoring stack is not rebuilt, restarted or otherwise changed.

Without Docker, the portable test subset is:

```bash
python monitoring/run_ci_checks.py --offline
```

An optional `--report` argument accepts a new absolute JSON path in an existing
directory. Reports include discovered, executed and skipped test counts, bounded
command outcomes, and output fingerprints. Raw command output and environment
variables are not exported. Promtool failures include fixed diagnostic categories
and only exact, allowlisted public rule-test names when those are mentioned.
A wholly skipped or empty suite cannot pass; at
least 13 tests must execute and at most the two documented archive tests may be
skipped.

`.github/workflows/monitoring.yml` runs the full helper with Python 3.12 on pushes
and pull requests. Its checkout and Python actions use the same pinned revisions
as the existing tests workflow. No model key, additional Python dependency,
uploaded archive or running Compose deployment is required. Passing locally
does not establish a GitHub Actions result; that status is verified after push.

Commit the `monitoring/` source directory and
`.github/workflows/monitoring.yml`. The folder's `.gitignore` excludes generated
`data/`, `secrets/` and Python caches. Do not force-add those runtime files.
`Run_Workflow_Checks.py` is the operator installation/check helper; permanent CI
uses the committed `monitoring/run_ci_checks.py` directly.

### Sequence 28 correction

The Sequence 27 laptop run passed its 20 portable Python tests and both Promtool
configuration/rule checks, then failed the rule-test command. That invocation
kept the root filesystem read-only but omitted the writable `/tmp` used by the
successful Sequence 26 container configuration. Upstream Prometheus test storage
creates a temporary directory before opening its test database. The missing
temporary workspace is therefore the supported diagnosis; the earlier report
did not retain raw stderr, so its exact error text was not observed.

Sequence 28 adds only the bounded temporary workspace and safe failure
diagnostics to the helper, with focused regression tests. Alert rules,
dashboard, exporter, snapshot, network isolation and read-only source mounts
remain unchanged. A new local result must confirm the Docker rule tests pass;
GitHub Actions remains a separate result to verify after pushing the changes.

Prometheus test-storage implementation:
<https://github.com/prometheus/prometheus/blob/v0.316.0/util/teststorage/storage.go>
